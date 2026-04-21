# ui/enrollment_window.py
import tkinter as tk
from tkinter import ttk, messagebox
import threading
import queue
import getpass

import bcrypt
import config
from modules.gdpr import get_consent_text, record_consent, has_consent, erase_user_data
from modules.ipc import make_client, send, recv


def _enroll_via_pipe(username: str, progress_cb) -> dict:
    """Connect to core service, stream progress updates, return final result."""
    conn = make_client()
    try:
        send(conn, {"cmd": "enroll", "username": username})
        while True:
            msg = recv(conn)
            if "progress" in msg:
                progress_cb(msg["progress"], msg["total"])
            else:
                return msg
    finally:
        conn.close()


class EnrollmentWindow(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("FaceLock — Enrollment")
        self.resizable(False, False)
        self._username = getpass.getuser()
        self._fallback = tk.StringVar(value=config.DEFAULT_FALLBACK)
        self._pin_var = tk.StringVar()
        self._frame_container = ttk.Frame(self)
        self._frame_container.pack(fill="both", expand=True, padx=20, pady=20)
        self._show_consent_step()

    # ------------------------------------------------------------------
    # Step 1 — GDPR consent
    # ------------------------------------------------------------------

    def _show_consent_step(self) -> None:
        self._clear()
        ttk.Label(self._frame_container, text="Data Collection Notice",
                  font=("Segoe UI", 13, "bold")).pack(anchor="w", pady=(0, 8))

        text = tk.Text(self._frame_container, width=64, height=14,
                       wrap="word", state="normal", font=("Consolas", 9))
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
        ttk.Label(self._frame_container, text="Choose a Fallback Method",
                  font=("Segoe UI", 13, "bold")).pack(anchor="w", pady=(0, 8))
        ttk.Label(self._frame_container,
                  text="Used if face authentication fails:").pack(anchor="w")

        options = [
            (config.FALLBACK_NONE,    "None — face auth only"),
            (config.FALLBACK_PIN,     "PIN code"),
            (config.FALLBACK_WINDOWS, "Windows Hello / password"),
        ]
        for value, label in options:
            ttk.Radiobutton(self._frame_container, text=label,
                            variable=self._fallback, value=value).pack(anchor="w", pady=2)

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
        # Record consent now so the user row exists when core service enrolls.
        # Rolled back in _on_enroll_done if enrollment fails.
        if not has_consent(config.DB_PATH, self._username):
            record_consent(
                config.DB_PATH,
                self._username,
                self._fallback.get(),
                pin_hash,
            )
        self._show_enrolling_step()

    def _show_enrolling_step(self) -> None:
        self._clear()
        ttk.Label(self._frame_container, text="Enrolling Your Face",
                  font=("Segoe UI", 13, "bold")).pack(anchor="w", pady=(0, 8))

        self._status_var = tk.StringVar(value="Look directly at the camera…")
        ttk.Label(self._frame_container, textvariable=self._status_var,
                  font=("Segoe UI", 10)).pack(pady=8)

        self._progress = ttk.Progressbar(self._frame_container, mode="determinate",
                                         maximum=config.ENROLLMENT_FRAMES, length=300)
        self._progress.pack(pady=(0, 8))

        self._frame_label = tk.StringVar(value=f"0 / {config.ENROLLMENT_FRAMES} frames captured")
        ttk.Label(self._frame_container, textvariable=self._frame_label,
                  font=("Segoe UI", 9), foreground="#555555").pack()

        self._enroll_queue = queue.Queue()
        threading.Thread(target=self._run_enroll, daemon=True).start()
        self._poll_enroll_result()

    def _on_progress(self, captured: int, total: int) -> None:
        self._enroll_queue.put({"progress": captured, "total": total})

    def _run_enroll(self) -> None:
        try:
            result = _enroll_via_pipe(self._username, self._on_progress)
        except Exception as exc:
            result = {"ok": False, "reason": str(exc)}
        self._enroll_queue.put(result)

    def _poll_enroll_result(self) -> None:
        try:
            msg = self._enroll_queue.get_nowait()
            if "progress" in msg:
                captured, total = msg["progress"], msg["total"]
                self._progress["value"] = captured
                self._frame_label.set(f"{captured} / {total} frames captured")
                self._status_var.set("Face detected — hold still…")
                self.after(100, self._poll_enroll_result)
            else:
                self._on_enroll_done(msg)
        except queue.Empty:
            self.after(100, self._poll_enroll_result)

    def _on_enroll_done(self, result: dict) -> None:
        if result.get("ok"):
            self._show_success_step()
        else:
            # Roll back consent so the user can retry from a clean state
            erase_user_data(config.DB_PATH, config.KEY_PATH, self._username)
            reason = result.get("reason", "unknown error")
            messagebox.showerror("Enrollment failed", f"Could not enroll: {reason}")
            self._show_enrolling_step()

    def _show_success_step(self) -> None:
        self._clear()
        ttk.Label(self._frame_container, text="Enrollment Complete",
                  font=("Segoe UI", 13, "bold")).pack(pady=(0, 8))
        ttk.Label(self._frame_container,
                  text="Your face has been enrolled successfully.\n"
                       "FaceLock will now protect this device.").pack()
        ttk.Button(self._frame_container, text="Close",
                   command=self.destroy).pack(pady=(16, 0))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _clear(self) -> None:
        for widget in self._frame_container.winfo_children():
            widget.destroy()


def launch() -> None:
    app = EnrollmentWindow()
    app.mainloop()


if __name__ == "__main__":
    launch()
