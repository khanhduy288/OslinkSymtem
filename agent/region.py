from pynput import mouse, keyboard

print("👉 Hướng dẫn:")
print(" - Giữ SHIFT + click chuột trái lần 1: chọn góc trên trái")
print(" - Giữ SHIFT + click chuột trái lần 2: chọn góc dưới phải")
print(" - Sau đó sẽ in ra region và tự thoát")

region = {}
shift_pressed = False
click_count = 0

# Biến lưu listener để stop sau khi xong
mouse_listener = None
keyboard_listener = None

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

        # Tính toán region
        x1, y1 = region['x1'], region['y1']
        x2, y2 = region['x2'], region['y2']
        x, y = min(x1, x2), min(y1, y2)
        w, h = abs(x2 - x1), abs(y2 - y1)
        print(f"✅ Region = ({x}, {y}, {w}, {h})")

        # Stop cả 2 listener => chương trình sẽ tự thoát
        mouse_listener.stop()
        keyboard_listener.stop()

# Khởi tạo listener
keyboard_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
mouse_listener = mouse.Listener(on_click=on_click)

keyboard_listener.start()
mouse_listener.start()

keyboard_listener.join()
mouse_listener.join()
