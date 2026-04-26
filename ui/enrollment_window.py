# ui/enrollment_window.py
import io
import tkinter as tk
from tkinter import ttk, messagebox
import threading
import queue
import getpass

import bcrypt
from PIL import Image, ImageDraw, ImageTk

import config
from modules.gdpr import get_consent_text, record_consent, has_consent, erase_user_data
from modules.database import update_user_fallback
from modules.ipc import make_client, send, recv
from ui._theme import apply as apply_theme, center as center_window

_PREVIEW_W = 320
_PREVIEW_H = 240
_STEPS = ["Consent", "Fallback", "Capture"]


def _pose_prompt(progress: int, total: int) -> str:
    pct = progress / total if total else 0
    if pct < 0.4:
        return "Look straight at the camera, blink naturally"
    if pct < 0.75:
        return "Stay still, small movements are fine"
    return "Almost done, keep looking at the camera"


def _check_camera_via_pipe() -> dict:
    conn = make_client()
    try:
        send(conn, {"cmd": "check_camera"})
        return recv(conn)
    finally:
        conn.close()


def _enroll_via_pipe(username: str, msg_cb) -> dict:
    """Connect to core service, forward every streaming frame via msg_cb, return final result."""
    conn = make_client()
    try:
        send(conn, {"cmd": "enroll", "username": username})
        while True:
            msg = recv(conn)
            if "jpeg" in msg:
                msg_cb(msg)
            else:
                return msg
    finally:
        conn.close()


