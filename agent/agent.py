from flask import Flask, request, jsonify
from flask_cors import CORS
from fuzzywuzzy import fuzz
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
import keyboard
import numpy as np

# ============== App & Config ==============
app = Flask(__name__)
CORS(app)
ACTIONS_FILE = "actions_create_room.json"
EXTEND_ACTIONS_FILE = "extend_actions.json"
BACKEND_API = os.getenv("BACKEND_API", "https://api.tabtreo.com")   # FIX: cho phép override
ROOMS = {}  # userId -> {'room_code':..., 'end_time':...}
JSON_PATH = "close_room.json"
ADMIN_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpZCI6MSwibGV2ZWwiOjEwMCwiaWF0IjoxNzU4MzA2MzQ2LCJleHAiOjE3NTg5MTExNDZ9.X0D-2uuv_rw2SpvJZjIUkHvXDnhQufLzKWRH2-LAv9o"
# --- ĐƯỜNG DẪN TESSERACT --- (ưu tiên ENV, fallback đường dẫn cứng)
tesseract_path = os.getenv("TESSERACT_PATH", r"D:\project12m\OslinkSymtem\tessat\tesseract.exe")
pytesseract.pytesseract.tesseract_cmd = tesseract_path

# --- Biến toàn cục lưu tên thiết bị đã copy ---
copied_names_global = set()
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

def http_patch(url, json=None, token=None, **kwargs):
    try:
        headers = kwargs.pop("headers", {})
        if token:
            headers["Authorization"] = f"Bearer {token}"
        else:
            # nếu không truyền token thì mặc định dùng admin token
            headers["Authorization"] = f"Bearer {ADMIN_TOKEN}"

        resp = requests.patch(
            url,
            json=json,
            headers=headers,
            timeout=kwargs.pop("timeout", 8),
            **kwargs
        )
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

