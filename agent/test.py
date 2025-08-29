import pyautogui


pos = pyautogui.locateCenterOnScreen("images/logooslink.png", confidence=0.8)
print("Found:", pos)
if pos:
    pyautogui.click(pos)