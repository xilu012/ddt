from __future__ import annotations

import ctypes
import sys
import time

from .engine import AutomationError


def enable_dpi_awareness() -> None:
    if sys.platform == "win32":
        # Must happen before importing GUI libraries or reading screen coordinates.
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (OSError, AttributeError):
            ctypes.windll.user32.SetProcessDPIAware()


class WindowsDesktop:
    def __init__(self):
        if sys.platform != "win32":
            raise AutomationError("实机录制和重放需要 Windows 桌面；check 和单元测试可在 Linux 运行。")
        enable_dpi_awareness()
        import pyautogui
        self.gui = pyautogui
        self.gui.FAILSAFE = True
        self.gui.PAUSE = 0.15

    def screenshot(self):
        return self.gui.screenshot()

    def click(self, x, y):
        self.check_stop()
        self.gui.click(x, y)

    def title(self):
        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
        buffer = ctypes.create_unicode_buffer(2048)
        user32.GetWindowTextW(user32.GetForegroundWindow(), buffer, len(buffer))
        return buffer.value

    def check_stop(self):
        if ctypes.windll.user32.GetAsyncKeyState(0x1B) & 0x8000:
            raise AutomationError("已按 Esc 停止。")
        self.gui.failSafeCheck()


def countdown(seconds: int = 5) -> None:
    print(f"请在 {seconds} 秒内切回游戏窗口；Esc 或将鼠标移至主屏幕角落可停止。", flush=True)
    for _ in range(seconds):
        time.sleep(1)


def select_box(screen, title: str) -> list[int]:
    """Select on a static screenshot; never pass recording clicks into the game."""
    import tkinter as tk
    from PIL import ImageTk

    root = tk.Tk()
    root.title(title + "（拖框后回车确认；Esc 取消）")
    scale = min(1, (root.winfo_screenwidth() - 100) / screen.width,
                (root.winfo_screenheight() - 150) / screen.height)
    preview = screen.resize((int(screen.width * scale), int(screen.height * scale)))
    photo = ImageTk.PhotoImage(preview)
    canvas = tk.Canvas(root, width=preview.width, height=preview.height, highlightthickness=0)
    canvas.pack()
    canvas.create_image(0, 0, anchor="nw", image=photo)
    start, selected = [], []
    rectangle = None

    def begin(event):
        nonlocal rectangle
        selected.clear()
        start[:] = [event.x, event.y]
        if rectangle is not None:
            canvas.delete(rectangle)
        rectangle = canvas.create_rectangle(event.x, event.y, event.x, event.y,
                                            outline="red", width=2)

    def drag(event):
        if start:
            canvas.coords(rectangle, *start, event.x, event.y)

    def finish(event):
        if not start:
            return
        x1, x2 = sorted((start[0], event.x))
        y1, y2 = sorted((start[1], event.y))
        x1, x2 = max(0, round(x1 / scale)), min(screen.width, round(x2 / scale))
        y1, y2 = max(0, round(y1 / scale)), min(screen.height, round(y2 / scale))
        selected[:] = [x1, y1, x2 - x1, y2 - y1]

    def accept(_event):
        if selected and min(selected[2:]) >= 12:
            root.destroy()

    def cancel(_event):
        selected.clear()
        root.destroy()

    canvas.bind("<ButtonPress-1>", begin)
    canvas.bind("<B1-Motion>", drag)
    canvas.bind("<ButtonRelease-1>", finish)
    root.bind("<Return>", accept)
    root.bind("<Escape>", cancel)
    root.protocol("WM_DELETE_WINDOW", lambda: cancel(None))
    root.mainloop()
    if not selected:
        raise AutomationError("已取消选框。")
    return selected
