from flask import Flask, request, jsonify
from flask_cors import CORS
import pyautogui
import pyperclip
import time
import threading
import requests
import json
import os
import pytesseract
import re
import pygetwindow as gw
from PIL import Image
import cv2
import queue
import numpy as np

app = Flask(__name__)
CORS(app)
ACTIONS_FILE = "actions_create_room.json"
EXTEND_ACTIONS_FILE = "extend_actions.json"
BACKEND_API = "http://localhost:5000"  # địa chỉ backend
ROOMS = {}  # userId -> {'room_code':..., 'end_time':...}

# --- ĐƯỜNG DẪN TESSERACT (sửa theo máy bạn) ---
pytesseract.pytesseract.tesseract_cmd = r"D:\project12m\OslinkSymtem\tessat\tesseract.exe"

request_queue = queue.Queue()

import queue
import threading
import requests

request_queue = queue.Queue()
BACKEND_API = "http://localhost:5000"

def get_latest_roomcode(userId):
    # Gọi BE để lấy bản ghi gần nhất của userId
    res = requests.get(f"{BACKEND_API}/rentals?userId={userId}&_sort=createdAt&_order=desc&_limit=1")
    if res.status_code == 200 and res.json():
        latest = res.json()[0]
        return latest.get("roomCode")
    return None

def worker():
    while True:
        job = request_queue.get()
        if not job:
            continue

        action, userId, rentalTime, rentalId, extra = job

        if action == "create_room":
            print(f"[INFO] Worker tạo room cho userId={userId}, rentalId={rentalId}, rentalTime={rentalTime}")

            # gọi tool để tạo room
            room_code = create_room_oslink()

            payload = {"roomCode": room_code, "status": "active"}
            print(f"[DEBUG] PATCH body gửi lên BE: {payload}")

            res = requests.patch(f"{BACKEND_API}/rentals/{rentalId}", json=payload)
            print(f"[INFO] PATCH rentalId={rentalId}, status={res.status_code}, resp={res.text}")

        elif action == "extend_room":
            room_code = get_latest_roomcode(userId)
            if not room_code:
                print(f"[ERROR] Không tìm thấy roomCode cho userId={userId}, không thể extend.")
                request_queue.task_done()
                continue

            print(f"[INFO] Worker extend room cho userId={userId}, rentalTime={rentalTime}, roomCode={room_code}")

            # 👉 Gọi tool thật để extend
            extended = run_extend_tool(room_code)
            if not extended:
                print("[ERROR] Extend tool thất bại, bỏ qua")
                request_queue.task_done()
                continue

            payload = {"userId": userId, "roomCode": room_code, "rentalTime": rentalTime, "status": "active"}
            res = requests.post(f"{BACKEND_API}/rentals", json=payload)
            print(f"[INFO] POST rental mới, status={res.status_code}, resp={res.text}")


        request_queue.task_done()




# =========================
#   WINDOW / RATIO HELPERS
# =========================
def get_window_by_title(title_substr: str):
    """Lấy cửa sổ theo 'chứa' chuỗi tiêu đề (title_substr). Trả về window đầu tiên."""
    wins = gw.getWindowsWithTitle(title_substr)
    if not wins:
        print(f"[ERROR] Không tìm thấy cửa sổ chứa: '{title_substr}'")
        return None
    # Ưu tiên cửa sổ đang hiển thị
    for w in wins:
        if w.visible:   # ✅ sửa ở đây
            return w
    return wins[0]


def window_rect_abs(title_substr: str):
    """Trả về (left, top, width, height) tuyệt đối của cửa sổ."""
    w = get_window_by_title(title_substr)
    if w is None:
        return None
    return (w.left, w.top, w.width, w.height)


def ratio_point_to_abs(win_rect, rel_x: float, rel_y: float):
    """Chuyển (rel_x, rel_y) 0..1 trong cửa sổ -> (abs_x, abs_y)."""
    left, top, width, height = win_rect
    abs_x = int(left + width * float(rel_x))
    abs_y = int(top  + height * float(rel_y))
    return abs_x, abs_y


