from pynput import mouse, keyboard
import pygetwindow as gw

print("👉 Hướng dẫn:")
print(" - Giữ SHIFT + click chuột trái lần 1: chọn góc trên trái")
print(" - Giữ SHIFT + click chuột trái lần 2: chọn góc dưới phải")
print(" - Sau đó sẽ in ra region (absolute & relative) và tự thoát")

region = {}
shift_pressed = False
click_count = 0

mouse_listener = None
keyboard_listener = None

# --------- HÀM LẤY CỬA SỔ LDPLAYER ---------
def get_ldplayer_window(title="LDPlayer"):
    wins = gw.getWindowsWithTitle(title)
    if not wins:
        print("[ERROR] Không tìm thấy LDPlayer")
        return None
    return wins[0]

def on_press(key):
    global shift_pressed
    if key == keyboard.Key.shift:
        shift_pressed = True

def on_release(key):
    global shift_pressed
    if key == keyboard.Key.shift:
        shift_pressed = False

def on_click(x, y, button, pressed):
    global region, shift_pressed, click_count, mouse_listener, keyboard_listener
    if not shift_pressed or not pressed or button != mouse.Button.left:
        return

    click_count += 1
    if click_count == 1:
        region['x1'], region['y1'] = x, y
        print(f"📍 Góc trên trái: ({x}, {y})")
    elif click_count == 2:
        region['x2'], region['y2'] = x, y
        print(f"📍 Góc dưới phải: ({x}, {y})")

        # Tính toán absolute region
        x1, y1 = region['x1'], region['y1']
        x2, y2 = region['x2'], region['y2']
        x, y = min(x1, x2), min(y1, y2)
        w, h = abs(x2 - x1), abs(y2 - y1)

        print(f"✅ Absolute Region = ({x}, {y}, {w}, {h})")

        # Lấy relative region theo LDPlayer
        win = get_ldplayer_window()
        if win:
            rel_x = (x - win.left) / win.width
            rel_y = (y - win.top) / win.height
            rel_w = w / win.width
            rel_h = h / win.height
            print(f"🌐 Relative Region = [{rel_x:.3f}, {rel_y:.3f}, {rel_w:.3f}, {rel_h:.3f}] (so với {win.title})")

        # Stop listener
        mouse_listener.stop()
        keyboard_listener.stop()

# Khởi tạo listener
keyboard_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
mouse_listener = mouse.Listener(on_click=on_click)

keyboard_listener.start()
mouse_listener.start()

keyboard_listener.join()
mouse_listener.join()
