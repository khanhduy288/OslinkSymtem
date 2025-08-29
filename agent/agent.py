from flask import Flask, request, jsonify
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
from flask_cors import CORS
from PIL import Image

app = Flask(__name__)
CORS(app)
ACTIONS_FILE = "actions_create_room.json"
BACKEND_API = "http://localhost:5000"  # địa chỉ backend
ROOMS = {}  # lưu thông tin room đang thuê: userId -> {'room_code':..., 'end_time':...}
pytesseract.pytesseract.tesseract_cmd = r"C:\project12m\OslinkSymtem\tessat\tesseract.exe"
# Chụp màn hình khu vực (x, y, width, height)
region = (1631, 189, 126, 247)  # chỉnh lại theo màn hình của bạn


# ---------- Load & Run Action Script ----------
def load_actions(file_path="actions_create_room.json"):
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Không tìm thấy file action: {file_path}")
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)

import time
import pyautogui
import pyperclip
import pytesseract
import re

def run_action(action, room_name=None):
    action_type = action.get("type")

    if action_type == "wait":
        seconds = action.get("seconds", 1)
        print(f"[INFO] Waiting {seconds}s...")
        time.sleep(seconds)

    elif action_type == "click":
        image_path = action.get("image")
        if image_path:
            try:
                location = pyautogui.locateCenterOnScreen(image_path, confidence=0.8)
                if location:
                    pyautogui.click(location)
                    print(f"[INFO] Clicked on image: {image_path}")
                else:
                    print(f"[WARN] Không tìm thấy hình ảnh {image_path}")
            except Exception as e:
                print(f"[ERROR] Lỗi khi tìm ảnh: {e}")
        else:
            x, y = action.get("x"), action.get("y")
            if x is not None and y is not None:
                pyautogui.click(x, y)
                print(f"[INFO] Click tại tọa độ ({x}, {y})")
            else:
                print("[WARN] Không có 'image' hoặc (x, y) hợp lệ.")

    elif action_type == "type":
        text = action.get("text", "")
        if room_name:
            text = text.replace("{room_name}", room_name)
        pyautogui.typewrite(text)
        print(f"[INFO] Gõ: {text}")

    elif action_type == "type_room_name":
        prefix = action.get("prefix", "khach")
        counter_file = action.get("counter_file", "room_counter.txt")
        try:
            with open(counter_file, "r") as f:
                counter = int(f.read().strip())
        except FileNotFoundError:
            counter = 1

        room_name = f"{prefix}{counter}"
        pyautogui.typewrite(room_name)
        print(f"[INFO] Nhập room_name: {room_name}")

        counter += 1
        with open(counter_file, "w") as f:
            f.write(str(counter))

    elif action_type == "copy_clipboard":
        try:
            content = pyperclip.paste()
            print(f"[INFO] Clipboard: {content}")
            # Tách code mời từ nội dung clipboard
            match = re.search(r'Code mời:\s*([a-z0-9]+)', content)
            if match:
                return match.group(1)
            else:
                print("[WARN] Không tìm thấy code trong clipboard")
                return None
        except Exception as e:
            print(f"[ERROR] Không đọc được clipboard: {e}")



    elif action_type == "ocr_copy":
        region = action.get("region")
        if not region:
            print("[WARN] Thiếu region cho action 'ocr_copy'")
            return None
        try:
            screenshot = pyautogui.screenshot(region=region)
            text = pytesseract.image_to_string(screenshot, config="--psm 6")
            text = text.strip()
            if text:
                pyperclip.copy(text)
                print(f"[INFO] OCR text copied to clipboard: {text}")
                return text
            else:
                print("[WARN] OCR không nhận được text nào.")
        except Exception as e:
            print(f"[ERROR] Lỗi OCR: {e}")

    elif action_type == "click_min_number":
        region = action.get("region")
        if region:
            screenshot = pyautogui.screenshot(region=region)
            text = pytesseract.image_to_string(screenshot, config="--psm 6 digits")
            print("OCR text:\n", text)
            numbers = [int(num) for num in re.findall(r"\d+", text)]
            if numbers:
                min_num = min(numbers)
                print(f"Số nhỏ nhất: {min_num}")
                x = region[0] + region[2] // 2
                y = region[1] + region[3] // 2
                pyautogui.click(x, y)
                print(f"[INFO] Click vào số nhỏ nhất tại ({x}, {y})")
            else:
                print("[WARN] Không tìm thấy số nào trong vùng.")
        else:
            print("[WARN] Không có region trong action 'click_min_number'.")

    elif action_type == "click_bottom_icon":
        image_path = action.get("image")
        region = action.get("region")
        try:
            import cv2
            import numpy as np

            if region:
                screenshot = pyautogui.screenshot(region=region)
                offset_x, offset_y = region[0], region[1]
            else:
                screenshot = pyautogui.screenshot()
                offset_x, offset_y = 0, 0

            screen_np = np.array(screenshot)
            screen_gray = cv2.cvtColor(screen_np, cv2.COLOR_BGR2GRAY)

            template = cv2.imread(image_path, cv2.IMREAD_UNCHANGED)
            template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
            w, h = template_gray.shape[::-1]

            res = cv2.matchTemplate(screen_gray, template_gray, cv2.TM_CCOEFF_NORMED)
            threshold = 0.8
            loc = np.where(res >= threshold)
            positions = list(zip(*loc[::-1]))

            if positions:
                bottom_most = max(positions, key=lambda p: p[1])
                x_click = offset_x + bottom_most[0] + w // 2
                y_click = offset_y + bottom_most[1] + h // 2
                pyautogui.click(x_click, y_click)
                print(f"[INFO] Click vào biểu tượng Android màu xanh tại ({x_click}, {y_click})")
            else:
                print("[WARN] Không tìm thấy biểu tượng Android màu xanh")
        except Exception as e:
            print(f"[ERROR] Lỗi khi click_bottom_icon: {e}")

    elif action_type == "close_app":
        image_path = action.get("image", "images/oslink_x.png")
        try:
            location = pyautogui.locateCenterOnScreen(image_path, confidence=0.8)
            if location:
                pyautogui.click(location)
                print(f"[INFO] Click nút X để đóng app (ảnh: {image_path})")
            else:
                print(f"[ERROR] Không tìm thấy nút X (ảnh: {image_path})")
        except Exception as e:
            print(f"[ERROR] Lỗi khi click X để đóng app: {e}")

    elif action_type == "scroll_down_max":
        print("[INFO] Scrolling down to max...")
        pyautogui.scroll(-9999999)

    elif action_type == "scroll_up_max":
        print("[INFO] Scrolling up to max...")
        pyautogui.scroll(9999999)

    else:
        print(f"[WARN] Action chưa hỗ trợ: {action_type}")

    return None