def ratio_region_to_abs(win_rect, region_ratio):
    """
    region_ratio: [x_ratio, y_ratio, w_ratio, h_ratio] trong cửa sổ (0..1).
    Trả về region tuyệt đối: (x, y, w, h)
    """
    if not region_ratio or len(region_ratio) != 4:
        raise ValueError("region_ratio phải có dạng [x_ratio, y_ratio, w_ratio, h_ratio]")
    left, top, width, height = win_rect
    rx, ry, rw, rh = [float(v) for v in region_ratio]
    abs_x = int(left + width  * rx)
    abs_y = int(top  + height * ry)
    abs_w = int(width  * rw)
    abs_h = int(height * rh)
    return (abs_x, abs_y, abs_w, abs_h)



def run_extend_tool(roomCode: str):
    """
    Hàm extend room: thêm 1 màn hình LDPlayer vào roomCode đã có.
    """
    print(f"[TOOL] Extend room {roomCode} → thêm 1 LDPlayer")
    try:
        result = run_script(EXTEND_ACTIONS_FILE, room_name=roomCode)  # ✅ sửa tên biến
        print(f"[INFO] Extend room {roomCode} thành công")
        return result
    except Exception as e:
        print(f"[ERROR] Extend room {roomCode} thất bại: {e}")
        return None
