# ui/status_indicator.py
import subprocess
import tkinter as tk
from tkinter import ttk
import threading
import getpass
import json
import os
import signal
import time

import bcrypt
from PIL import Image, ImageDraw
import pystray

import config
from modules.database import get_user
from modules.ipc import make_client, send, recv


def _make_tray_icon(color: str) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, 60, 60), fill=color)
    return img


class LockOverlay:
    """Full-screen topmost overlay shown when FaceLock detects absence.

    Runs a face auth loop in a background thread and closes itself on success.
    If the user doesn't authenticate within the timeout, Mode A falls back to
    the Windows lock screen.
    """

    # Cycling dot frames for the scanning animation.
    _DOT_FRAMES = ["●○○", "○●○", "○○●", "○●○"]

    def __init__(self) -> None:
        self._root: tk.Tk | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._status_var: tk.StringVar | None = None
        self._dot_var: tk.StringVar | None = None
        self._dot_idx = 0

    def show(self, username: str) -> None:
        # Guard against showing twice if already visible.
        if self._root is not None:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run, args=(username,), daemon=True)
        self._thread.start()

    def hide(self) -> None:
        # Signal the auth loop to stop and destroy the window.
        self._running = False
        if self._root:
            self._root.after(0, self._root.destroy)
            self._root = None

    def _run(self, username: str) -> None:
        root = tk.Tk()
        self._root = root
        root.attributes("-fullscreen", True)
        root.attributes("-topmost", True)
        root.configure(bg="#0d0d0d")

        # Remove window decorations — eliminates title bar, taskbar entry,
        # and the WM_CLOSE message that Alt+F4 sends.
        root.overrideredirect(True)

        # Intercept common escape shortcuts and swallow them.
        for seq in ("<Alt-F4>", "<Escape>", "<Alt-Tab>", "<Super_L>", "<Super_R>"):
            root.bind(seq, lambda e: "break")

        # Prevent the OS from deleting the window via standard close requests.
        root.protocol("WM_DELETE_WINDOW", lambda: None)

        # Periodically re-raise and refocus to fight off any window that tries
        # to come to the foreground (e.g. notifications, other apps).
        def _keep_on_top() -> None:
            if self._root:
                root.lift()
                root.focus_force()
                root.after(500, _keep_on_top)
        root.after(500, _keep_on_top)

        center = tk.Frame(root, bg="#0d0d0d")
        center.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(center, text="🔒", font=("Segoe UI", 64),
                 bg="#0d0d0d", fg="white").pack(pady=(0, 8))
        tk.Label(center, text="FaceLock — Locked",
                 font=("Segoe UI", 32, "bold"),
                 bg="#0d0d0d", fg="white").pack()

        # Status text updated live by the auth loop thread.
        self._status_var = tk.StringVar(
            master=root, value="Look at the camera to unlock")
        tk.Label(center, textvariable=self._status_var,
                 font=("Segoe UI", 16),
                 bg="#0d0d0d", fg="#888888").pack(pady=(8, 0))

        # Animated scanning dots driven by _animate_dot().
        self._dot_var = tk.StringVar(master=root, value="●○○")
        tk.Label(center, textvariable=self._dot_var,
                 font=("Segoe UI", 14),
                 bg="#0d0d0d", fg="#1a73e8").pack(pady=(8, 0))

        # Show PIN fallback option only if the user enrolled with a PIN.
        user = get_user(config.DB_PATH, username)
        has_pin = (
            user is not None
            and user.get("fallback_method") == config.FALLBACK_PIN
            and user.get("pin_hash")
        )

        if has_pin:
            pin_hash: str = user["pin_hash"]

            # Divider above the PIN option.
            tk.Label(center, text="─" * 24,
                     bg="#0d0d0d", fg="#333333",
                     font=("Segoe UI", 9)).pack(pady=(20, 4))

            # "Use PIN instead" button — hidden once clicked.
            use_pin_btn = tk.Button(
                center, text="Use PIN instead",
                font=("Segoe UI", 10), bg="#0d0d0d", fg="#666666",
                relief="flat", cursor="hand2",
                activebackground="#0d0d0d", activeforeground="white",
            )
            use_pin_btn.pack()

            # PIN entry row + error label — hidden until button is clicked.
            pin_frame = tk.Frame(center, bg="#0d0d0d")
            pin_var = tk.StringVar(master=root)
            pin_status_var = tk.StringVar(master=root, value="")

            tk.Entry(pin_frame, textvariable=pin_var, show="*",
                     font=("Segoe UI", 14), width=10,
                     bg="#1a1a1a", fg="white", insertbackground="white",
                     relief="flat").pack(side="left", padx=(0, 8))

            tk.Button(
                pin_frame, text="Unlock",
                font=("Segoe UI", 10), bg="#1a73e8", fg="white",
                relief="flat", cursor="hand2",
                activebackground="#1558b0", activeforeground="white",
                command=lambda: _check_pin(),
            ).pack(side="left")

            tk.Label(center, textvariable=pin_status_var,
                     font=("Segoe UI", 9),
                     bg="#0d0d0d", fg="#cc4444").pack()

            def _show_pin_entry() -> None:
                # Swap the button for the entry field.
                use_pin_btn.pack_forget()
                pin_frame.pack(pady=(4, 0))

            def _check_pin() -> None:
                entered = pin_var.get().encode()
                if bcrypt.checkpw(entered, pin_hash.encode()):
                    # PIN correct — unlock core service and close overlay.
                    try:
                        c = make_client()
                        send(c, {"cmd": "unlock"})
                        recv(c)
                        c.close()
                    except Exception:
                        pass
                    root.after(0, root.destroy)
                else:
                    pin_status_var.set("Incorrect PIN — try again")
                    pin_var.set("")

            # Wire the button after the callbacks are defined.
            use_pin_btn.configure(command=_show_pin_entry)

            # Allow Enter key to submit the PIN.
            root.bind("<Return>", lambda e: _check_pin())

        tk.Label(center, text="FaceLock  •  GDPR compliant",
                 font=("Segoe UI", 9),
                 bg="#0d0d0d", fg="#444444").pack(pady=(32, 0))

        # Start the background face auth loop.
        threading.Thread(
            target=self._auth_loop, args=(username,), daemon=True).start()

        # Kick off the dot animation via tkinter's event loop.
        root.after(400, self._animate_dot)

        root.mainloop()

        # Clean up after window closes (auth success or hide() called).
        self._running = False
        self._root = None
        self._status_var = None
        self._dot_var = None

    def _animate_dot(self) -> None:
        # Advance the dot frame and reschedule — runs on the tkinter thread.
        if self._root is None:
            return
        if self._dot_var:
            self._dot_var.set(self._DOT_FRAMES[self._dot_idx % len(self._DOT_FRAMES)])
        self._dot_idx += 1
        self._root.after(400, self._animate_dot)

    def _set_status(self, text: str) -> None:
        # Thread-safe status update: schedules the StringVar write on the tk thread.
        try:
            if self._root and self._status_var:
                self._root.after(
                    0, lambda t=text: self._status_var.set(t) if self._status_var else None)
        except Exception:
            pass

    def _auth_loop(self, username: str) -> None:
        # Continuously attempts face auth while the overlay is visible.
        # On success, sends unlock IPC and closes the overlay.
        # On timeout or repeated failure, Mode A will call LockWorkStation().
        while self._running:
            try:
                conn = make_client()
                send(conn, {"cmd": "auth", "username": username})
                result = recv(conn)
                conn.close()

                if result.get("ok"):
                    # Auth succeeded — tell core service to clear locked state.
                    try:
                        c = make_client()
                        send(c, {"cmd": "unlock"})
                        recv(c)
                        c.close()
                    except Exception:
                        pass
                    # Close the overlay window from the tkinter thread.
                    if self._root:
                        self._root.after(0, self._root.destroy)
                    return

                # Auth timed out without a match — loop and try again.
                self._set_status("Scanning... look directly at the camera")

            except Exception:
                # Core service unreachable — wait before retrying.
                self._set_status("Connecting to service...")
                time.sleep(2)