class EnrollmentWindow(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("FaceLock — Enrollment")
        self.resizable(False, False)
        apply_theme(self)
        self._username = getpass.getuser()
        self._fallback = tk.StringVar(master=self, value=config.DEFAULT_FALLBACK)
        self._pin_var = tk.StringVar(master=self)

        self._build_chrome()
        self._show_consent_step()
        center_window(self)

    def _build_chrome(self) -> None:
        # Step indicator bar at the top
        bar = tk.Frame(self, bg="#1a73e8", height=4)
        bar.pack(fill="x")

        step_row = ttk.Frame(self, padding=(20, 10, 20, 0))
        step_row.pack(fill="x")
        self._step_labels: list[ttk.Label] = []
        for i, name in enumerate(_STEPS):
            if i:
                ttk.Label(step_row, text="──", foreground="#cccccc").pack(side="left", padx=2)
            lbl = ttk.Label(step_row, text=f"{i + 1}. {name}")
            lbl.pack(side="left")
            self._step_labels.append(lbl)

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=0, pady=(8, 0))
        self._frame_container = ttk.Frame(self, padding=20)
        self._frame_container.pack(fill="both", expand=True)

    def _set_step(self, index: int) -> None:
        for i, lbl in enumerate(self._step_labels):
            if i == index:
                lbl.configure(foreground="#1a73e8", font=("Segoe UI", 9, "bold"))
            elif i < index:
                lbl.configure(foreground="#888888", font=("Segoe UI", 9))
            else:
                lbl.configure(foreground="#aaaaaa", font=("Segoe UI", 9))

    # ------------------------------------------------------------------
    # Step 1 — GDPR consent
    # ------------------------------------------------------------------

    def _show_consent_step(self) -> None:
        self._clear()
        self._set_step(0)
        ttk.Label(self._frame_container, text="Data Collection Notice",
                  style="Section.TLabel").pack(anchor="w", pady=(0, 8))

        text = tk.Text(self._frame_container, width=64, height=14,
                       wrap="word", state="normal", font=("Consolas", 9),
                       relief="flat", borderwidth=1, background="#f8f8f8")
        text.insert("1.0", get_consent_text())
        text.config(state="disabled")
        text.pack()

        btn_row = ttk.Frame(self._frame_container)
        btn_row.pack(pady=(12, 0), fill="x")
        ttk.Button(btn_row, text="Decline", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(btn_row, text="Accept & Continue",
                   command=self._show_fallback_step).pack(side="right")

    # ------------------------------------------------------------------
    # Step 2 — Fallback method
    # ------------------------------------------------------------------

    def _show_fallback_step(self) -> None:
        self._clear()
        self._set_step(1)
        ttk.Label(self._frame_container, text="Choose a Fallback Method",
                  style="Section.TLabel").pack(anchor="w", pady=(0, 4))
        ttk.Label(self._frame_container,
                  text="Used if face authentication fails:",
                  style="Hint.TLabel").pack(anchor="w", pady=(0, 8))

        options = [
            (config.FALLBACK_NONE,    "None — face auth only"),
            (config.FALLBACK_PIN,     "PIN code"),
            (config.FALLBACK_WINDOWS, "Windows Hello / password"),
        ]
        for value, label in options:
            ttk.Radiobutton(self._frame_container, text=label,
                            variable=self._fallback, value=value).pack(anchor="w", pady=3)

        self._pin_frame = ttk.Frame(self._frame_container)
        ttk.Label(self._pin_frame, text="Enter PIN:").pack(side="left")
        ttk.Entry(self._pin_frame, textvariable=self._pin_var,
                  show="*", width=12).pack(side="left", padx=(6, 0))

        self._fallback.trace_add("write", self._toggle_pin_field)
        self._toggle_pin_field()

        btn_row = ttk.Frame(self._frame_container)
        btn_row.pack(pady=(12, 0), fill="x")
        ttk.Button(btn_row, text="Back",
                   command=self._show_consent_step).pack(side="left")
        ttk.Button(btn_row, text="Next",
                   command=self._commit_consent_and_enroll).pack(side="right")

    def _toggle_pin_field(self, *_) -> None:
        if self._fallback.get() == config.FALLBACK_PIN:
            self._pin_frame.pack(anchor="w", pady=(6, 0))
        else:
            self._pin_frame.pack_forget()

    # ------------------------------------------------------------------
    # Step 3 — Enroll (camera capture via core service)
    # ------------------------------------------------------------------

    def _commit_consent_and_enroll(self) -> None:
        pin_hash: str | None = None
        if self._fallback.get() == config.FALLBACK_PIN:
            pin = self._pin_var.get().strip()
            if not pin:
                messagebox.showwarning("PIN required", "Please enter a PIN.")
                return
            pin_hash = bcrypt.hashpw(pin.encode(), bcrypt.gensalt()).decode()

        self._pending_pin_hash = pin_hash
        try:
            result = _check_camera_via_pipe()
        except Exception:
            messagebox.showerror(
                "Camera unavailable",
                "Could not reach the FaceLock service.\n"
                "Make sure the core service is running and try again.",
            )
            return
        if not result.get("ok"):
            messagebox.showerror(
                "Camera unavailable",
                f"Cannot access the webcam:\n{result.get('reason', 'unknown error')}\n\n"
                "Check that no other application is using the camera.",
            )
            return

        if not has_consent(config.DB_PATH, self._username):
            # First-time enrollment — create user record with consent.
            record_consent(
                config.DB_PATH,
                self._username,
                self._fallback.get(),
                pin_hash,
            )
        else:
            # Re-enrollment — update fallback/PIN in case user changed them.
            update_user_fallback(
                config.DB_PATH,
                self._username,
                self._fallback.get(),
                pin_hash,
            )
        self._show_enrolling_step()

    def _show_enrolling_step(self) -> None:
        self._clear()
        self._set_step(2)
        ttk.Label(self._frame_container, text="Enrolling Your Face",
                  style="Section.TLabel").pack(anchor="w", pady=(0, 4))

        self._status_var = tk.StringVar(master=self, value="Look straight at the camera, blink naturally")
        ttk.Label(self._frame_container, textvariable=self._status_var,
                  font=("Segoe UI", 10, "bold")).pack(pady=(0, 8))

        self._preview_label = ttk.Label(self._frame_container,
                                        relief="flat", borderwidth=0)
        self._preview_label.pack(pady=(0, 8))
        self._photo_ref = None

        prog_row = ttk.Frame(self._frame_container)
        prog_row.pack(fill="x", pady=(0, 4))
        self._progress = ttk.Progressbar(prog_row, mode="determinate",
                                         maximum=config.ENROLLMENT_FRAMES,
                                         length=_PREVIEW_W - 48)
        self._progress.pack(side="left")
        self._pct_var = tk.StringVar(master=self, value="0%")
        ttk.Label(prog_row, textvariable=self._pct_var,
                  font=("Segoe UI", 9, "bold"), width=5).pack(side="left", padx=(6, 0))

        self._frame_label = tk.StringVar(master=self, value=f"0 / {config.ENROLLMENT_FRAMES} frames captured")
        ttk.Label(self._frame_container, textvariable=self._frame_label,
                  style="Hint.TLabel").pack()

        self._enroll_queue = queue.Queue()
        threading.Thread(target=self._run_enroll, daemon=True).start()
        self._poll_enroll_result()

    def _run_enroll(self) -> None:
        try:
            result = _enroll_via_pipe(self._username, self._enroll_queue.put)
        except Exception as exc:
            result = {"ok": False, "reason": str(exc)}
        self._enroll_queue.put(result)

    def _poll_enroll_result(self) -> None:
        latest_frame = None
        final = None

        # Drain everything queued since last tick; keep the newest frame only
        while True:
            try:
                msg = self._enroll_queue.get_nowait()
            except queue.Empty:
                break
            if "jpeg" in msg:
                latest_frame = msg
            else:
                final = msg
                break

        if latest_frame:
            self._update_preview(latest_frame)

        if final is not None:
            self._on_enroll_done(final)
            return

        self.after(30, self._poll_enroll_result)

    def _update_preview(self, msg: dict) -> None:
        img = Image.open(io.BytesIO(msg["jpeg"]))
        boxes = msg.get("boxes", [])
        progress = msg["progress"]
        total = msg["total"]

        if boxes:
            draw = ImageDraw.Draw(img)
            color = "#00dd00" if len(boxes) == 1 else "#ffcc00"
            for (x, y, w, h) in boxes:
                draw.rectangle([x, y, x + w, y + h], outline=color, width=3)

        img.thumbnail((_PREVIEW_W, _PREVIEW_H))
        photo = ImageTk.PhotoImage(img)
        self._preview_label.configure(image=photo)
        self._photo_ref = photo

        self._progress["value"] = progress
        pct = int(progress / total * 100) if total else 0
        self._pct_var.set(f"{pct}%")
        self._frame_label.set(f"{progress} / {total} frames captured")

        if len(boxes) == 0:
            self._status_var.set("No face detected — look at the camera…")
        elif len(boxes) > 1:
            self._status_var.set("Multiple faces — ensure only you are visible")
        else:
            self._status_var.set(_pose_prompt(progress, total))

    def _on_enroll_done(self, result: dict) -> None:
        if result.get("ok"):
            from modules.notifications import notify
            notify("FaceLock — Enrolled", f"{self._username} has been enrolled successfully.")
            self._show_success_step()
        else:
            erase_user_data(config.DB_PATH, config.KEY_PATH, self._username)
            reason = result.get("reason", "unknown error")
            messagebox.showerror("Enrollment failed", f"Could not enroll: {reason}")
            self._show_enrolling_step()

    def _show_success_step(self) -> None:
        self._clear()
        for lbl in self._step_labels:
            lbl.configure(foreground="#1a73e8", font=("Segoe UI", 9, "bold"))

        ttk.Label(self._frame_container, text="✓",
                  font=("Segoe UI", 52, "bold"), foreground="#1a8f1a").pack(pady=(20, 0))

        ttk.Label(self._frame_container, text="You're all set!",
                  font=("Segoe UI", 14, "bold")).pack(pady=(8, 2))
        ttk.Label(self._frame_container,
                  text=f"Enrolled as  {self._username}",
                  font=("Segoe UI", 10)).pack()
        ttk.Label(self._frame_container,
                  text="FaceLock will monitor your presence and\n"
                       "lock this device when you step away.",
                  justify="center", style="Hint.TLabel").pack(pady=(10, 0))

        ttk.Button(self._frame_container, text="Done",
                   command=self.destroy).pack(pady=(20, 8))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def destroy(self) -> None:
        for attr in ("_fallback", "_pin_var", "_status_var", "_frame_label", "_pct_var", "_tol_var"):
            setattr(self, attr, None)
        super().destroy()

    def _clear(self) -> None:
        for widget in self._frame_container.winfo_children():
            widget.destroy()
        self.geometry("")  # let tkinter recalculate window size for new content


def launch() -> None:
    app = EnrollmentWindow()
    app.mainloop()


if __name__ == "__main__":
    launch()