# =========================
#   OPENCV TEMPLATE MATCH
# =========================
def find_image_opencv(image_path, region=None, threshold=0.75, scales=(0.8, 1.2, 9)):
    """
    Tìm ảnh trên màn hình bằng OpenCV + multi-scale.
    region: (x,y,w,h) tuyệt đối hoặc None (full screen).
    Trả về: (center_xy, score) hoặc (None, best_score)
    """
    try:
        if region:
            screenshot = pyautogui.screenshot(region=region)
            offset_x, offset_y = region[0], region[1]
        else:
            screenshot = pyautogui.screenshot()
            offset_x, offset_y = 0, 0

        screen_np = np.array(screenshot)
        screen_gray = cv2.cvtColor(screen_np, cv2.COLOR_BGR2GRAY)

        template = cv2.imread(image_path, cv2.IMREAD_COLOR)
        if template is None:
            print(f"[ERROR] Không thể đọc file ảnh: {image_path}")
            return None, 0.0

        template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
        th, tw = template_gray.shape

        best_val = 0.0
        best_pt = None
        best_scale = 1.0

        start, end, num = scales
        for scale in np.linspace(start, end, int(num)):
            resized = cv2.resize(template_gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            if resized.shape[0] > screen_gray.shape[0] or resized.shape[1] > screen_gray.shape[1]:
                continue
            res = cv2.matchTemplate(screen_gray, resized, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(res)
            if max_val > best_val:
                best_val = max_val
                best_pt = max_loc
                best_scale = scale

        if best_val >= threshold and best_pt is not None:
            cx = best_pt[0] + offset_x + int((tw * best_scale) / 2)
            cy = best_pt[1] + offset_y + int((th * best_scale) / 2)
            return (cx, cy), float(best_val)

        return None, float(best_val)
    except Exception as e:
        print(f"[ERROR] find_image_opencv lỗi: {e}")
        return None, 0.0


def find_all_images(image_path, region=None, threshold=0.8):
    """
    Tìm tất cả vị trí ảnh trong vùng màn hình với độ chính xác >= threshold.
    Trả về list các tuple (x, y) là tọa độ trung tâm.
    """
    try:
        screenshot = pyautogui.screenshot(region=region)
        screenshot_rgb = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)

        template = cv2.imread(image_path, cv2.IMREAD_COLOR)
        if template is None:
            print(f"[ERROR] Không load được ảnh: {image_path}")
            return []

        result = cv2.matchTemplate(screenshot_rgb, template, cv2.TM_CCOEFF_NORMED)
        locations = np.where(result >= threshold)

        matches = []
        h, w = template.shape[:2]
        for pt in zip(*locations[::-1]):  # (x, y)
            center_x = pt[0] + w // 2
            center_y = pt[1] + h // 2
            # Nếu có region thì cộng offset
            if region:
                center_x += region[0]
                center_y += region[1]
            matches.append((center_x, center_y))

        print(f"[DEBUG] Tìm thấy {len(matches)} icon khớp {image_path}")
        return matches
    except Exception as e:
        print(f"[ERROR] Lỗi find_all_images: {e}")
        return []

# =========================
#   ACTIONS
# =========================
def load_actions(file_path=ACTIONS_FILE):
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Không tìm thấy file action: {file_path}")
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def run_action(action, room_name=None):
    action_type = action.get("type")

    # --------------- MỚI: theo cửa sổ/ratio ---------------
    if action_type == "wait":
        seconds = float(action.get("seconds", 1))
        print(f"[INFO] Waiting {seconds}s...")
        time.sleep(seconds)
        return None

    elif action_type == "click_window":
        window = action.get("window", "LDPlayer")
        rel_x = action.get("rel_x", 0.5)
        rel_y = action.get("rel_y", 0.5)
        rect = window_rect_abs(window)
        if rect:
            abs_x, abs_y = ratio_point_to_abs(rect, rel_x, rel_y)
            pyautogui.click(abs_x, abs_y)
            print(f"[INFO] click_window '{window}' ratio ({rel_x},{rel_y}) -> ({abs_x},{abs_y})")
        return None

    elif action_type == "click_image_in_window":
        window = action.get("window", "LDPlayer")
        image_path = action.get("image")
        threshold = float(action.get("threshold", 0.75))
        region_ratio = action.get("region_ratio")
        rect = window_rect_abs(window)
        if rect:
            region_abs = ratio_region_to_abs(rect, region_ratio) if region_ratio else rect
            center, score = find_image_opencv(image_path, region=region_abs, threshold=threshold)
            if center:
                pyautogui.click(center)
                print(f"[INFO] click_image_in_window '{window}' {image_path} at {center} (score={score:.2f})")
            else:
                print(f"[WARN] Không tìm thấy {image_path} trong '{window}', best_score={score:.2f}")
        return None

    elif action_type == "ocr_copy_window":
        window = action.get("window", "LDPlayer")
        region_ratio = action.get("region_ratio")
        rect = window_rect_abs(window)
        if rect:
            try:
                region_abs = ratio_region_to_abs(rect, region_ratio)
                screenshot = pyautogui.screenshot(region=region_abs)
                text = pytesseract.image_to_string(screenshot, config="--psm 6").strip()
                if text:
                    pyperclip.copy(text)
                    print(f"[INFO] OCR (in '{window}') copied: {text}")
                    return text
                else:
                    print("[WARN] OCR không nhận được text nào.")
            except Exception as e:
                print(f"[ERROR] OCR lỗi: {e}")
        return None

    elif action_type == "click_min_number_window":
        window = action.get("window", "LDPlayer")
        region_ratio = action.get("region_ratio")
        rect = window_rect_abs(window)
        if rect:
            region_abs = ratio_region_to_abs(rect, region_ratio)
            screenshot = pyautogui.screenshot(region=region_abs)
            text = pytesseract.image_to_string(screenshot, config="--psm 6 digits")
            numbers = [int(num) for num in re.findall(r"\d+", text)]
            if numbers:
                min_num = min(numbers)
                cx = region_abs[0] + region_abs[2] // 2
                cy = region_abs[1] + region_abs[3] // 2
                pyautogui.click(cx, cy)
                print(f"[INFO] Click số nhỏ nhất {min_num} tại ({cx}, {cy}) trong '{window}'")
            else:
                print("[WARN] Không tìm thấy số nào trong vùng.")
        return None

    elif action_type == "scroll_window":
        window = action.get("window", "LDPlayer")
        amount = int(action.get("amount", -1200))
        rect = window_rect_abs(window)
        if rect:
            cx, cy = ratio_point_to_abs(rect, 0.5, 0.5)
            pyautogui.moveTo(cx, cy, duration=0.1)
            pyautogui.scroll(amount)
            print(f"[INFO] scroll_window '{window}' amount={amount}")
        return None

    # --------------- CŨ: tuyệt đối ---------------
    elif action_type == "click":
        image_path = action.get("image")
        if image_path:
            center, score = find_image_opencv(image_path, threshold=float(action.get("threshold", 0.75)))
            if center:
                pyautogui.click(center)
                print(f"[INFO] Clicked {image_path} tại {center} (score={score:.2f})")
            else:
                print(f"[WARN] Không tìm thấy {image_path} bằng OpenCV")
        else:
            x, y = action.get("x"), action.get("y")
            if x is not None and y is not None:
                pyautogui.click(x, y)
                print(f"[INFO] Click tại tọa độ ({x}, {y})")
            else:
                print("[WARN] Không có 'image' hoặc (x, y) hợp lệ.")
        return None

    elif action_type == "click_bottom_icon":
        image_path = action.get("image")
        window_title = action.get("window")
        region_ratio = action.get("region_ratio", [0, 0, 1, 1])
        threshold = float(action.get("threshold", 0.75))

        if not image_path:
            print(f"[ERROR] Thiếu 'image' trong action: {action}")
            return None

        # Lấy cửa sổ
        win = get_window_by_title(window_title)
        if not win:
            print(f"[ERROR] Không tìm thấy cửa sổ: {window_title}")
            return None

        # Tính toán region theo tỷ lệ
        x = win.left + int(win.width * region_ratio[0])
        y = win.top + int(win.height * region_ratio[1])
        w = int(win.width * (region_ratio[2] - region_ratio[0]))
        h = int(win.height * (region_ratio[3] - region_ratio[1]))
        region = (x, y, w, h)

        # Tìm tất cả ảnh
        matches = find_all_images(image_path, region=region, threshold=threshold)
        if matches:
            target = max(matches, key=lambda c: c[1])  # chọn icon có y lớn nhất
            pyautogui.click(target)
            print(f"[INFO] Click bottom icon cuối cùng {image_path} tại {target}")
        else:
            print(f"[WARN] Không tìm thấy icon {image_path} trong vùng {region}")
        return None


    elif action_type == "type":
        text = action.get("text", "")
        if room_name:
            text = text.replace("{room_name}", room_name)
        pyautogui.typewrite(text)
        print(f"[INFO] Gõ: {text}")
        return None

    elif action_type == "type_room_name":
        prefix = action.get("prefix", "khach")
        counter_file = action.get("counter_file", "room_counter.txt")
        try:
            with open(counter_file, "r") as f:
                counter = int(f.read().strip())
        except FileNotFoundError:
            counter = 1
        room = f"{prefix}{counter}"
        pyautogui.typewrite(room)
        print(f"[INFO] Nhập room_name: {room}")
        with open(counter_file, "w") as f:
            f.write(str(counter + 1))
        return None

    elif action_type == "copy_clipboard":
        try:
            content = pyperclip.paste()
            print(f"[INFO] Clipboard: {content}")
            match = re.search(r'Code mời:\s*([a-z0-9]+)', content, re.I)
            return match.group(1) if match else None
        except Exception as e:
            print(f"[ERROR] Không đọc được clipboard: {e}")
            return None

    elif action_type == "ocr_copy":
        region = action.get("region")
        if region:
            try:
                screenshot = pyautogui.screenshot(region=tuple(region))
                text = pytesseract.image_to_string(screenshot, config="--psm 6").strip()
                if text:
                    pyperclip.copy(text)
                    print(f"[INFO] OCR copied: {text}")
                    return text
                else:
                    print("[WARN] OCR không nhận được text nào.")
            except Exception as e:
                print(f"[ERROR] Lỗi OCR: {e}")
        else:
            print("[WARN] Thiếu region cho 'ocr_copy'")
        return None

    elif action_type == "click_min_number":
        if "window" in action and "region_ratio" in action:
            window = action.get("window", "LDPlayer")
            region_ratio = action.get("region_ratio")
            rect = window_rect_abs(window)
            if rect:
                region_abs = ratio_region_to_abs(rect, region_ratio)
                screenshot = pyautogui.screenshot(region=region_abs)
                text = pytesseract.image_to_string(screenshot, config="--psm 6 digits")
                numbers = [int(num) for num in re.findall(r"\d+", text)]
                if numbers:
                    min_num = min(numbers)
                    cx = region_abs[0] + region_abs[2] // 2
                    cy = region_abs[1] + region_abs[3] // 2
                    pyautogui.click(cx, cy)
                    print(f"[INFO] Click số nhỏ nhất {min_num} tại ({cx}, {cy}) trong '{window}'")
                else:
                    print("[WARN] Không tìm thấy số nào trong vùng.")
        else:
            region = action.get("region")
            if region:
                screenshot = pyautogui.screenshot(region=tuple(region))
                text = pytesseract.image_to_string(screenshot, config="--psm 6 digits")
                numbers = [int(num) for num in re.findall(r"\d+", text)]
                if numbers:
                    min_num = min(numbers)
                    cx = region[0] + region[2] // 2
                    cy = region[1] + region[3] // 2
                    pyautogui.click(cx, cy)
                    print(f"[INFO] Click số nhỏ nhất {min_num} tại ({cx}, {cy})")
                else:
                    print("[WARN] Không tìm thấy số nào trong vùng.")
            else:
                print("[WARN] Thiếu region cho 'click_min_number'")
        return None

    elif action_type == "close_app":
        image_path = action.get("image", "images/oslink_x.png")
        center, score = find_image_opencv(image_path, threshold=float(action.get("threshold", 0.75)))
        if center:
            pyautogui.click(center)
            print(f"[INFO] Click nút X để đóng app (ảnh: {image_path})")
        else:
            print(f"[ERROR] Không tìm thấy nút X (ảnh: {image_path})")
        return None

    elif action_type == "scroll_down_max":
        pyautogui.scroll(-9999999)
        print("[INFO] Scrolling down to max...")
        return None

    elif action_type == "scroll_up_max":
        pyautogui.scroll(9999999)
        print("[INFO] Scrolling up to max...")
        return None

    else:
        print(f"[WARN] Action chưa hỗ trợ: {action_type}")
        return None


def run_script(file_path=ACTIONS_FILE, room_name=None):
    actions = load_actions(file_path)
    parts = []
    for action in actions:
        result = run_action(action, room_name=room_name)
        if result:
            parts.append(str(result).strip())
    return " ".join(parts) if parts else None



def run_create_tool(userId):
    """
    Hàm giả lập tạo room mới cho user.
    Trả về roomCode mới tạo được.
    """
    import random, string
    roomCode = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    print(f"[TOOL] Created room {roomCode} cho user {userId}")
    # TODO: ở đây bạn code thao tác tool thật sự
    return roomCode


def create_room_oslink():
    try:
        with open("room_counter.txt", "r") as f:
            counter = int(f.read().strip())
    except FileNotFoundError:
        counter = 1

    room_name = f"khach{counter}"
    print(f"[INFO] Room name: {room_name}")

    with open("room_counter.txt", "w") as f:
        f.write(str(counter + 1))

    room_code = run_script(ACTIONS_FILE, room_name=room_name)
    return room_code


def schedule_room_close(userId, rentalTime):
    def task():
        time.sleep(float(rentalTime) * 60 * 60)
        print(f"[INFO] Hết hạn thuê room user {userId}")
        # TODO: close_room_oslink() / remove_device_ldplayer()
        ROOMS.pop(userId, None)
    threading.Thread(target=task, daemon=True).start()


# =========================
#   Flask API
# =========================
@app.route("/command", methods=["POST"])
def command():
    data = request.get_json()
    action = data.get("action")

    if action == "create_room":
        userId = data.get("userId")
        rentalId = data.get("rentalId")
        rentalTime = data.get("rentalTime")

        # Đúng thứ tự: action, userId, rentalTime, rentalId, extra
        request_queue.put(("create_room", userId, rentalTime, rentalId, None))
        return jsonify({"status": "queued"})

    elif action == "extend_room":
        userId = data.get("userId")
        rentalId = data.get("rentalId")
        rentalTime = data.get("rentalTime")
        roomCode = data.get("roomCode")

        # Truyền roomCode làm extra
        request_queue.put(("extend_room", userId, rentalTime, rentalId, roomCode))
        return jsonify({"status": "queued"})

    else:
        return jsonify({"error": "Unknown action"}), 400


@app.route("/actions", methods=["GET"])
def get_actions():
    if not os.path.exists(ACTIONS_FILE):
        return jsonify([])
    with open(ACTIONS_FILE, "r", encoding="utf-8") as f:
        actions = json.load(f)
    return jsonify(actions)


@app.route("/actions", methods=["POST"])
def save_actions():
    data = request.json
    with open(ACTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    # Khởi động 1 worker chạy nền
    for _ in range(1):
        threading.Thread(target=worker, daemon=True).start()

    # Chạy Flask API
    app.run(port=5001, debug=False, use_reloader=False)
