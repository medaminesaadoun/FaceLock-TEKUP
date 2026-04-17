# ui/enrollment_window.py
import tkinter as tk
from tkinter import ttk, messagebox
import threading
import getpass

import config
from modules.gdpr import get_consent_text, record_consent, has_consent
from modules.ipc import make_client, send, recv


def _enroll_via_pipe(username: str) -> dict:
    conn = make_client()
    try:
        send(conn, {"cmd": "enroll", "username": username})
        return recv(conn)
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
            import bcrypt
            pin = self._pin_var.get().strip()
            if not pin:
                messagebox.showwarning("PIN required", "Please enter a PIN.")
                return
            pin_hash = bcrypt.hashpw(pin.encode(), bcrypt.gensalt()).decode()

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

        self._progress = ttk.Progressbar(self._frame_container, mode="indeterminate",
                                         length=300)
        self._progress.pack(pady=(0, 8))
        self._progress.start(12)

        threading.Thread(target=self._run_enroll, daemon=True).start()

    def _run_enroll(self) -> None:
        try:
            result = _enroll_via_pipe(self._username)
        except Exception as exc:
            result = {"ok": False, "reason": str(exc)}
        self.after(0, self._on_enroll_done, result)

    def _on_enroll_done(self, result: dict) -> None:
        self._progress.stop()
        if result.get("ok"):
            self._show_success_step()
        else:
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
