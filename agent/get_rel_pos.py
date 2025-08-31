import pyautogui
import pygetwindow as gw
import time

# Tên cửa sổ muốn lấy (VD: LDPlayer, Chrome, Notepad...)
WINDOW_NAME = "LDPlayer"

print(f"[INFO] Đang tìm cửa sổ: {WINDOW_NAME}...")
win_list = gw.getWindowsWithTitle(WINDOW_NAME)

if not win_list:
    print(f"[ERROR] Không tìm thấy cửa sổ {WINDOW_NAME}")
    exit(1)

win = win_list[0]
print(f"[INFO] Cửa sổ {WINDOW_NAME} tọa độ: left={win.left}, top={win.top}, width={win.width}, height={win.height}")

print("👉 Di chuột tới vị trí cần lấy trong 5 giây...")
time.sleep(5)

# Lấy tọa độ tuyệt đối
x, y = pyautogui.position()

# Tính tọa độ tương đối trong cửa sổ
rel_x = (x - win.left) / win.width
rel_y = (y - win.top) / win.height

print(f"[RESULT] Tọa độ tuyệt đối: ({x}, {y})")
print(f"[RESULT] Tọa độ tương đối trong {WINDOW_NAME}: rel_x={rel_x:.3f}, rel_y={rel_y:.3f}")
