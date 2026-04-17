# ui/status_indicator.py
import tkinter as tk
from tkinter import ttk
import threading
import getpass

from PIL import Image, ImageDraw
import pystray

import config
from modules.ipc import make_client, send, recv


def _make_tray_icon(color: str) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, 60, 60), fill=color)
    return img


class LockOverlay:
    """Full-screen topmost window displayed while the workstation is locked."""

    def __init__(self) -> None:
        self._root: tk.Tk | None = None
        self._thread: threading.Thread | None = None

    def show(self) -> None:
        if self._root is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def hide(self) -> None:
        if self._root:
            self._root.after(0, self._root.destroy)
            self._root = None

    def _run(self) -> None:
        root = tk.Tk()
        self._root = root
        root.attributes("-fullscreen", True)
        root.attributes("-topmost", True)
        root.configure(bg="black")
        ttk.Label(root, text="🔒  FaceLock — Locked",
                  font=("Segoe UI", 28, "bold"),
                  foreground="white", background="black").pack(expand=True)
        ttk.Label(root, text="Look at the camera to unlock",
                  font=("Segoe UI", 14),
                  foreground="#aaaaaa", background="black").pack()
        root.mainloop()
        self._root = None


class StatusIndicator:
    """System tray icon that reflects the current lock state."""

    def __init__(self) -> None:
        self._overlay = LockOverlay()
        self._locked = False
        self._icon = pystray.Icon(
            "FaceLock",
            _make_tray_icon("green"),
            "FaceLock — Active",
            menu=pystray.Menu(
                pystray.MenuItem("Settings", self._open_settings),
                pystray.MenuItem("Enroll", self._open_enrollment),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit", self._quit),
            ),
        )

    def set_locked(self, locked: bool) -> None:
        self._locked = locked
        color = "red" if locked else "green"
        title = "FaceLock — Locked" if locked else "FaceLock — Active"
        self._icon.icon = _make_tray_icon(color)
        self._icon.title = title
        if locked:
            self._overlay.show()
        else:
            self._overlay.hide()

    def run(self) -> None:
        self._icon.run()

    def stop(self) -> None:
        self._icon.stop()

    # ------------------------------------------------------------------

    def _open_settings(self, icon, item) -> None:
        from ui.settings_window import launch as launch_settings
        threading.Thread(target=launch_settings, daemon=True).start()

    def _open_enrollment(self, icon, item) -> None:
        from ui.enrollment_window import launch as launch_enroll
        threading.Thread(target=launch_enroll, daemon=True).start()

    def _quit(self, icon, item) -> None:
        self._overlay.hide()
        icon.stop()


def launch() -> None:
    indicator = StatusIndicator()
    indicator.run()


if __name__ == "__main__":
    launch()
