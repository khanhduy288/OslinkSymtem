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

# ============== App & Config ==============
app = Flask(__name__)
CORS(app)

ACTIONS_FILE = "actions_create_room.json"
EXTEND_ACTIONS_FILE = "extend_actions.json"
BACKEND_API = os.getenv("BACKEND_API", "http://localhost:5000")   # FIX: cho phép override
ROOMS = {}  # userId -> {'room_code':..., 'end_time':...}
JSON_PATH = "close_room.json"

# --- ĐƯỜNG DẪN TESSERACT --- (ưu tiên ENV, fallback đường dẫn cứng)
tesseract_path = os.getenv("TESSERACT_PATH", r"C:\project12m\OslinkSymtem\tessat\tesseract.exe")
pytesseract.pytesseract.tesseract_cmd = tesseract_path

# Global queue
request_queue = queue.Queue()

# ============== Helpers ==============
def http_get(url, **kwargs):
    try:
        resp = requests.get(url, timeout=kwargs.pop("timeout", 8), **kwargs)
        resp.raise_for_status()
        return resp
    except requests.RequestException as e:
        print(f"[HTTP][GET] {url} error: {e}")
        return None

def http_post(url, json=None, **kwargs):
    try:
        resp = requests.post(url, json=json, timeout=kwargs.pop("timeout", 8), **kwargs)
        resp.raise_for_status()
        return resp
    except requests.RequestException as e:
        print(f"[HTTP][POST] {url} error: {e}, payload={json}")
        return None

def http_patch(url, json=None, **kwargs):
    try:
        resp = requests.patch(url, json=json, timeout=kwargs.pop("timeout", 8), **kwargs)
        resp.raise_for_status()
        return resp
    except requests.RequestException as e:
        print(f"[HTTP][PATCH] {url} error: {e}, payload={json}")
        return None

def get_latest_roomcode(userId):
    url = f"{BACKEND_API}/rentals?userId={userId}&_sort=createdAt&_order=desc&_limit=1"
    res = http_get(url)
    if res and res.status_code == 200:
        data = res.json()
        if data:
            return data[0].get("roomCode")
    return None

# Hàm parse bên ngoài
def parse_room_code(room_code):
    parts = room_code.split()
    server_letter = parts[0] if len(parts) > 0 else ""
    room_name = parts[1] if len(parts) > 1 else ""
    return server_letter, room_name

