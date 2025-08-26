# automation.py
import sys
import time
import random

def create_room(user_id: str):
    # Mô phỏng:
    # B1: Mở LdPlayer
    # B2: Vào 1 máy chủ
    # B3: Sao chép LdPlayer
    # B4: Cùng chơi -> tạo room -> add thiết bị
    # B5: Copy mã & trả về cho backend (in stdout)
    time.sleep(1.5)  # giả lập thời gian thao tác
    room_code = f"ROOM-{user_id}-{int(time.time())}-{random.randint(100,999)}"
    # Quan trọng: chỉ in mỗi room_code để server.js đọc stdout
    print(room_code)

def close_room(room_code: str):
    # Mô phỏng thao tác:
    # Vào "Cùng chơi" -> đúng room -> tắt máy -> xóa room nếu trống
    # Vào "LdPlayer" -> vào máy chủ chứa thiết bị -> Xóa Thiết Bị
    time.sleep(0.8)
    # Không cần in gì đặc biệt, exit code 0 là OK
    print(f"closed:{room_code}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Need action")
        sys.exit(1)

    action = sys.argv[1]
    if action == "create_room":
        if len(sys.argv) < 3:
          print("Need user_id")
          sys.exit(1)
        create_room(sys.argv[2])
        sys.exit(0)
    elif action == "close_room":
        if len(sys.argv) < 3:
          print("Need room_code")
          sys.exit(1)
        close_room(sys.argv[2])
        sys.exit(0)
    else:
        print("Unknown action")
        sys.exit(1)