def run_script(file_path="actions_create_room.json", room_name=None):
    actions = load_actions(file_path)
    room_code_parts = []
    
    for action in actions:
        result = run_action(action, room_name=room_name)
        if result:  # lưu kết quả vào danh sách
            room_code_parts.append(str(result).strip())
    
    # nối các phần bằng khoảng trắng
    room_code = " ".join(room_code_parts)
    return room_code if room_code else None


# ---------- Business Logic ----------
def create_room_oslink():
    # Lấy số thứ tự room
    try:
        with open("room_counter.txt", "r") as f:
            counter = int(f.read().strip())
    except FileNotFoundError:
        counter = 1

    room_name = f"khach{counter}"
    print(f"[INFO] Room name: {room_name}")

    # Tăng counter
    counter += 1
    with open("room_counter.txt", "w") as f:
        f.write(str(counter))

    # Chạy script JSON
    room_code = run_script("actions_create_room.json", room_name=room_name)

    return room_code

# ================= Task quản lý thời gian thuê =================
def schedule_room_close(userId, rentalTime):
    def task():
        time.sleep(rentalTime * 60 * 60)  # giờ -> giây
        print(f"[INFO] Hết hạn thuê room user {userId}")
        close_room_oslink()
        remove_device_ldplayer()
        ROOMS.pop(userId, None)
    threading.Thread(target=task, daemon=True).start()

# ================= Flask API =================
@app.route('/command', methods=['POST'])
def command():
    data = request.json
    action = data.get('action')
    userId = data.get('userId')
    rentalTime = data.get('rentalTime')
    rentalId = data.get('rentalId')

    if action == 'create_room':
        print(f"[INFO] Tạo room cho user {userId}")
        room_code = create_room_oslink()
        ROOMS[userId] = {'room_code': room_code}
        schedule_room_close(userId, rentalTime)

        try:
            res = requests.patch(
                f"{BACKEND_API}/rentals/{rentalId}",
                json={'roomCode': room_code, 'status': 'active'}
            )
            print(f"[INFO] PATCH roomCode={room_code} rentalId={rentalId}, status={res.status_code}")
        except Exception as e:
            print(f"[ERR] PATCH thất bại: {e}")

        return jsonify({'status': 'ok', 'room_code': room_code})
    
    elif action == 'close_room':
        print(f"[INFO] Đóng room user {userId}")
        ROOMS.pop(userId, None)
        return jsonify({'status': 'ok'})
    
    return jsonify({'status': 'unknown action'})

@app.route("/create-room", methods=["POST"])
def create_room():
    room_code = create_room_oslink()
    return jsonify({"room_code": room_code})

@app.route("/actions", methods=["GET"])
def get_actions():
    if not os.path.exists(ACTIONS_FILE):
        return jsonify([])  # trả về mảng rỗng nếu chưa có
    with open(ACTIONS_FILE, "r", encoding="utf-8") as f:
        actions = json.load(f)
    return jsonify(actions)

@app.route("/actions", methods=["POST"])
def save_actions():
    data = request.json
    with open(ACTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return jsonify({"status": "ok"})

if __name__ == '__main__':
    app.run(port=5001)
