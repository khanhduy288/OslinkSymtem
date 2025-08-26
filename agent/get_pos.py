import pyautogui
import time

print("Di chuột tới vị trí cần lấy trong 5 giây...")
time.sleep(5)

x, y = pyautogui.position()
print(f"Tọa độ hiện tại: x={x}, y={y}")
