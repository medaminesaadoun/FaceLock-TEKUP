# ui/status_indicator.py
import tkinter as tk
from tkinter import ttk
import threading
import getpass
import json
import os
import signal

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
        root.configure(bg="#0d0d0d")

        center = tk.Frame(root, bg="#0d0d0d")
        center.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(center, text="🔒", font=("Segoe UI", 64),
                 bg="#0d0d0d", fg="white").pack(pady=(0, 8))
        tk.Label(center, text="FaceLock — Locked",
                 font=("Segoe UI", 32, "bold"),
                 bg="#0d0d0d", fg="white").pack()
        tk.Label(center, text="Look at the camera to unlock",
                 font=("Segoe UI", 16),
                 bg="#0d0d0d", fg="#888888").pack(pady=(8, 0))
        tk.Label(center, text="FaceLock  •  GDPR compliant",
                 font=("Segoe UI", 9),
                 bg="#0d0d0d", fg="#444444").pack(pady=(32, 0))

        root.mainloop()
        self._root = None


class StatusIndicator:
    """System tray icon that reflects the current lock state."""

    def __init__(self) -> None:
        self._overlay = LockOverlay()
        self._locked = False
        self._paused = False
        self._icon = pystray.Icon(
            "FaceLock",
            _make_tray_icon("green"),
            "FaceLock — Active",
            menu=pystray.Menu(
                pystray.MenuItem("Settings", self._open_settings),
                pystray.MenuItem("Enroll", self._open_enrollment),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    lambda item: "Resume" if self._paused else "Pause",
                    self._toggle_pause,
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit", self._quit),
            ),
        )

    def set_locked(self, locked: bool) -> None:
        self._locked = locked
        self._refresh_icon()
        if locked:
            self._overlay.show()
        else:
            self._overlay.hide()

    def _refresh_icon(self) -> None:
        if self._paused:
            color, title = "yellow", "FaceLock — Paused"
        elif self._locked:
            color, title = "red", "FaceLock — Locked"
        else:
            color, title = "green", "FaceLock — Active"
        self._icon.icon = _make_tray_icon(color)
        self._icon.title = title

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

    def _toggle_pause(self, icon, item) -> None:
        self._paused = not self._paused
        self._send_ipc({"cmd": "pause" if self._paused else "resume"})
        self._refresh_icon()

    def _send_ipc(self, msg: dict) -> None:
        try:
            conn = make_client()
            send(conn, msg)
            recv(conn)
            conn.close()
        except Exception:
            pass

    def _quit(self, icon, item) -> None:
        self._overlay.hide()
        self._kill_subprocesses()
        icon.stop()

    def _kill_subprocesses(self) -> None:
        pid_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "pids.json")
        try:
            pids = json.loads(open(pid_path).read())
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass
            os.remove(pid_path)
        except (FileNotFoundError, Exception):
            pass


def launch() -> None:
    indicator = StatusIndicator()
    indicator.run()


if __name__ == "__main__":
    launch()