def find_text_in_image(screenshot, target_text):
    if hasattr(screenshot, "shape"):
        screenshot = Image.fromarray(screenshot)

    # Chỉ lấy ký tự đầu tiên
    target_char = target_text[0]

    data = pytesseract.image_to_data(screenshot, output_type=pytesseract.Output.DICT)
    for i, text in enumerate(data['text']):
        if text.strip().startswith(target_char):
            x = data['left'][i]
            y = data['top'][i]
            w = data['width'][i]
            h = data['height'][i]
            # Trả tọa độ tâm ký tự đầu tiên
            return (x + w // 2, y + h // 2)
    return None


# ============== Window Utils ==============
def get_window_by_title(title_substr: str):
    """Trả về window đầu tiên chứa title_substr, ưu tiên visible, cố gắng restore/activate."""
    wins = gw.getWindowsWithTitle(title_substr)
    if not wins:
        print(f"[ERROR] Không tìm thấy cửa sổ chứa: '{title_substr}'")
        return None
    for w in wins:
        try:
            if not w.visible or w.isMinimized:
                w.restore()
            w.activate()
            return w
        except Exception:
            # nếu activate lỗi, thử window tiếp theo
            continue
    return wins[0]

def window_rect_abs(title_substr: str):
    w = get_window_by_title(title_substr)
    if w is None:
        return None
    return (w.left, w.top, w.width, w.height)

def ratio_point_to_abs(win_rect, rel_x: float, rel_y: float):
    left, top, width, height = win_rect
    abs_x = int(left + width * float(rel_x))
    abs_y = int(top  + height * float(rel_y))
    return abs_x, abs_y

def ratio_region_to_abs(win_rect, region_ratio):
    if not region_ratio or len(region_ratio) != 4:
        raise ValueError("region_ratio phải có dạng [x_ratio, y_ratio, w_ratio, h_ratio]")
    left, top, width, height = win_rect
    rx, ry, rw, rh = [float(v) for v in region_ratio]
    abs_x = int(left + width  * rx)
    abs_y = int(top  + height * ry)
    abs_w = int(width  * rw)
    abs_h = int(height * rh)
    return (abs_x, abs_y, abs_w, abs_h)

# ============== OpenCV Template Match ==============
def find_image_opencv(image_path, region=None, threshold=0.75, scales=(0.8, 1.2, 9)):
    """
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
        # FIX: screenshot (PIL) là RGB → dùng RGB2GRAY
        screen_gray = cv2.cvtColor(screen_np, cv2.COLOR_RGB2GRAY)

        template = cv2.imread(image_path, cv2.IMREAD_COLOR)
        if template is None:
            print(f"[ERROR] Không thể đọc file ảnh: {image_path}")
            return None, 0.0

        template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
        th, tw = template_gray.shape  # (h, w)

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


def find_text_near_icon(screenshot, target_text, icon_path, max_dx=200, max_dy=20):
    """
    Tìm icon gần text target nhất theo cùng hàng, cùng chiều cao.
    - screenshot: PIL Image
    - target_text: text cần tìm
    - icon_path: đường dẫn ảnh icon
    - max_dx: khoảng cách tối đa ngang
    - max_dy: khoảng cách tối đa dọc
    Trả về (x,y) center của icon nếu tìm thấy, None nếu không.
    """
    data = pytesseract.image_to_data(screenshot, output_type=pytesseract.Output.DICT)

    # Tìm tất cả text target
    candidates = []
    for i, text in enumerate(data['text']):
        if target_text in text:
            x = data['left'][i]
            y = data['top'][i]
            w = data['width'][i]
            h = data['height'][i]
            candidates.append((x, y, w, h))
    if not candidates:
        return None

    # Tải icon
    icon_img = cv2.imread(icon_path, cv2.IMREAD_UNCHANGED)
    icon_gray = cv2.cvtColor(icon_img, cv2.COLOR_BGR2GRAY)
    screenshot_cv = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2GRAY)

    # Template matching
    res = cv2.matchTemplate(screenshot_cv, icon_gray, cv2.TM_CCOEFF_NORMED)
    threshold = 0.8
    loc = np.where(res >= threshold)
    icon_positions = list(zip(*loc[::-1]))  # list of (x,y) top-left

    # Lọc icon gần text nhất
    for tx, ty, tw, th in candidates:
        for ix, iy in icon_positions:
            if abs(iy - ty) <= max_dy and 0 <= ix - (tx + tw) <= max_dx:
                # click center icon
                cx = ix + icon_gray.shape[1] // 2
                cy = iy + icon_gray.shape[0] // 2
                return (cx, cy)
    return None


def find_all_images(image_path, region=None, threshold=0.8):
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
            if region:
                center_x += region[0]
                center_y += region[1]
            matches.append((center_x, center_y))
        print(f"[DEBUG] Tìm thấy {len(matches)} icon khớp {image_path}")
        return matches
    except Exception as e:
        print(f"[ERROR] Lỗi find_all_images: {e}")
        return []

# ============== Actions ==============
def load_actions(file_path=ACTIONS_FILE):
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Không tìm thấy file action: {file_path}")
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)

def run_action(action, room_name=None, **kwargs):
    action_type = action.get("type")

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

    elif action_type == "click_server":
        window_title = action.get("window", "LDPlayer")
        region_ratio = action.get("region_ratio")  # [rel_x, rel_y, rel_w, rel_h]

        # Lấy room_code từ kwargs (đúng với key truyền từ run_script)
        room_code = room_name
        print(f"[DEBUG] click_server nhận room_code: {room_name}")

        if not room_name or not region_ratio:
            print("[ERROR] Thiếu params cho click_server")
            return

        # Tách server và room_name
        server_letter = room_name[0]
        room_name = room_name.split()[1] if len(room_name.split()) > 1 else ""

        # Lấy rect cửa sổ
        win_rect = window_rect_abs(window_title)
        if not win_rect:
            print(f"[ERROR] Không lấy được rect cho window '{window_title}'")
            return

        # Chuyển region_ratio thành tọa độ tuyệt đối
        x, y, w, h = ratio_region_to_abs(win_rect, region_ratio)

        # Chụp ảnh vùng này
        screenshot = pyautogui.screenshot(region=(x, y, w, h))
        
        # Tìm vị trí server_letter trong ảnh
        coords = find_text_in_image(screenshot, server_letter)

        if coords:
            abs_x = x + coords[0]
            abs_y = y + coords[1]
            pyautogui.click(abs_x, abs_y)
            print(f"[INFO] Click server {server_letter} tại ({abs_x},{abs_y})")
            time.sleep(1)
            # Gõ toàn bộ room_code
            pyautogui.typewrite(room_code)
            pyautogui.press("enter")
        else:
            print(f"[WARN] Không tìm thấy server {server_letter}")



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

    elif action_type == "click_min_number":
        window = action.get("window", "LDPlayer")
        region_ratio = action.get("region_ratio")
        rect = window_rect_abs(window)
        if rect:
            region_abs = ratio_region_to_abs(rect, region_ratio)
            screenshot = pyautogui.screenshot(region=region_abs)
            gray = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2GRAY)

            data = pytesseract.image_to_data(gray, output_type=pytesseract.Output.DICT)

            numbers = []
            print("[DEBUG] Các số OCR tìm được trong vùng:")
            for i, text in enumerate(data["text"]):
                clean = text.strip()
                if re.fullmatch(r"\d+", clean):  # chỉ nhận số
                    num = int(clean)
                    x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
                    numbers.append((num, x, y, w, h))
                    print(f"  - {num} tại ({x},{y},{w},{h})")

            if numbers:
                min_num, nx, ny, nw, nh = min(numbers, key=lambda x: x[0])
                cx = region_abs[0] + nx + nw // 2
                cy = region_abs[1] + ny + nh // 2
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

    elif action_type == "click":
        image_path = action.get("image")
        threshold = float(action.get("threshold", 0.75))

        if image_path:
            # Tìm tất cả vị trí khớp thay vì chỉ 1
            matches = find_all_images(image_path, threshold=threshold)

            if matches:
                # Sắp xếp theo y trước, rồi x (ưu tiên trên–trái)
                matches.sort(key=lambda p: (p[1], p[0]))
                center = matches[0]
                pyautogui.click(center)
                print(f"[INFO] Clicked (top-left) {image_path} tại {center}")
            else:
                print(f"[WARN] Không tìm thấy {image_path} bằng OpenCV (threshold={threshold})")
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

        win = get_window_by_title(window_title)
        if not win:
            print(f"[ERROR] Không tìm thấy cửa sổ: {window_title}")
            return None

        x = win.left + int(win.width * region_ratio[0])
        y = win.top + int(win.height * region_ratio[1])
        w = int(win.width * (region_ratio[2] - region_ratio[0]))
        h = int(win.height * (region_ratio[3] - region_ratio[1]))
        region = (x, y, w, h)

        matches = find_all_images(image_path, region=region, threshold=threshold)
        if matches:
            # Chọn icon có y lớn nhất (dưới cùng)
            target = max(matches, key=lambda c: c[1])
            pyautogui.click(target)
            print(f"[INFO] Click bottom icon cuối cùng {image_path} tại {target}")

            # target = (center_x, center_y)
            icon_cx, icon_cy = target
            icon_w, icon_h = 40, 40  # giả định icon vuông, cao 40px

            # Vùng OCR: cùng hàng với icon, dịch qua phải
            ocr_x = int(icon_cx + icon_w // 2 + 5)
            ocr_y = int(icon_cy - icon_h // 2)
            ocr_w = 300
            ocr_h = icon_h

            try:
                region_ocr = (ocr_x, ocr_y, ocr_w, ocr_h)
                print(f"[DEBUG] OCR region: {region_ocr}")  # log vùng OCR
                screenshot = pyautogui.screenshot(region=region_ocr)
                text = pytesseract.image_to_string(screenshot, config="--psm 7").strip()

                if text:
                    # Regex bắt ký tự in hoa (A-Z) + dấu gạch ngang + số (vd: A-1, B-4, C-12)
                    match = re.search(r"[A-Z]-\d+", text)
                    if match:
                        cleaned = match.group(0)
                    else:
                        cleaned = text.split()[0]

                    pyperclip.copy(cleaned)
                    print(f"[INFO] OCR bên phải icon: '{text}' -> cleaned: '{cleaned}' (copied)")
                    return cleaned

                else:
                    print("[WARN] OCR không nhận được text nào.")
            except Exception as e:
                print(f"[ERROR] OCR lỗi: {e}")
        else:
            print(f"[WARN] Không tìm thấy icon {image_path} trong vùng {region}")
        return None

    elif action_type == "click_bottom_icon_only":
        image_path = action.get("image")
        window_title = action.get("window")
        region_ratio = action.get("region_ratio", [0, 0, 1, 1])
        threshold = float(action.get("threshold", 0.75))

        if not image_path:
            print(f"[ERROR] Thiếu 'image' trong action: {action}")
            return None

        win = get_window_by_title(window_title)
        if not win:
            print(f"[ERROR] Không tìm thấy cửa sổ: {window_title}")
            return None

        # Tính region theo tỉ lệ cửa sổ
        x = win.left + int(win.width * region_ratio[0])
        y = win.top + int(win.height * region_ratio[1])
        w = int(win.width * (region_ratio[2] - region_ratio[0]))
        h = int(win.height * (region_ratio[3] - region_ratio[1]))
        region = (x, y, w, h)

        try:
            matches = find_all_images(image_path, region=region, threshold=threshold)
            if matches:
                # Chọn icon có toạ độ y lớn nhất (nằm thấp nhất)
                target = max(matches, key=lambda c: c[1])  # c = (center_x, center_y)
                pyautogui.click(target)
                print(f"[INFO] Click bottom icon (no OCR) {image_path} tại {target}")
                return True
            else:
                print(f"[WARN] Không tìm thấy icon {image_path} trong vùng {region}")
                return None
        except Exception as e:
            print(f"[ERROR] click_bottom_icon_only: {e}")
            return None

    elif action_type == "off_devide":
        window = action.get("window", "LDPlayer")
        off_icon = action.get("off_icon", "images/setting.png")  # icon cần click
        region_ratio = action.get("region", [0, 0, 1, 1])        # vùng tổng để tìm icon
        scroll_amount = action.get("scroll_amount", 100)         # pixels scroll mỗi lần
        max_scrolls = 1                                        # số lần scroll tối đa

        # Lấy target_text từ room_name
        target_text = None
        if room_name:
            parts = room_name.split()
            if len(parts) >= 3:
                target_text = parts[2]
        if not target_text:
            print("[WARN] Không lấy được target_text từ room_name")
            return None
        print(f"[INFO] off_devide target: {target_text}")

        # Lấy tọa độ tuyệt đối của region tổng
        rect_full = window_rect_abs(window)
        if not rect_full:
            print(f"[ERROR] Không tìm thấy window '{window}'")
            return None
        x0 = int(rect_full[0] + rect_full[2] * region_ratio[0])
        y0 = int(rect_full[1] + rect_full[3] * region_ratio[1])
        w = int(rect_full[2] * region_ratio[2])
        h = int(rect_full[3] * region_ratio[3])
        rect = (x0, y0, w, h)

        clicked = False
        scroll_count = 0

        while scroll_count <= max_scrolls and not clicked:
            # Screenshot toàn vùng
            screenshot_full = pyautogui.screenshot(region=rect)
            screenshot_cv = cv2.cvtColor(np.array(screenshot_full), cv2.COLOR_RGB2BGR)

            # Tìm tất cả vị trí icon
            icon_positions = find_all_images(off_icon, region=rect)
            print(f"[INFO] Tìm thấy {len(icon_positions)} icon '{off_icon}' (scroll lần {scroll_count})")

            for idx, (cx, cy) in enumerate(icon_positions):
                # Lấy kích thước icon
                icon_w, icon_h = 50, 50  # thay bằng kích thước thực tế icon nếu biết

                # Crop vùng OCR: bên phải icon
                margin_x = 8   # nhỏ hơn để không thừa ngang
                margin_y = 5   # cắt gọn trên dưới
                x1 = cx - rect[0] + icon_w - margin_x
                y1 = cy - rect[1] - margin_y
                x2 = x1 + 45 + 2*margin_x   # rộng vừa đủ chữ
                y2 = y1 + 30 + 2*margin_y   # cao vừa đủ chữ
                crop_img = screenshot_cv[y1:y2, x1:x2]

                # Lưu ảnh crop gốc
                cv2.imwrite(f"debug_crop_{target_text}_{scroll_count}_{idx}.png", crop_img)

                # Resize + threshold để OCR đọc tốt hơn
                scale = 3
                crop_img = cv2.resize(crop_img, (0, 0), fx=scale, fy=scale)
                gray = cv2.cvtColor(crop_img, cv2.COLOR_BGR2GRAY)

                # Threshold cho chữ trắng nền đen
                _, thresh = cv2.threshold(
                    gray, 0, 255,
                    cv2.THRESH_BINARY + cv2.THRESH_OTSU
                )

                # Lưu ảnh đã threshold để debug
                cv2.imwrite(f"debug_crop_thresh_{target_text}_{scroll_count}_{idx}.png", thresh)

                # OCR
                text = pytesseract.image_to_string(
                    thresh,
                    config='--psm 7 -c tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-'
                ).strip()

                print(f"[DEBUG] OCR read: '{text}'")

                if text.upper() == target_text.upper():
                    # Tìm icon bên phải cùng hàng gần nhất
                    candidates = [
                        (ix, iy) for ix, iy in icon_positions
                        if iy >= cy-10 and iy <= cy+10 and ix > cx
                    ]
                    if candidates:
                        right_icon = min(candidates, key=lambda p: p[0])
                        pyautogui.click(right_icon[0], right_icon[1])
                        print(f"[INFO] Clicked icon '{off_icon}' RIGHT of '{target_text}' tại {right_icon}")
                        clicked = True
                        break

            if not clicked:
                if scroll_count < max_scrolls:
                    # Scroll xuống
                    pyautogui.moveTo(rect[0] + rect[2]//2, rect[1] + rect[3]//2)
                    pyautogui.scroll(-scroll_amount)
                    print(f"[INFO] Scroll xuống {scroll_amount}px (lần {scroll_count+1})")
                    scroll_count += 1
                    time.sleep(0.5)  # chờ giao diện load
                else:
                    print(f"[WARN] Không tìm thấy icon gần '{target_text}' sau {max_scrolls} lần scroll")
                    break

        return target_text



    elif action_type == "type":
        text_mode = action.get("text_mode", "full")  # mặc định gõ full room_name
        if room_name:
            if text_mode == "second_token":
                parts = room_name.split()
                if len(parts) > 1:
                    text = parts[1]
                else:
                    text = room_name
            else:
                text = room_name
        else:
            text = action.get("text", "")
        pyautogui.typewrite(text)
        print(f"[INFO] Gõ ({text_mode}): {text}")
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

    elif action_type == "ocr_server_name":
        window = action.get("window", "LDPlayer")
        region_ratio = action.get("region_ratio")
        rect = window_rect_abs(window)
        if rect and region_ratio:
            try:
                region_abs = ratio_region_to_abs(rect, region_ratio)
                screenshot = pyautogui.screenshot(region=region_abs)
                text = pytesseract.image_to_string(screenshot, config="--psm 7").strip()
                if text:
                    first_char = text[0]  # lấy ký tự đầu tiên
                    pyperclip.copy(first_char)
                    print(f"[INFO] OCR server name: {text} -> first char: {first_char}")
                    return first_char
                else:
                    print("[WARN] OCR không nhận được text nào.")
            except Exception as e:
                print(f"[ERROR] OCR lỗi: {e}")
        else:
            print("[WARN] Thiếu region_ratio hoặc không tìm thấy window.")
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
        print(f"[DEBUG] Chạy action: {action} với room_name={room_name}")
        result = run_action(action, room_name=room_name)
        # Chỉ thêm nếu result là chuỗi
        if isinstance(result, str) and result.strip():
            parts.append(result.strip())
    return " ".join(parts) if parts else None


# ============== Business Tools ==============
def run_create_tool(userId):
    """Demo tạo room ngẫu nhiên (giữ nguyên)"""
    import random, string
    roomCode = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    print(f"[TOOL] Created room {roomCode} cho user {userId}")
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

def run_extend_tool(roomCode: str):
    """
    Extend room: thêm 1 LDPlayer vào room đã có.
    Trả True/False, KHÔNG dựa vào text trả về.
    """
    print(f"[TOOL] Extend room {roomCode} → thêm 1 LDPlayer")
    try:
        _ = run_script(EXTEND_ACTIONS_FILE, room_name=roomCode)
        print(f"[INFO] Extend room {roomCode} thành công")
        return True   # FIX: coi chạy xong là thành công
    except Exception as e:
        print(f"[ERROR] Extend room {roomCode} thất bại: {e}")
        return False

def schedule_room_close(userId, rentalTime):
    def task():
        time.sleep(float(rentalTime) * 60 * 60)
        print(f"[INFO] Hết hạn thuê room user {userId}")
        ROOMS.pop(userId, None)
    threading.Thread(target=task, daemon=True).start()

def update_close_room_json(room_code: str):
    server, room_name = parse_room_code(room_code)

    with open(JSON_PATH, "r") as f:
        actions = json.load(f)

    # Tìm action click_server và cập nhật
    for act in actions:
        if act.get("type") == "click_server":
            act["server_letter"] = server
            act["room_name"] = room_name

    with open(JSON_PATH, "w") as f:
        json.dump(actions, f, indent=2)

    print(f"Updated close_room.json with room_code={room_code}")
# ============== Worker ==============
def worker():
    while True:
        try:
            job = request_queue.get()
            if not job:
                request_queue.task_done()
                continue

            action, userId, rentalTime, rentalId, extra = job

            if action == "create_room":
                print(f"[INFO] Worker tạo room cho userId={userId}, rentalId={rentalId}, rentalTime={rentalTime}")
                room_code = create_room_oslink()
                payload = {"roomCode": room_code, "status": "active"}
                print(f"[DEBUG] PATCH body gửi lên BE: {payload}")
                res = http_patch(f"{BACKEND_API}/rentals/{rentalId}", json=payload)
                if res:
                    print(f"[INFO] PATCH rentalId={rentalId}, status={res.status_code}, resp={res.text}")

            elif action == "extend_room":
                # Ưu tiên roomCode từ request (extra), fallback BE
                room_code = extra or get_latest_roomcode(userId)
                if not room_code:
                    print(f"[ERROR] Không tìm thấy roomCode cho userId={userId}, không thể extend.")
                    request_queue.task_done()
                    continue

                print(f"[INFO] Worker extend room cho userId={userId}, rentalTime={rentalTime}, roomCode={room_code}")

                extended_ok = run_extend_tool(room_code)
                if not extended_ok:
                    print("[ERROR] Extend tool thất bại, bỏ qua")
                    request_queue.task_done()
                    continue

                payload = {
                    "userId": userId,
                    "roomCode": room_code,
                    "rentalTime": rentalTime,
                    "status": "active"
                }
                res = http_post(f"{BACKEND_API}/rentals", json=payload)
                if res:
                    print(f"[INFO] POST rental mới, status={res.status_code}, resp={res.text}")

            elif action == "close_room":
                userId = userId or "unknown"
                room_code = extra  # VD: "A khach73 fj27beba43re6ndw4"
                print(f"[INFO] Worker close_room userId={userId}, rentalId={rentalId}, roomCode={room_code}")

                try:
                    # chạy kịch bản JSON, chỉ truyền room_code
                    run_script("close_room.json", room_name=room_code)
                    print(f"[INFO] Close room automation đã chạy cho room {room_code}")
                except Exception as e:
                    print(f"[ERROR] Close room automation thất bại: {e}")

                # Báo BE update status expired
                payload = {"status": "expired"}
                res = http_patch(f"{BACKEND_API}/rentals/{rentalId}", json=payload)
                if res:
                    print(f"[INFO] PATCH rentalId={rentalId} -> expired OK")



            else:
                print(f"[WARN] Unknown worker action: {action}")

            request_queue.task_done()

        except Exception as e:
            print(f"[WORKER][ERROR] {e}")
            try:
                request_queue.task_done()
            except Exception:
                pass

            # tiếp tục vòng lặp

# ============== Flask API ==============
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True})

@app.route("/command", methods=["POST"])
def command():
    data = request.get_json(force=True, silent=True) or {}
    action = data.get("action")

    if action == "create_room":
        userId = data.get("userId")
        rentalId = data.get("rentalId")
        rentalTime = data.get("rentalTime")
        if not all([userId, rentalId, rentalTime]):
            return jsonify({"error": "Missing params"}), 400

        request_queue.put(("create_room", userId, rentalTime, rentalId, None))
        return jsonify({"status": "queued"})

    elif action == "extend_room":
        userId = data.get("userId")
        rentalId = data.get("rentalId")
        rentalTime = data.get("rentalTime")
        roomCode = data.get("roomCode")  # optional
        if not all([userId, rentalTime]):
            return jsonify({"error": "Missing params"}), 400

        request_queue.put(("extend_room", userId, rentalTime, rentalId, roomCode))
        return jsonify({"status": "queued"})

    elif action == "close_room":
        userId = data.get("userId")
        rentalId = data.get("rentalId")
        roomCode = data.get("roomCode")
        if not all([userId, rentalId, roomCode]):
            return jsonify({"error": "Missing params"}), 400

        request_queue.put(("close_room", userId, None, rentalId, roomCode))
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
    try:
        data = request.get_json(force=True)
        if not isinstance(data, list):
            return jsonify({"error": "actions must be a JSON array"}), 400
        with open(ACTIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

if __name__ == "__main__":
    # Khởi động 1 worker chạy nền
    threading.Thread(target=worker, daemon=True).start()
    # Chạy Flask API
    app.run(port=5001, debug=False, use_reloader=False)