class StatusIndicator:
    """System tray icon that reflects the current lock/pause state."""

    def __init__(self) -> None:
        self._overlay = LockOverlay()
        self._locked = False
        self._paused = False
        self._username = getpass.getuser()
        self._dashboard_thread: threading.Thread | None = None
        self._icon = pystray.Icon(
            "FaceLock",
            _make_tray_icon("green"),
            "FaceLock — Active",
            menu=pystray.Menu(
                pystray.MenuItem("Open Dashboard", self._open_dashboard, default=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Settings", self._open_settings),
                pystray.MenuItem("Enroll", self._open_enrollment),
                pystray.MenuItem("Debug View", self._open_debug),
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
        # Guard: skip if state hasn't changed to avoid redundant overlay toggles.
        if locked == self._locked:
            return
        self._locked = locked
        self._refresh_icon()
        if locked:
            self._overlay.show(self._username)
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
        # Start the lock-state polling thread before the tray event loop.
        threading.Thread(target=self._poll_lock_state, daemon=True).start()
        self._icon.run()

    def stop(self) -> None:
        self._icon.stop()

    def _poll_lock_state(self) -> None:
        # Polls the core service every second to sync locked/paused state.
        # This is how the tray learns that Mode A triggered a lock — they run
        # in separate processes and communicate only through the core service.
        while True:
            try:
                conn = make_client()
                send(conn, {"cmd": "status"})
                result = recv(conn)
                conn.close()

                # Sync locked state — triggers overlay show/hide if changed.
                self.set_locked(result.get("locked", False))

                # Sync paused state — update icon if changed.
                paused = result.get("paused", False)
                if paused != self._paused:
                    self._paused = paused
                    self._refresh_icon()

            except Exception:
                pass  # Core service not yet ready or temporarily unreachable.

            time.sleep(1)

    # ------------------------------------------------------------------

    def _open_dashboard(self, icon=None, item=None) -> None:
        if self._dashboard_thread and self._dashboard_thread.is_alive():
            return
        from ui.dashboard import launch as launch_dashboard
        self._dashboard_thread = threading.Thread(
            target=launch_dashboard,
            args=(
                self._locked, self._paused,
                self._toggle_pause_from_dashboard,
                lambda: self._quit(self._icon, None),
                lambda: threading.Thread(target=self._do_open_settings, daemon=True).start(),
                lambda: threading.Thread(target=self._do_open_enroll, daemon=True).start(),
                lambda: threading.Thread(target=self._do_open_debug, daemon=True).start(),
            ),
            daemon=True,
        )
        self._dashboard_thread.start()

    def _toggle_pause_from_dashboard(self) -> None:
        self._paused = not self._paused
        self._send_ipc({"cmd": "pause" if self._paused else "resume"})
        self._refresh_icon()

    def _do_open_settings(self) -> None:
        from ui.settings_window import launch as launch_settings
        launch_settings()

    def _do_open_enroll(self) -> None:
        from ui.enrollment_window import launch as launch_enroll
        launch_enroll()

    def _do_open_debug(self) -> None:
        # Launch debug view as a separate process to avoid Tcl thread conflicts.
        import sys
        from pathlib import Path
        main_py = Path(__file__).parent.parent / "main.py"
        subprocess.Popen([sys.executable, str(main_py), "debug"])

    def _open_settings(self, icon, item) -> None:
        threading.Thread(target=self._do_open_settings, daemon=True).start()

    def _open_enrollment(self, icon, item) -> None:
        threading.Thread(target=self._do_open_enroll, daemon=True).start()

    def _open_debug(self, icon, item) -> None:
        threading.Thread(target=self._do_open_debug, daemon=True).start()

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
        # Clean up lock state in core service before exiting.
        self._send_ipc({"cmd": "unlock"})
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
