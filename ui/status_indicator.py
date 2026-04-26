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
import ctypes
import ctypes.wintypes
from datetime import datetime

import bcrypt
from PIL import Image, ImageDraw
import pystray

import config
from modules.database import get_user
from modules.ipc import make_client, send, recv


# ---------------------------------------------------------------------------
# Low-level keyboard hook — blocks Alt+Tab and Win key at the OS level so
# they cannot switch away from the lock overlay.  Must be installed and
# uninstalled from the same thread that runs a Windows message loop (tkinter
# mainloop qualifies).
# ---------------------------------------------------------------------------

_WH_KEYBOARD_LL = 13
_WM_KEYDOWN    = 0x0100
_WM_SYSKEYDOWN = 0x0104
_VK_BLOCK = {0x09, 0x5B, 0x5C}  # Tab, LWin, RWin

# LRESULT is a pointer-sized signed integer (32-bit on x86, 64-bit on x64).
# Using c_ssize_t ensures the correct size on all platforms.
_LRESULT = ctypes.c_ssize_t

# Declare argtypes/restype on CallNextHookEx so ctypes marshals the 64-bit
# lParam correctly — without this, Python overflows when converting a large
# pointer value to a plain c_int on 64-bit Windows.
_call_next = ctypes.windll.user32.CallNextHookEx
_call_next.restype  = _LRESULT
_call_next.argtypes = [
    ctypes.c_void_p,        # hhk  (can be None)
    ctypes.c_int,           # nCode
    ctypes.wintypes.WPARAM, # wParam
    ctypes.wintypes.LPARAM, # lParam
]

