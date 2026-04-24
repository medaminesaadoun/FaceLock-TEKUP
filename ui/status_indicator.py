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
from datetime import datetime

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
        from modules.user_settings import load as load_settings

        # Fetch PIN info and hidden mode setting before building UI.
        settings = load_settings(config.SETTINGS_PATH)
        hidden_mode = settings.get("hidden_mode", False)

        user = get_user(config.DB_PATH, username)
        has_pin = (
            user is not None
            and user.get("fallback_method") == config.FALLBACK_PIN
            and user.get("pin_hash")
        )
        pin_hash: str | None = user["pin_hash"] if has_pin else None

        root = tk.Tk()
        self._root = root
        root.attributes("-fullscreen", True)
        root.attributes("-topmost", True)

        # Remove window decorations — eliminates title bar, taskbar entry,
        # and the WM_CLOSE message that Alt+F4 sends.
        root.overrideredirect(True)

        # Intercept common escape shortcuts and swallow them.
        for seq in ("<Alt-F4>", "<Escape>", "<Alt-Tab>", "<Super_L>", "<Super_R>"):
            root.bind(seq, lambda e: "break")

        # Prevent the OS from deleting the window via standard close requests.
        root.protocol("WM_DELETE_WINDOW", lambda: None)

        # Periodically re-raise to fight off windows trying to come to the
        # foreground. Only steal keyboard focus if no child widget has it —
        # calling focus_force() unconditionally breaks PIN entry fields.
        def _keep_on_top() -> None:
            if self._root:
                root.lift()
                if root.focus_get() in (None, root):
                    root.focus_force()
                root.after(500, _keep_on_top)
        root.after(500, _keep_on_top)

        if hidden_mode:
            # Disguise as Windows lock screen — no FaceLock branding visible.
            # Fall back to normal mode if anything goes wrong building the UI.
            try:
                self._build_hidden_ui(root, username, has_pin, pin_hash)
            except Exception as e:
                import logging
                logging.getLogger("facelock.audit").error(
                    "hidden mode UI failed, falling back: %s", e)
                hidden_mode = False
        if not hidden_mode:
            # Standard FaceLock overlay with branding and visible status.
            root.configure(bg="#0d0d0d")
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

            if has_pin:
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
                    use_pin_btn.pack_forget()
                    pin_frame.pack(pady=(4, 0))

                def _check_pin() -> None:
                    entered = pin_var.get().encode()
                    if bcrypt.checkpw(entered, pin_hash.encode()):
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

                use_pin_btn.configure(command=_show_pin_entry)
                root.bind("<Return>", lambda e: _check_pin())

            tk.Label(center, text="FaceLock  •  GDPR compliant",
                     font=("Segoe UI", 9),
                     bg="#0d0d0d", fg="#444444").pack(pady=(32, 0))

            # Start dot animation only in normal mode.
            root.after(400, self._animate_dot)

        # Auth loop is the same regardless of display mode.
        threading.Thread(
            target=self._auth_loop, args=(username,), daemon=True).start()

        root.mainloop()

        # Clean up after window closes (auth success or hide() called).
        self._running = False
        self._root = None
        self._status_var = None
        self._dot_var = None

    @staticmethod
    def _load_lock_screen_image(w: int, h: int) -> bytes | None:
        """Try to find the Windows lock screen image and return it as PNG bytes.

        Returns raw PNG bytes rather than ImageTk.PhotoImage to avoid PIL's
        Tk bridge, which has global state that causes Tcl_AsyncDelete crashes
        when used across threads. The caller creates tk.PhotoImage(data=b64)
        instead, which is fully bound to the local Tcl interpreter.

        Checks Windows Spotlight assets, registry lock image path, then
        default Windows wallpapers. Returns None if nothing usable is found.
        """
        import glob
        import io
        import winreg
        from PIL import ImageFilter, ImageEnhance

        candidates: list[str] = []

        # Windows Spotlight stores lock screen images without file extensions
        # in the ContentDeliveryManager assets folder. The largest files are
        # the landscape lock screen images (usually > 200 KB).
        spotlight_dir = os.path.expandvars(
            r"%LOCALAPPDATA%\Packages"
            r"\Microsoft.Windows.ContentDeliveryManager_cw5n1h2txyewy"
            r"\LocalState\Assets"
        )
        if os.path.isdir(spotlight_dir):
            files = []
            for f in glob.glob(os.path.join(spotlight_dir, "*")):
                try:
                    if os.path.isfile(f) and os.path.getsize(f) > 200_000:
                        files.append((os.path.getsize(f), f))
                except OSError:
                    continue
            files.sort(reverse=True)
            candidates.extend(f for _, f in files[:5])

        # Registry key for custom lock screen wallpaper.
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\Lock Screen\Creative",
            )
            path, _ = winreg.QueryValueEx(key, "LockImagePath")
            if os.path.isfile(path):
                candidates.insert(0, path)
        except Exception:
            pass

        # Default Windows lock screen images.
        for path in [
            r"C:\Windows\Web\Screen\img100.jpg",
            r"C:\Windows\Web\Screen\img104.jpg",
            r"C:\Windows\Web\4K\Wallpaper\Windows\img0_1920x1200.jpg",
        ]:
            if os.path.isfile(path):
                candidates.append(path)

        for path in candidates:
            try:
                img = Image.open(path).convert("RGB")
                # Skip files that aren't real images (e.g. metadata blobs).
                if img.width < 400 or img.height < 300:
                    continue
                img = img.resize((w, h), Image.LANCZOS)
                # Darken and slightly blur to match Windows lock screen look.
                img = ImageEnhance.Brightness(img).enhance(0.45)
                img = img.filter(ImageFilter.GaussianBlur(radius=4))
                # Return raw PNG bytes — caller uses tk.PhotoImage(data=b64)
                # to avoid PIL's Tk bridge and its threading issues.
                buf = io.BytesIO()
                img.save(buf, format="PNG", compress_level=1)
                return buf.getvalue()
            except Exception:
                continue

        return None

    def _build_hidden_ui(self, root: tk.Tk, username: str,
                         has_pin: bool, pin_hash: str | None) -> None:
        """Renders a Windows lock screen clone — clock, date, optional PIN entry.

        Uses a Canvas so text and widgets render cleanly over the background
        image without needing transparent widget backgrounds (tkinter limitation).
        Face auth runs silently. PIN entry appears on first keypress.
        """
        w = root.winfo_screenwidth()
        h = root.winfo_screenheight()

        # Canvas fills the entire screen — all content is drawn on it.
        canvas = tk.Canvas(root, bg="#1a1a2e", highlightthickness=0)
        canvas.pack(fill="both", expand=True)

        # Try to use the real Windows lock screen image as background.
        # Use tk.PhotoImage(data=b64) — not ImageTk.PhotoImage — so the image
        # is bound to this thread's Tcl interpreter and avoids PIL's global
        # Tk bridge which causes Tcl_AsyncDelete crashes across threads.
        raw_png = self._load_lock_screen_image(w, h)
        if raw_png:
            import base64
            b64 = base64.b64encode(raw_png).decode("ascii")
            bg_photo = tk.PhotoImage(master=root, data=b64)
            canvas.create_image(0, 0, anchor="nw", image=bg_photo)
            canvas._bg_photo = bg_photo  # hold reference to prevent GC

        # Clock text drawn directly on canvas — no background color conflict.
        cy = int(h * 0.38)
        time_id = canvas.create_text(
            w // 2, cy, text="00:00",
            font=("Segoe UI Light", 80), fill="white", anchor="center")
        date_id = canvas.create_text(
            w // 2, cy + 96, text="",
            font=("Segoe UI Light", 22), fill="white", anchor="center")

        def _update_clock() -> None:
            # Refresh clock every second via tkinter's event loop.
            if not self._root:
                return
            now = datetime.now()
            canvas.itemconfig(time_id, text=now.strftime("%H:%M"))
            canvas.itemconfig(date_id, text=now.strftime("%A, %B %d"))
            root.after(1000, _update_clock)

        _update_clock()

        if has_pin:
            # Outer frame sits on the canvas via create_window.
            # Uses the background color so it blends with the lock screen image.
            pin_outer = tk.Frame(canvas, bg="#1a1a2e")

            # Username label above the field, like Windows lock screen.
            tk.Label(pin_outer, text=username,
                     font=("Segoe UI", 12), bg="#1a1a2e",
                     fg="#cccccc").pack(pady=(0, 8))

            # White field container with a thin border — matches the screenshot.
            field_box = tk.Frame(
                pin_outer, bg="white",
                highlightbackground="#8a9cc0",
                highlightthickness=1,
            )
            field_box.pack()

            pin_var = tk.StringVar(master=root)
            pin_entry = tk.Entry(
                field_box, textvariable=pin_var,
                show="●",                         # filled circle dots
                font=("Segoe UI", 14), width=18,
                bg="white", fg="#1a1a2a",
                insertbackground="#1a1a2a",
                relief="flat", bd=0,
            )
            pin_entry.pack(side="left", padx=(12, 4), pady=8, ipady=2)

            # Eye button toggles show/hide — matches the ⊙ icon in the image.
            _showing = tk.BooleanVar(master=root, value=False)

            def _toggle_reveal() -> None:
                if _showing.get():
                    pin_entry.configure(show="●")
                    _showing.set(False)
                else:
                    pin_entry.configure(show="")
                    _showing.set(True)

            tk.Button(
                field_box, text="⊙",
                font=("Segoe UI", 12), bg="white", fg="#666666",
                relief="flat", bd=0, cursor="hand2",
                activebackground="white", activeforeground="#333333",
                command=_toggle_reveal,
            ).pack(side="right", padx=(4, 10))

            # Error label and submit button below the field.
            pin_status_var = tk.StringVar(master=root, value="")
            tk.Label(pin_outer, textvariable=pin_status_var,
                     font=("Segoe UI", 9), bg="#1a1a2e",
                     fg="#ff6666").pack(pady=(4, 0))

            tk.Button(
                pin_outer, text="Sign in  →",
                font=("Segoe UI", 10), bg="#0067c0", fg="white",
                relief="flat", cursor="hand2", padx=16, pady=4,
                activebackground="#0053a0", activeforeground="white",
                command=lambda: _check_pin(),
            ).pack(pady=(10, 0))

            # Place on canvas, visible immediately — no keypress needed.
            canvas.create_window(
                w // 2, int(h * 0.65),
                window=pin_outer, anchor="center")

            def _check_pin() -> None:
                entered = pin_var.get().encode()
                if bcrypt.checkpw(entered, pin_hash.encode()):
                    try:
                        c = make_client()
                        send(c, {"cmd": "unlock"})
                        recv(c)
                        c.close()
                    except Exception:
                        pass
                    root.after(0, root.destroy)
                else:
                    pin_status_var.set("Incorrect PIN")
                    pin_var.set("")

            # Auto-focus the entry and allow Enter to submit.
            root.after(100, pin_entry.focus_set)
            root.bind("<Return>", lambda e: _check_pin())

        # Tiny indicator dot — only visible cue that face auth is running.
        canvas.create_text(
            w - 10, h - 10, text="●",
            font=("Segoe UI", 8), fill="#1a73e8", anchor="se")

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