def click_image_in_region(image_path, threshold=0.75, window=None, region_ratio=None):
    region = None
    if window and region_ratio:
        wins = gw.getWindowsWithTitle(window)
        if wins:
            win = wins[0]
            wx, wy, ww, wh = win.left, win.top, win.width, win.height
            rx = int(wx + ww * region_ratio[0])
            ry = int(wy + wh * region_ratio[1])
            rw = int(ww * region_ratio[2])
            rh = int(wh * region_ratio[3])
            region = (rx, ry, rw, rh)

    center, score = find_image_opencv(image_path, threshold=threshold, region=region)
    if center:
        pyautogui.click(center)
        print(f"[INFO] Clicked {image_path} tại {center} (score={score:.2f}, region={region})")
        return True
    else:
        print(f"[WARN] Không tìm thấy {image_path} trong region={region}, best_score={score:.2f}")
        return False
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

    elif action_type == "click_server_name":
        window_title = action.get("window", "LDPlayer")
        server_name = action.get("server_name")

        # Dùng region cố định
        region_ratio = [0.009, 0.081, 0.123, 0.339]

        if not server_name:
            print("[ERROR] Thiếu server_name cho click_server_name")
            return

        # Lấy rect cửa sổ
        win_rect = window_rect_abs(window_title)
        if not win_rect:
            print(f"[ERROR] Không lấy được rect cho window '{window_title}'")
            return

        # Chuyển region_ratio thành tọa độ tuyệt đối
        x, y, w, h = ratio_region_to_abs(win_rect, region_ratio)

        # Chụp ảnh vùng này
        screenshot = pyautogui.screenshot(region=(x, y, w, h))

        # Tìm vị trí server_name trong ảnh
        coords = find_text_in_image(screenshot, server_name)
        if coords:
            abs_x = x + coords[0]
            abs_y = y + coords[1]
            pyautogui.click(abs_x, abs_y)
            print(f"[INFO] Click server {server_name} tại ({abs_x},{abs_y})")
            time.sleep(1)
            # Nếu cần gõ thêm enter
            pyautogui.press("enter")
        else:
            print(f"[WARN] Không tìm thấy server {server_name} trong vùng")



    elif action_type == "click_region":
        image_path = action.get("image")
        threshold = float(action.get("threshold", 0.75))
        window = action.get("window", None)
        region_ratio = action.get("region_ratio", None)

        if image_path:
            click_image_in_region(image_path, threshold=threshold, window=window, region_ratio=region_ratio)
        else:
            print("[WARN] 'click_region' cần 'image'")
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

    elif action_type == "ocr_copy":
        # region_ratio: [x_ratio, y_ratio, w_ratio, h_ratio] tương đối so với cửa sổ
        region_ratio = action.get("region")
        if not region_ratio:
            print("[WARN] Thiếu region cho 'ocr_copy'")
            return None

        win_rect = window_rect_abs("LDPlayer")
        if not win_rect:
            print("[WARN] Không tìm thấy cửa sổ LDPlayer")
            return None

        try:
            abs_region = ratio_region_to_abs(win_rect, region_ratio)
            screenshot = pyautogui.screenshot(region=abs_region)
            text = pytesseract.image_to_string(screenshot, config="--psm 6").strip()
            if text:
                print(f"[INFO] OCR result: {text}")
                return text   # chỉ return, không copy
            else:
                print("[WARN] OCR không nhận được text nào.")
                return None
        except Exception as e:
            print(f"[ERROR] Lỗi OCR: {e}")
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
                print("[WARN] Không tìm thấy số nào trong vùng. Fallback click vị trí khác.")
                # ---- fallback click sang vị trí tùy chỉnh ----
                # ví dụ click góc trên bên phải vùng quét
                cx = region_abs[0] + int(region_abs[2] * 0.8)
                cy = region_abs[1] + int(region_abs[3] * 0.2)
                pyautogui.click(cx, cy)
                print(f"[INFO] Fallback click tại ({cx},{cy})")
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


    elif action_type == "click_device_by_name":
        window_title = action.get("window", "LDPlayer")
        region_ratio = action.get("region_ratio", [0, 0, 1, 1])

        # Lấy device target từ clipboard
        target_device = pyperclip.paste().strip().upper()
        if not target_device or not re.match(r"^[A-Z]-\d{1,2}$", target_device):
            print(f"[ERROR] Clipboard không có device hợp lệ: '{target_device}'")
            return None

        # Chuẩn hóa input: thêm số 0 phía trước nếu chỉ có 1 chữ số
        m = re.match(r"([A-Z])-(\d{1,2})", target_device)
        if m:
            prefix = m.group(1)
            suffix = m.group(2).zfill(2)
            target_device = f"{prefix}-{suffix}"

        win = get_window_by_title(window_title)
        if not win:
            print(f"[ERROR] Không tìm thấy cửa sổ: {window_title}")
            return None

        abs_region = (
            int(win.left + region_ratio[0] * win.width),
            int(win.top + region_ratio[1] * win.height),
            int(region_ratio[2] * win.width),
            int(region_ratio[3] * win.height)
        )

        MAX_SCROLL = 90
        IMG_SCALE = 2
        Y_CLICK_OFFSET = 8
        found_and_clicked = False

        for scroll_round in range(MAX_SCROLL):
            print(f"[DEBUG] ===== Scroll round {scroll_round} =====")
            # Screenshot toàn region
            screenshot = pyautogui.screenshot(region=abs_region)
            gray = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2GRAY)
            _, bin_img = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)

            # Resize để OCR dễ đọc
            img = cv2.resize(
                bin_img,
                (bin_img.shape[1] * IMG_SCALE, bin_img.shape[0] * IMG_SCALE),
                interpolation=cv2.INTER_LINEAR
            )

            text_data = pytesseract.image_to_data(
                img,
                output_type=pytesseract.Output.DICT,
                config="--psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-"
            )

            print(f"[DEBUG] OCR phát hiện {len(text_data['text'])} text box")

            for i, raw_text in enumerate(text_data['text']):
                raw_text = re.sub(r'\s+', '', raw_text.strip().upper())
                match = re.match(r'([A-Z])-?(\d{1,2})', raw_text)
                if not match:
                    continue
                prefix = match.group(1)
                suffix = match.group(2).zfill(2)
                text = f"{prefix}-{suffix}"

                print(f"[DEBUG] OCR chuẩn: '{text}' tại (scaled) ({text_data['left'][i]},{text_data['top'][i]},{text_data['width'][i]},{text_data['height'][i]})")

                if text == target_device:
                    print(f"[INFO] Match chính xác device '{text}' → '{target_device}', đợi màn hình đứng yên...")
                    time.sleep(1.0)

                    # OCR lại để lấy tọa độ chính xác
                    screenshot2 = pyautogui.screenshot(region=abs_region)
                    gray2 = cv2.cvtColor(np.array(screenshot2), cv2.COLOR_RGB2GRAY)
                    _, bin2 = cv2.threshold(gray2, 150, 255, cv2.THRESH_BINARY)
                    img2 = cv2.resize(bin2, (bin2.shape[1]*IMG_SCALE, bin2.shape[0]*IMG_SCALE), interpolation=cv2.INTER_LINEAR)

                    text_data2 = pytesseract.image_to_data(
                        img2,
                        output_type=pytesseract.Output.DICT,
                        config="--psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-"
                    )

                    for j, raw2 in enumerate(text_data2['text']):
                        raw2 = re.sub(r'\s+', '', raw2.strip().upper())
                        match2 = re.match(r'([A-Z])-?(\d{1,2})', raw2)
                        if not match2:
                            continue
                        prefix2 = match2.group(1)
                        suffix2 = match2.group(2).zfill(2)
                        text2 = f"{prefix2}-{suffix2}"

                        if text2 == target_device:
                            # Tính tọa độ click
                            x_scaled, y_scaled = text_data2['left'][j], text_data2['top'][j]
                            w_scaled, h_scaled = text_data2['width'][j], text_data2['height'][j]
                            center_x_orig = (x_scaled + w_scaled / 2.0) / IMG_SCALE
                            center_y_orig = (y_scaled + h_scaled / 2.0) / IMG_SCALE
                            click_x = abs_region[0] + int(center_x_orig)
                            click_y = abs_region[1] + int(center_y_orig) + Y_CLICK_OFFSET

                            try:
                                win.activate()
                            except Exception:
                                pass

                            time.sleep(0.05)
                            pyautogui.moveTo(click_x, click_y, duration=0.12)
                            pyautogui.click()
                            print(f"[INFO] Click chính xác vào '{text2}' tại ({click_x},{click_y})")
                            found_and_clicked = True
                            break
                    if found_and_clicked:
                        break
            if found_and_clicked:
                break

            # Scroll xuống nếu chưa tìm thấy
            pyautogui.moveTo(win.centerx, win.centery)
            pyautogui.scroll(-500)
            time.sleep(0.3)

        if not found_and_clicked:
            print(f"[WARN] Không tìm thấy device '{target_device}'")
            return None

        return None


        

    elif action_type == "get_next_device_name":
        window_title = action.get("window", "LDPlayer")
        region_ratio = action.get("region_ratio", [0, 0, 1, 1])
        scan_delay = action.get("scan_delay", 0.3)
        scroll_delay = action.get("scroll_delay", 0.5)

        # Lấy prefix từ clipboard
        prefix = pyperclip.paste().strip().upper()
        prefix = re.sub(r'[^A-Z]', '', prefix)
        if not prefix or len(prefix) != 1:
            print(f"[ERROR] Clipboard không có prefix hợp lệ: '{prefix}'")
            return None

        # Lấy cửa sổ
        win = get_window_by_title(window_title)
        if not win:
            print(f"[ERROR] Không tìm thấy cửa sổ: {window_title}")
            return None

        abs_region = (
            int(win.left + region_ratio[0] * win.width),
            int(win.top + region_ratio[1] * win.height),
            int(region_ratio[2] * win.width),
            int(region_ratio[3] * win.height)
        )

        IMG_SCALE = 2
        MAX_SCROLL = 80
        all_numbers = set()
        found_prefix = False
        no_new_rounds = 0
        ocr_config = "--oem 3 --psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-"

        for scroll_round in range(MAX_SCROLL):
            print(f"[DEBUG] ===== Scroll round {scroll_round} =====")

            new_found = False

            # OCR 2 lần mỗi vòng
            for attempt in range(2):
                screenshot = pyautogui.screenshot(region=abs_region)
                gray = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2GRAY)
                _, bin_img = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)
                img = cv2.resize(bin_img, (bin_img.shape[1]*IMG_SCALE, bin_img.shape[0]*IMG_SCALE), interpolation=cv2.INTER_LINEAR)

                text_data = pytesseract.image_to_data(
                    img,
                    output_type=pytesseract.Output.DICT,
                    config=ocr_config
                )

                for i in range(len(text_data['text'])):
                    raw_text = text_data['text'][i].strip().upper()
                    raw_text = re.sub(r'\s+', '', raw_text)
                    if not raw_text:
                        continue
                    print(f"[OCR] {raw_text}")

                    match = re.match(rf'({prefix})-?(\d{{1,2}})', raw_text)
                    if match:
                        num = int(match.group(2).zfill(2))
                        if num not in all_numbers:
                            all_numbers.add(num)
                            new_found = True
                            print(f"[DEBUG] Thêm {prefix}-{str(num).zfill(2)} vào all_numbers")

                time.sleep(scan_delay)

            if new_found:
                found_prefix = True

            if found_prefix:
                if not new_found:
                    no_new_rounds += 1
                else:
                    no_new_rounds = 0

                if no_new_rounds >= 3:
                    print("[DEBUG] Sau 3 vòng không có thiết bị mới, dừng quét.")
                    break

            # Scroll xuống như trước
            pyautogui.moveTo(win.centerx, win.centery)
            pyautogui.scroll(-500)
            time.sleep(scroll_delay)

        if not all_numbers:
            print(f"[WARN] Không tìm thấy thiết bị nào với prefix {prefix}")
            return None

        print(f"[DEBUG] all_numbers thu thập được: {sorted(all_numbers)}")

        # Sinh số mới
        next_num = 1
        while next_num in all_numbers:
            next_num += 1

        new_name = f"{prefix}-{str(next_num).zfill(2)}"
        pyperclip.copy(new_name)
        print(f"[INFO] Sinh tên mới: {new_name} (đã copy vào clipboard)")
        return new_name



    elif action_type == "delete_device_by_roomname":
        window = action.get("window", "LDPlayer")
        region_ratio = action.get("region", [0.002, 0.149, 0.972, 0.732])

        # Lấy room_name từ hàm gọi
        print(f"[DEBUG] Room name nhận được: '{room_name}'")
        parts = room_name.split()
        print(f"[DEBUG] parts của room_name: {parts}")

        if len(parts) < 3:
            print("[ERROR] Room name quá ngắn để lấy thiết bị")
            return None

        # Lấy thiết bị cần xóa từ phần thứ 3
        device_to_delete_raw = parts[1].upper()
        if "-" in device_to_delete_raw:
            prefix, num = device_to_delete_raw.split("-")
            try:
                device_to_delete = f"{prefix}-{int(num)}"
            except:
                device_to_delete = device_to_delete_raw
        else:
            device_to_delete = device_to_delete_raw

        print(f"[INFO] Tên thiết bị cần xóa: {device_to_delete}")

        rect = window_rect_abs(window)
        if not rect:
            print(f"[ERROR] Không tìm thấy cửa sổ '{window}'")
            return None

        copied_names = set()
        scroll_attempts = 5

        for attempt in range(scroll_attempts):
            region_abs = ratio_region_to_abs(rect, region_ratio)
            setting_icons = find_all_images("images/setting2.png", region=region_abs, threshold=0.6)
            print(f"[DEBUG] Scroll attempt {attempt+1}/{scroll_attempts}, tìm thấy {len(setting_icons)} setting_icon")

            if not setting_icons:
                pyautogui.moveTo(region_abs[0] + region_abs[2]//2,
                                region_abs[1] + region_abs[3]//2)
                pyautogui.scroll(-region_abs[3])
                time.sleep(1)
                continue

            found_new = False
            for idx, target in enumerate(setting_icons, 1):
                print(f"[DEBUG] Xử lý setting_icon {idx}/{len(setting_icons)} tại {target}")
                pyautogui.click(target)
                time.sleep(1.5)

                # Vào rename
                click_image_in_region("images/rename2.png", window=window, region_ratio=region_ratio)
                time.sleep(1)

                # Giữ chuột textbox
                textbox_x, textbox_y = 888, 529
                pyautogui.mouseDown(x=textbox_x, y=textbox_y)
                time.sleep(0.5)
                pyautogui.mouseUp(x=textbox_x, y=textbox_y)
                time.sleep(0.5)

                # Copy text
                click_image_in_region("images/select_all1.png", window=window, region_ratio=[0,0,1,1])
                time.sleep(0.9)
                click_image_in_region("images/saochep1.png", window=window, region_ratio=[0,0,1,1])
                time.sleep(0.9)

                copied_text_raw = pyperclip.paste().strip().upper()
                if "-" in copied_text_raw:
                    prefix, num = copied_text_raw.split("-")
                    try:
                        copied_text = f"{prefix}-{int(num)}"
                    except:
                        copied_text = copied_text_raw
                else:
                    copied_text = copied_text_raw

                print(f"[DEBUG] Copy được: {copied_text}")
                copied_names.add(copied_text)

                # Back ra sau khi copy
                click_image_in_region("images/back_button1.png", window=window, region_ratio=region_ratio)
                time.sleep(1)

                # Nếu trùng thiết bị, kick room và thoát vòng lặp
                if copied_text == device_to_delete:
                    print(f"[INFO] Tìm thấy thiết bị cần xóa: {copied_text}")

                    pyautogui.click(target)
                    time.sleep(1.5)

                    # Kiểm tra và tắt thiết bị nếu chưa tắt
                    off_icons = find_all_images("images/off_icon1.png", region=region_abs, threshold=0.7)
                    if off_icons:
                        print(f"[INFO] Thiết bị chưa tắt, click để tắt")
                        pyautogui.click(off_icons[0])
                        time.sleep(2)

                        # Click lại setting_icon để kick room
                        pyautogui.click(target)
                        time.sleep(1.5)
                    else:
                        print(f"[INFO] Thiết bị đã tắt, không cần click off_icon")

                    # Kick room
                    click_image_in_region("images/kickroom.png", window=window, region_ratio=region_ratio)
                    time.sleep(3)

                    found_new = True
                    break
                else:
                    print(f"[INFO] Thiết bị {copied_text} không trùng -> bỏ qua")

            if found_new:
                print("[INFO] Đã kick room, dừng vòng lặp")
                break

            # Scroll xuống nếu chưa kick
            pyautogui.moveTo(region_abs[0]+region_abs[2]//2, region_abs[1]+region_abs[3]//2)
            pyautogui.scroll(-region_abs[3])
            time.sleep(1)

        print(f"[INFO] Danh sách thiết bị copy được: {copied_names}")
        return list(copied_names)

    elif action_type == "check_and_giai_tan":
        image_check = action.get("image")  # ví dụ: "images/check3.png"
        window_name = action.get("window")
        region_ratio = action.get("region_ratio")
        threshold = float(action.get("threshold", 0.75))

        # Chuyển region_ratio -> pixel
        region = None
        if window_name and region_ratio:
            win_rect = window_rect_abs(window_name)
            if win_rect:
                region = ratio_region_to_abs(win_rect, region_ratio)

        # Kiểm tra ảnh
        center, score = find_image_opencv(image_check, region=region, threshold=threshold)
        if center:
            print(f"[INFO] Icon {image_check} tồn tại (score={score:.2f}), sẽ giai tan room...")
            # Click menu3.png
            click_image_in_region("images/menu3.png", window=window_name)
            time.sleep(3)
            # Click giaitanroom.png
            click_image_in_region("images/giaitan3.png", window=window_name)
            time.sleep(3)
            click_image_in_region("images/xacnhan3.png", window=window_name)
            time.sleep(3)
            click_image_in_region("images/huy1.png", window=window_name)
            time.sleep(3)
            print("[INFO] Room đã được giải tán")
        else:
            time.sleep(3)
            click_image_in_region("images/back1.png", window=window_name)
            time.sleep(3)
            click_image_in_region("images/huy1.png", window=window_name)
            time.sleep(3)
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

    elif action_type == "get_new_device_name":
        window = action.get("window", "LDPlayer")
        off_icon = action.get("off_icon", "images/setting.png")  # icon cần scan
        region_ratio = action.get("region", [0, 0, 1, 1])
        scroll_amount = action.get("scroll_amount", 100)
        max_scrolls = 3

        # --- Lấy server prefix từ clipboard ---
        try:
            server_name = pyperclip.paste().strip()
            if not server_name:
                print("[ERROR] Clipboard trống, không lấy được server name")
                return None
            prefix = server_name.split("-")[0].upper()
        except Exception as e:
            print(f"[ERROR] Lỗi lấy server name từ clipboard: {e}")
            return None
        print(f"[INFO] Server prefix: {prefix}")

        # --- Lấy tọa độ tuyệt đối của region ---
        rect_full = window_rect_abs(window)
        if not rect_full:
            print(f"[ERROR] Không tìm thấy window '{window}'")
            return None

        rect = ratio_region_to_abs(rect_full, region_ratio)
        if not rect:
            print("[ERROR] Không tính được region abs")
            return None

        all_names = set()
        scroll_count = 0

        while scroll_count <= max_scrolls:
            screenshot_full = pyautogui.screenshot(region=rect)
            screenshot_cv = cv2.cvtColor(np.array(screenshot_full), cv2.COLOR_RGB2BGR)

            # --- Tìm tất cả icon ---
            icon_positions = find_all_images(off_icon, region=rect)
            print(f"[INFO] Tìm thấy {len(icon_positions)} icon '{off_icon}' (scroll {scroll_count})")

            for idx, (cx, cy) in enumerate(icon_positions):
                icon_w, icon_h = 50, 50

                # --- Crop OCR bên trái icon ---
                offset_left, offset_right, offset_top, offset_bottom = 489, 20, 30, -30
                x1 = cx - rect[0] - offset_left
                y1 = cy - rect[1] - offset_top
                x2 = cx - rect[0] + offset_right
                y2 = cy - rect[1] + icon_h + offset_bottom

                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(screenshot_cv.shape[1], x2), min(screenshot_cv.shape[0], y2)

                crop_img = screenshot_cv[y1:y2, x1:x2]

                # Resize + threshold
                scale = 3
                crop_img = cv2.resize(crop_img, (0, 0), fx=scale, fy=scale)
                gray = cv2.cvtColor(crop_img, cv2.COLOR_BGR2GRAY)
                _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

                # OCR
                text = pytesseract.image_to_string(
                    thresh,
                    config='--psm 7 -c tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-'
                ).strip()

                if text:
                    text = text.replace(" ", "").upper()
                    all_names.add(text)
                    print(f"[DEBUG] OCR device: '{text}'")

            # --- Scroll xuống ---
            pyautogui.moveTo(rect[0] + rect[2] // 2, rect[1] + rect[3] // 2)
            pyautogui.scroll(-scroll_amount)
            scroll_count += 1
            time.sleep(0.5)

        print(f"[INFO] Danh sách thiết bị OCR được: {all_names}")

        # --- Tính số mới ---
        numbers = []
        for name in all_names:
            if name.startswith(prefix + "-"):
                try:
                    numbers.append(int(name.split("-")[1]))
                except:
                    pass

        new_number = max(numbers) + 1 if numbers else 1
        new_device_name = f"{prefix}-{new_number}"
        pyperclip.copy(new_device_name)
        print(f"[INFO] Tên thiết bị mới: {new_device_name} (đã copy vào clipboard)")

        return new_device_name


    elif action_type == "off_devide":
        window = action.get("window", "LDPlayer")
        off_icon = action.get("off_icon", "images/setting.png")  # icon cần click
        region_ratio = action.get("region", [0, 0, 1, 1])        # vùng tổng để tìm icon
        scroll_amount = action.get("scroll_amount", 100)         # pixels scroll mỗi lần
        max_scrolls = 3                                          # số lần scroll tối đa

        # Lấy target_text từ room_name
        target_text = None
        if room_name:
            parts = room_name.split()
            if len(parts) >= 3:
                target_text = parts[1]
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
                # Giả định kích thước icon
                icon_w, icon_h = 50, 50  

                # --- Cắt OCR bên trái icon ---
                offset_left = 489   # mở rộng sang trái nhiều hơn
                offset_right = 20   # dư thêm chút bên phải icon
                offset_top = 30     # sát phía trên
                offset_bottom = -30   # không cắt xuống dưới

                x1 = cx - rect[0] - offset_left
                y1 = cy - rect[1] - offset_top
                x2 = cx - rect[0] + offset_right
                y2 = cy - rect[1] + icon_h + offset_bottom

                # đảm bảo không vượt ngoài ảnh
                x1 = max(0, x1)
                y1 = max(0, y1)
                x2 = min(screenshot_cv.shape[1], x2)
                y2 = min(screenshot_cv.shape[0], y2)

                # crop vùng OCR
                crop_img = screenshot_cv[y1:y2, x1:x2]

                # debug: vẽ khung xanh để check
                # debug_img = screenshot_cv.copy()
                # cv2.rectangle(debug_img, (x1, y1), (x2, y2), (0, 255, 0), 2)
                # cv2.imwrite(f"debug_box_{target_text}_{scroll_count}_{idx}.png", debug_img)
                # cv2.imwrite(f"debug_crop_{target_text}_{scroll_count}_{idx}.png", crop_img)

                # Resize + threshold
                scale = 3
                crop_img = cv2.resize(crop_img, (0, 0), fx=scale, fy=scale)
                gray = cv2.cvtColor(crop_img, cv2.COLOR_BGR2GRAY)
                _, thresh = cv2.threshold(
                    gray, 0, 255,
                    cv2.THRESH_BINARY + cv2.THRESH_OTSU
                )
                cv2.imwrite(f"debug_crop_thresh_{target_text}_{scroll_count}_{idx}.png", thresh)

                # OCR
                text = pytesseract.image_to_string(
                    thresh,
                    config='--psm 7 -c tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-'
                ).strip()
                print(f"[DEBUG] OCR read: '{text}'")

                # So khớp fuzzy
                from fuzzywuzzy import fuzz
                score = fuzz.ratio(text.upper(), target_text.upper())
                print(f"[DEBUG] Fuzzy match: {score} với target '{target_text}'")

                if score >= 70:
                    pyautogui.click(cx, cy)
                    print(f"[INFO] Clicked icon '{off_icon}' tại ({cx}, {cy}) cho device '{target_text}'")
                    clicked = True
                    break

            if not clicked:
                if scroll_count < max_scrolls:
                    pyautogui.moveTo(rect[0] + rect[2]//2, rect[1] + rect[3]//2)
                    pyautogui.scroll(-scroll_amount)
                    print(f"[INFO] Scroll xuống {scroll_amount}px (lần {scroll_count+1})")
                    scroll_count += 1
                    time.sleep(0.5)
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
            elif text_mode == "third_token":
                parts = room_name.split()
                if len(parts) > 2:
                    text = parts[2]   # từ thứ 3 (index = 2)
                else:
                    text = room_name  # fallback nếu không đủ 3 từ
            else:
                text = room_name
        else:
            text = action.get("text", "")
        pyautogui.typewrite(text)
        print(f"[INFO] Gõ ({text_mode}): {text}")
        return None


    elif action_type == "type1":
            # Lấy text trực tiếp từ JSON
        text = action.get("text", "")
        interval = action.get("interval", 0.05)  # có thể thêm delay giữa các ký tự
        if not text:
            print("[WARN] Không có text nào để gõ.")
            return None
        pyautogui.typewrite(text, interval=interval)
        print(f"[INFO] Gõ trực tiếp từ JSON: {text}")
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
                    print(f"[INFO] OCR result: {text}")
                    return text   # chỉ return, không copy
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
        window = action.get("window", "LDPlayer")
        wins = gw.getWindowsWithTitle(window)
        if wins:
            win = wins[0]
            win.activate()
            time.sleep(0.3)

            # ==== Tùy chỉnh tọa độ cố định (VD: x=800, y=500) ====
            fixed_x, fixed_y = 939, 234  
            pyautogui.moveTo(fixed_x, fixed_y)
            time.sleep(0.2)

            pyautogui.scroll(-999999)
            print(f"[INFO] Scrolled down to max tại ({fixed_x},{fixed_y}).")
        else:
            print(f"[WARN] Không tìm thấy cửa sổ {window}")
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
    for idx, action in enumerate(actions, start=1):
        print(f"[DEBUG] Chạy action {idx}/{len(actions)}: {action} với room_name={room_name}")
        result = run_action(action, room_name=room_name)
        # Chỉ thêm nếu result là chuỗi
        if isinstance(result, str) and result.strip():
            parts.append(result.strip())

    summary = " ".join(parts) if parts else None
    print(f"[INFO] Hoàn thành script {file_path} ({len(actions)} actions) cho room {room_name}")
    return summary


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
        codenew = run_script(EXTEND_ACTIONS_FILE, room_name=roomCode)
        print(f"[INFO] Extend room {roomCode} thành công")
        return codenew   # FIX: coi chạy xong là thành công
    except Exception as e:
        print(f"[ERROR] Extend room {roomCode} thất bại: {e}")
        return None

def run_remove_group_tool(roomCode: str):
    """
    Remove toàn bộ group theo roomCode.
    Trả True/False, KHÔNG dựa vào text trả về.
    """
    print(f"[TOOL] Remove group với roomCode={roomCode}")
    try:
        _ = run_script("remove_group.json", room_name=roomCode)
        print(f"[INFO] Remove group {roomCode} thành công")
        return True
    except Exception as e:
        print(f"[ERROR] Remove group {roomCode} thất bại: {e}")
        return False


def run_change_devide_tool(roomCode: str):
    """
    Đổi thiết bị trong room theo roomCode.
    Trả về roomCode mới nếu thành công, None nếu thất bại.
    """
    print(f"[TOOL] Change devide cho roomCode={roomCode}")
    try:
        codenew = run_script("change_devide.json", room_name=roomCode)
        print(f"[INFO] Change devide {roomCode} thành công, codenew={codenew}")
        return codenew
    except Exception as e:
        print(f"[ERROR] Change devide {roomCode} thất bại: {e}")
        return None



def schedule_room_close(userId, rentalTime):
    def task():
        time.sleep(float(rentalTime) * 60 * 60)
        print(f"[INFO] Hết hạn thuê room user {userId}")
        ROOMS.pop(userId, None)
    threading.Thread(target=task, daemon=True).start()

def run_get_codenew_tool(room_code: str):
    """
    Lấy roomCode mới từ automation script và trả về roomCode đã cập nhật.
    Ví dụ:
      input:  "A khach1 F-1 ahjsdhaksd"
      script return: "ajshdhkac"
      output: "A khach1 F-1 ajshdhkac"
    """
    try:
        print(f"[DEBUG] run_get_codenew_tool start với room_code={room_code}")

        # chạy automation JSON để lấy code mới
        new_code = run_script("get_codenew.json", room_name=room_code)

        if not new_code:
            print("[ERROR] Script không trả về code mới")
            return None

        # tách prefix (giữ nguyên tất cả trừ phần cuối)
        parts = room_code.split(" ")
        if len(parts) < 2:
            print("[ERROR] room_code không đúng định dạng, không thể cập nhật")
            return None

        # thay thế phần cuối cùng = new_code
        updated_code = " ".join(parts[:-1] + [new_code])

        print(f"[INFO] run_get_codenew_tool → updated_code={updated_code}")
        return updated_code

    except Exception as e:
        print(f"[ERROR] run_get_codenew_tool fail: {e}")
        return None



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
                    "roomCode": extended_ok,
                    "rentalTime": rentalTime,
                    "status": "active"
                }
                res = http_patch(f"{BACKEND_API}/rentals/{rentalId}", json=payload)
                if res:
                    print(f"[INFO] PATCH rentalId={rentalId}, status={res.status_code}, resp={res.text}")

            elif action == "get_codenew":
                userId = userId or "unknown"
                room_code = extra
                print(f"[INFO] Worker get_codenew userId={userId}, rentalId={rentalId}, roomCode={room_code}")

                updated_code = run_get_codenew_tool(room_code)
                if updated_code:
                    payload = {"roomCode": updated_code}
                    print(f"[DEBUG] PATCH body gửi lên BE: {payload}")
                    res = http_patch(f"{BACKEND_API}/rentals/{rentalId}", json=payload)
                    if res:
                        print(f"[INFO] PATCH rentalId={rentalId}, status={res.status_code}, resp={res.text}")
                else:
                    print("[ERROR] Không thể cập nhật roomCode mới")


            elif action == "close_room":
                userId = userId or "unknown"
                room_code = extra  # VD: "A khach73 fj27beba43re6ndw4"
                print(f"[INFO] Worker close_room userId={userId}, rentalId={rentalId}, roomCode={room_code}")

                try:
                    run_script("close_room.json", room_name=room_code)
                    print(f"[INFO] Close room automation đã chạy cho room {room_code}")
                except Exception as e:
                    print(f"[ERROR] Close room automation thất bại: {e}")

                payload = {"status": "expired"}
                res = http_patch(f"{BACKEND_API}/rentals/{rentalId}", json=payload)
                if res:
                    print(f"[INFO] PATCH rentalId={rentalId} -> expired OK")

            # ================== ACTION MỚI ==================
            elif action == "remove_group":
                room_code = extra
                print(f"[INFO] Worker remove_group roomCode={room_code}, userId={userId}")
                run_script("remove_group.json", room_name=room_code)

            elif action == "change_devide":
                room_code = extra
                print(f"[INFO] Worker change_devide roomCode={room_code}, userId={userId}")

                codenew = run_change_devide_tool(room_code)
                if codenew:
                    payload = {"status": "active", "roomCode": codenew}
                    print(f"[DEBUG] PATCH body gửi lên BE: {payload}")
                    res = http_patch(f"{BACKEND_API}/rentals/{rentalId}", json=payload)
                    if res:
                        print(f"[INFO] PATCH rentalId={rentalId}, status={res.status_code}, resp={res.text}")
                else:
                    print(f"[WARN] Change_devide thất bại, rentalId={rentalId} vẫn pending_change_tab")


        except Exception as e:
            print(f"[WORKER][ERROR] {e}")
            try:
                request_queue.task_done()
            except Exception:
                pass


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

    # ================== API MỚI ==================
    elif action == "remove_group":
        userId = data.get("userId")
        roomCode = data.get("roomCode")
        if not all([userId, roomCode]):
            return jsonify({"error": "Missing params"}), 400

        request_queue.put(("remove_group", userId, None, None, roomCode))
        return jsonify({"status": "queued"})

    elif action == "change_devide":
        userId = data.get("userId")
        roomCode = data.get("roomCode")
        if not all([userId, roomCode]):
            return jsonify({"error": "Missing params"}), 400

        request_queue.put(("change_devide", userId, None, None, roomCode))
        return jsonify({"status": "queued"})

    elif action == "get_codenew":
        userId = data.get("userId")
        rentalId = data.get("rentalId")
        roomCode = data.get("roomCode")
        if not all([userId, rentalId, roomCode]):
            return jsonify({"error": "Missing params"}), 400

        request_queue.put(("get_codenew", userId, None, rentalId, roomCode))
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

        def one_line_json(obj):
            return json.dumps(obj, ensure_ascii=False, separators=(", ", ": "))

        with open(ACTIONS_FILE, "w", encoding="utf-8") as f:
            f.write("[\n")
            for i, action in enumerate(data):
                line = "  " + one_line_json(action)
                if i < len(data) - 1:
                    line += ","
                f.write(line + "\n")
            f.write("]\n")

        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

if __name__ == "__main__":
    # Khởi động 1 worker chạy nền
    threading.Thread(target=worker, daemon=True).start()
    # Chạy Flask API
    app.run(port=5001, debug=False, use_reloader=False)