_HOOKPROC = ctypes.WINFUNCTYPE(
    _LRESULT, ctypes.c_int,
    ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode",      ctypes.wintypes.DWORD),
        ("scanCode",    ctypes.wintypes.DWORD),
        ("flags",       ctypes.wintypes.DWORD),
        ("time",        ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


def _install_kb_hook():
    """Install WH_KEYBOARD_LL hook; return (hook_handle, callback_ref)."""
    def _handler(nCode, wParam, lParam):
        if nCode >= 0 and wParam in (_WM_KEYDOWN, _WM_SYSKEYDOWN):
            kb = ctypes.cast(lParam, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
            if kb.vkCode in _VK_BLOCK:
                return _LRESULT(1)  # swallow — do not pass to next hook
        return _call_next(None, nCode, wParam, lParam)

    fn   = _HOOKPROC(_handler)
    hook = ctypes.windll.user32.SetWindowsHookExW(_WH_KEYBOARD_LL, fn, None, 0)
    return hook, fn  # fn must stay alive to prevent GC


def _uninstall_kb_hook(hook) -> None:
    if hook:
        ctypes.windll.user32.UnhookWindowsHookEx(hook)


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
        self._bg_photo = None  # tk.PhotoImage for hidden mode background

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

        # Install a low-level keyboard hook to block Alt+Tab and the Win key.
        # tkinter bindings only intercept events the app receives — shell-level
        # shortcuts like Alt+Tab bypass them entirely. WH_KEYBOARD_LL intercepts
        # keystrokes before any application sees them.
        _kb_hook, _kb_fn = _install_kb_hook()
        try:
            root.mainloop()
        finally:
            _uninstall_kb_hook(_kb_hook)

        # Clean up after window closes (auth success or hide() called).
        self._running = False
        self._root = None
        self._status_var = None
        self._dot_var = None
        self._bg_photo = None  # <Destroy> binding already deleted it from Tcl

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
            # Store on self so we control when it's deleted. A <Destroy>
            # binding below deletes it from the correct thread before the
            # Tcl interpreter tears down, preventing the __del__ crash.
            self._bg_photo = bg_photo

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

        # Delete the background PhotoImage from inside the Tcl event loop
        # (i.e. from the correct thread) before the interpreter tears down.
        # Without this, Python's GC calls __del__ from the main thread later,
        # which causes the "main thread is not in main loop" crash.
        def _on_destroy(e: tk.Event) -> None:
            if e.widget is root and self._bg_photo is not None:
                try:
                    self._bg_photo.tk.call("image", "delete", self._bg_photo.name)
                    self._bg_photo.name = None  # disarm __del__
                except Exception:
                    pass
                self._bg_photo = None

        root.bind("<Destroy>", _on_destroy)

        if has_pin:
            # PIN area rendered entirely on the canvas — no Frame wrapper, so
            # there's no solid background rectangle overlaid on the wallpaper.
            FIELD_W, FIELD_H = 300, 44
            PIN_Y = int(h * 0.64)

            # Username as canvas text — floats on the wallpaper with no bg box.
            uid = canvas.create_text(
                w // 2, PIN_Y - 60, text=username,
                font=("Segoe UI Light", 15), fill="white",
                anchor="center", state="hidden")

            # White field rectangle drawn directly on canvas.
            fid = canvas.create_rectangle(
                w // 2 - FIELD_W // 2, PIN_Y - FIELD_H // 2,
                w // 2 + FIELD_W // 2, PIN_Y + FIELD_H // 2,
                fill="white", outline="#c8c8c8", width=1, state="hidden")

            # Entry embedded on top of the white rectangle.
            pin_var = tk.StringVar(master=root)
            pin_entry = tk.Entry(
                canvas, textvariable=pin_var, show="●",
                font=("Segoe UI", 13), bd=0, relief="flat",
                bg="white", fg="#1a1a2a", insertbackground="#1a1a2a",
            )
            pew = canvas.create_window(
                w // 2 - 18, PIN_Y,
                window=pin_entry, anchor="center",
                width=FIELD_W - 54, height=FIELD_H - 12, state="hidden")

            # Eye button inside the field on the right.
            def _toggle_reveal() -> None:
                pin_entry.configure(
                    show="" if pin_entry.cget("show") == "●" else "●")

            eye_btn = tk.Button(
                canvas, text="⊙", font=("Segoe UI", 11),
                bg="white", fg="#888888", bd=0, relief="flat",
                activebackground="white", activeforeground="#444444",
                cursor="hand2", command=_toggle_reveal)
            ebw = canvas.create_window(
                w // 2 + FIELD_W // 2 - 20, PIN_Y,
                window=eye_btn, anchor="center", state="hidden")

            # Arrow submit button to the right of the field — Windows style.
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
                    # Show error text below the field.
                    canvas.itemconfig(eid, state="normal",
                                      text="Incorrect PIN")
                    pin_var.set("")

            arrow_btn = tk.Button(
                canvas, text="→", font=("Segoe UI", 14),
                bg="#0067c0", fg="white", bd=0, relief="flat",
                activebackground="#0053a0", cursor="hand2",
                command=_check_pin)
            abw = canvas.create_window(
                w // 2 + FIELD_W // 2 + 28, PIN_Y,
                window=arrow_btn, anchor="center",
                width=44, height=FIELD_H, state="hidden")

            # Error message as canvas text — no background box.
            eid = canvas.create_text(
                w // 2, PIN_Y + FIELD_H // 2 + 18, text="",
                font=("Segoe UI", 9), fill="#ff6666",
                anchor="center", state="hidden")

            # Collect all items to show/hide together.
            _pin_items = [uid, fid, pew, ebw, abw]

            def _reveal_pin(e=None) -> None:
                # Unbind so this only fires once.
                root.unbind("<KeyPress>")
                canvas.unbind("<Button-1>")
                for item in _pin_items:
                    canvas.itemconfig(item, state="normal")
                # Show error slot (empty text, becomes visible on wrong PIN).
                canvas.itemconfig(eid, state="normal")
                pin_entry.focus_set()

            # Reveal on any click or keypress — mirrors Windows lock screen.
            canvas.bind("<Button-1>", lambda e: _reveal_pin())
            root.bind("<KeyPress>", lambda e: _reveal_pin())
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
                        # Null self._root before scheduling destroy so that a
                        # concurrent hide() call doesn't try to destroy twice.
                        root_ref = self._root
                        self._root = None
                        root_ref.after(0, root_ref.destroy)
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
        self._dashboard_app = None        # live Dashboard tk.Tk
        self._dashboard_ready = threading.Event()  # set once app is assigned
        self._settings_thread: threading.Thread | None = None
        self._settings_app = None   # live SettingsWindow tk.Tk
        self._enroll_thread: threading.Thread | None = None
        self._enroll_app = None     # live EnrollmentWindow tk.Tk
        self._icon = pystray.Icon(
            "FaceLock",
            _make_tray_icon("green"),
            "FaceLock — Active",
            menu=pystray.Menu(
                pystray.MenuItem("Open Dashboard", self._open_dashboard, default=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Settings", self._open_settings),
                pystray.MenuItem("Re-enroll", self._open_enrollment),
                pystray.MenuItem("Add User", self._open_add_user),
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
            # Close the dashboard before showing the overlay — having two
            # tk.Tk() instances in different threads causes Tcl_AsyncDelete.
            self._close_all_windows()
            self._overlay.show(self._username)
        else:
            self._overlay.hide()

    def _close_all_windows(self) -> None:
        """Close every secondary tk.Tk window and wait for their threads.

        Called before showing the overlay — having two tk.Tk interpreters
        alive in different threads causes Tcl_AsyncDelete crashes.
        """
        # If the dashboard thread is alive but _dashboard_app is not yet set,
        # wait for it — there is a brief race between Dashboard() construction
        # and the assignment of _dashboard_app. Without this wait, the overlay
        # could create its own Tcl interpreter while Dashboard's is mid-init.
        if (self._dashboard_thread and self._dashboard_thread.is_alive()
                and self._dashboard_app is None):
            self._dashboard_ready.wait(timeout=2.0)

        pairs = [
            ("_dashboard_app", "_dashboard_thread"),
            ("_settings_app",  "_settings_thread"),
            ("_enroll_app",    "_enroll_thread"),
        ]
        for app_attr, thread_attr in pairs:
            app = getattr(self, app_attr, None)
            if app is not None:
                setattr(self, app_attr, None)
                try:
                    app.after(0, app.destroy)
                except Exception:
                    pass
        # Wait for all threads so their interpreters are fully torn down.
        # Skip the current thread to avoid deadlock when called from within
        # a secondary window thread (e.g. settings closing dashboard).
        current = threading.current_thread()
        for _, thread_attr in pairs:
            t = getattr(self, thread_attr, None)
            if t and t.is_alive() and t is not current:
                t.join(timeout=1.5)

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

        def _run() -> None:
            self._dashboard_ready.clear()
            self._close_all_windows()
            from ui.dashboard import Dashboard
            app = Dashboard(
                self._locked, self._paused,
                self._toggle_pause_from_dashboard,
                lambda: self._quit(self._icon, None),
                lambda: self._open_settings(None, None),
                lambda: self._open_enrollment(None, None),
                lambda: self._open_add_user(None, None),
                lambda: threading.Thread(target=self._do_open_debug, daemon=True).start(),
            )
            # Signal that the app is ready — _close_all_windows() waits for
            # this before trying to call app.after(0, destroy) on the dashboard.
            self._dashboard_app = app
            self._dashboard_ready.set()
            app.mainloop()
            self._dashboard_app = None
            self._dashboard_ready.clear()

        self._dashboard_thread = threading.Thread(target=_run, daemon=True)
        self._dashboard_thread.start()

    def _toggle_pause_from_dashboard(self) -> None:
        self._paused = not self._paused
        self._send_ipc({"cmd": "pause" if self._paused else "resume"})
        self._refresh_icon()

    def _do_open_settings(self) -> None:
        # Close any other open secondary window first — two tk.Tk interpreters
        # in different threads cause Tcl_AsyncDelete crashes.
        self._close_all_windows()
        from ui.settings_window import SettingsWindow
        # Pass enrollment callback so re-enroll goes through the tracked path.
        app = SettingsWindow(on_re_enroll=lambda: self._open_enrollment(None, None))
        self._settings_app = app
        app.mainloop()
        self._settings_app = None

    def _do_open_enroll(self) -> None:
        self._close_all_windows()
        from ui.enrollment_window import EnrollmentWindow
        app = EnrollmentWindow(mode="enroll")
        self._enroll_app = app
        app.mainloop()
        self._enroll_app = None

    def _do_open_add_user(self) -> None:
        self._close_all_windows()
        from modules.gdpr import has_consent
        from ui.enrollment_window import EnrollmentWindow
        # Add User requires an existing enrollment — redirect to full enroll if not enrolled.
        mode = "add_user" if has_consent(config.DB_PATH, self._username) else "enroll"
        app = EnrollmentWindow(mode=mode)
        self._enroll_app = app
        app.mainloop()
        self._enroll_app = None

    def _do_open_debug(self) -> None:
        # Launch debug view as a separate process to avoid Tcl thread conflicts.
        import sys
        from pathlib import Path
        main_py = Path(__file__).parent.parent / "main.py"
        subprocess.Popen([sys.executable, str(main_py), "debug"])

    def _open_settings(self, icon, item) -> None:
        t = threading.Thread(target=self._do_open_settings, daemon=True)
        self._settings_thread = t
        t.start()

    def _open_enrollment(self, icon, item) -> None:
        t = threading.Thread(target=self._do_open_enroll, daemon=True)
        self._enroll_thread = t
        t.start()

    def _open_add_user(self, icon=None, item=None) -> None:
        t = threading.Thread(target=self._do_open_add_user, daemon=True)
        self._enroll_thread = t
        t.start()

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
