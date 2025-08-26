from flask import Flask, request, jsonify
import pyautogui
import pyperclip
import time
import threading
import requests

app = Flask(__name__)

BACKEND_API = "http://localhost:5000"  # địa chỉ backend
ROOMS = {}  # lưu thông tin room đang thuê: userId -> {'room_code':..., 'end_time':...}

# ================= Helper thao tác trên Oslink/LDPlayer =================
def open_ldplayer():
    pyautogui.press('win')
    time.sleep(0.5)
    pyautogui.write('LDPlayer')
    pyautogui.press('enter')
    time.sleep(5)  # đợi LDPlayer mở

import time
import pyautogui

def create_room_oslink():
    # --- Lấy số thứ tự room từ file ---
    try:
        with open("room_counter.txt", "r") as f:
            counter = int(f.read().strip())
    except FileNotFoundError:
        counter = 1  # lần đầu tiên

    room_name = f"khach{counter}"
    print(f"[INFO] Tên room: {room_name}")

    # --- Click lần đầu ---
    time.sleep(2)
    x1, y1 = 963, 297
    pyautogui.click(x1, y1)
    print(f"[INFO] Click vào tọa độ x={x1}, y={y1}")

    # --- Click đóng QC ---
    time.sleep(11)
    x2, y2 = 957, 769
    pyautogui.click(x2, y2)
    print(f"[INFO] Click vào tọa độ x={x2}, y={y2}")

    # --- Click cùng chơi ---
    time.sleep(3)
    x3, y3 = 1163, 945
    pyautogui.click(x3, y3)
    print(f"[INFO] Click vào tọa độ x={x3}, y={y3}")

    # --- Click tạo room ---
    time.sleep(3)
    x4, y4 = 1621, 145
    pyautogui.click(x4, y4)
    print(f"[INFO] Click vào tọa độ x={x4}, y={y4}")

    # --- Click nhập tên room ---
    time.sleep(3)
    x5, y5 = 887, 132
    pyautogui.click(x5, y5)
    pyautogui.typewrite(room_name)
    print(f"[INFO] Nhập tên room: {room_name}")

    time.sleep(3)

    # --- Cập nhật counter ---
    counter += 1
    with open("room_counter.txt", "w") as f:
        f.write(str(counter))

        # --- Click lần 6 ấn button tạo room
    time.sleep(3)
    x6, y6 = 951, 193
    pyautogui.click(x6, y6)
    print(f"[INFO] Click vào tọa độ x={x6}, y={y6}")


    time.sleep(3)
    x7, y7 = 751, 285
    pyautogui.click(x7, y7)
    print(f"[INFO] Click vào tọa độ x={x7}, y={y7}")

    time.sleep(5)
    x8, y8 = 702, 135
    pyautogui.click(x8, y8)
    print(f"[INFO] Click vào tọa độ x={x8}, y={y8}")

    time.sleep(3)
    x9, y9 = 962, 982
    pyautogui.click(x9, y9)
    print(f"[INFO] Click vào tọa độ x={x9}, y={y9}")
    time.sleep(2)

    x10, y10 = 1217, 76
    pyautogui.click(x10, y10)
    print(f"[INFO] Click vào tọa độ x={x10}, y={y10}")
    time.sleep(2)
    

    x11, y11 = 1151, 219
    pyautogui.click(x11, y11)
    print(f"[INFO] Click vào tọa độ x={x11}, y={y11}")
    time.sleep(2)

    x12, y12 = 1008, 913
    pyautogui.click(x12, y12)
    print(f"[INFO] Click vào tọa độ x={x12}, y={y12}")
    time.sleep(2)

    x13, y13 = 1031, 578
    pyautogui.click(x13, y13)
    print(f"[INFO] Click vào tọa độ x={x13}, y={y13}")
    time.sleep(3)

    x14, y14 = 1031, 578
    pyautogui.click(x14, y14)
    print(f"[INFO] Click vào tọa độ x={x14}, y={y14}")
    time.sleep(3)

    # === Xong toàn bộ, return room_code ===
    room_code = pyperclip.paste()
    print(f"[INFO] Room code lấy được: {room_code}")


    return room_code

def add_device_to_room():
    # Click Add Device
    pyautogui.click(x=200, y=300)  # chỉnh tọa độ
    time.sleep(1)

def close_room_oslink():
    # Vào Cùng chơi -> vào room -> tắt tab -> xóa room
    pyautogui.click(x=100, y=200)
    time.sleep(1)
    pyautogui.click(x=120, y=250)
    time.sleep(1)
    pyautogui.click(x=300, y=400)  # tắt tab
    time.sleep(1)
    pyautogui.click(x=150, y=300)  # xóa room
    time.sleep(1)

def remove_device_ldplayer():
    # Vào LdPlayer -> máy chủ chứa thiết bị -> Xóa thiết bị
    pyautogui.click(x=500, y=200)
    time.sleep(1)
    pyautogui.click(x=520, y=250)
    time.sleep(1)

# ================= Task quản lý thời gian thuê =================
def schedule_room_close(userId, rentalTime):
    def task():
        time.sleep(rentalTime * 60 * 60)  # giờ -> giây
        print(f"[INFO] Hết hạn thuê room của user {userId}")
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
    rentalId = data.get('rentalId')   # 👈 thêm rentalId

    if action == 'create_room':
        print(f"[INFO] Tạo room cho user {userId}")
        open_ldplayer()
        room_code = create_room_oslink()
        add_device_to_room()
        ROOMS[userId] = {'room_code': room_code}
        schedule_room_close(userId, rentalTime)

        # ==== PATCH roomCode vào BE NodeJS ====
        try:
            res = requests.patch(
                f"{BACKEND_API}/rentals/{rentalId}",
                json={'roomCode': room_code, 'status': 'active'}
            )
            print(f"[INFO] Đã PATCH roomCode={room_code} -> rentalId={rentalId}, status={res.status_code}")
        except Exception as e:
            print(f"[ERR] Không PATCH được roomCode: {e}")

        return jsonify({'status': 'ok', 'room_code': room_code})
    
    elif action == 'close_room':
        print(f"[INFO] Đóng room cho user {userId}")
        close_room_oslink()
        remove_device_ldplayer()
        ROOMS.pop(userId, None)
        return jsonify({'status': 'ok'})
    
    return jsonify({'status': 'unknown action'})


@app.route("/create-room", methods=["POST"])
def create_room():
    room_code = create_room_oslink()
    return jsonify({"room_code": room_code})

if __name__ == '__main__':
    app.run(port=6000)
