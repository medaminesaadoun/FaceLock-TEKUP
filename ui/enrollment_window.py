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


def _enroll_via_pipe(username: str, msg_cb,
                     mode: str = "replace",
                     face_name: str = "Primary") -> dict:
    """Connect to core service, stream frames via msg_cb, return final result."""
    conn = make_client()
    try:
        send(conn, {
            "cmd": "enroll",
            "username": username,
            "mode": mode,
            "face_name": face_name,
        })
        while True:
            msg = recv(conn)
            if "jpeg" in msg:
                msg_cb(msg)
            else:
                return msg
    finally:
        conn.close()


class EnrollmentWindow(tk.Tk):
    def __init__(self, mode: str = "enroll") -> None:
        """
        mode: "enroll"    — full wizard (consent → fallback → capture), replaces existing face.
              "add_user"  — abbreviated wizard (name → capture), appends a new face.
        """
        super().__init__()
        self._mode = mode
        self.title("FaceLock — Add User" if mode == "add_user" else "FaceLock — Enrollment")
        self.resizable(False, False)
        apply_theme(self)
        self._username = getpass.getuser()
        self._fallback = tk.StringVar(master=self, value=config.FALLBACK_WINDOWS)
        self._pin_var = tk.StringVar(master=self)
        self._confirm_pin_var = tk.StringVar(master=self)
        self._face_name_var = tk.StringVar(master=self, value="")

        # Step labels differ by mode.
        if mode == "add_user":
            self._step_names = ["Name", "Capture"]
        else:
            self._step_names = ["Consent", "Fallback", "Capture"]

        self._build_chrome()

        if mode == "add_user":
            self._show_name_step()
        else:
            self._show_consent_step()

        center_window(self)

    def _build_chrome(self) -> None:
        tk.Frame(self, bg="#1a73e8", height=4).pack(fill="x")

        step_row = ttk.Frame(self, padding=(20, 10, 20, 0))
        step_row.pack(fill="x")
        self._step_labels: list[ttk.Label] = []
        for i, name in enumerate(self._step_names):
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
    # Add-user mode: Name step
    # ------------------------------------------------------------------

    def _show_name_step(self) -> None:
        self._clear()
        self._set_step(0)
        ttk.Label(self._frame_container, text="Name This Face",
                  style="Section.TLabel").pack(anchor="w", pady=(0, 4))
        ttk.Label(self._frame_container,
                  text="Give this face a name so you can identify it later.",
                  style="Hint.TLabel").pack(anchor="w", pady=(0, 12))

        name_row = ttk.Frame(self._frame_container)
        name_row.pack(anchor="w")
        ttk.Label(name_row, text="Name:").pack(side="left")
        ttk.Entry(name_row, textvariable=self._face_name_var,
                  width=20).pack(side="left", padx=(8, 0))

        ttk.Label(self._frame_container,
                  text='Leave blank to use "User 2", "User 3", etc.',
                  style="Hint.TLabel").pack(anchor="w", pady=(6, 0))

        btn_row = ttk.Frame(self._frame_container)
        btn_row.pack(pady=(16, 0), fill="x")
        ttk.Button(btn_row, text="Cancel", command=self.destroy).pack(side="left")
        ttk.Button(btn_row, text="Next",
                   command=self._check_camera_and_enroll).pack(side="right")

    # ------------------------------------------------------------------
    # Enroll mode: Consent step
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
    # Enroll mode: Fallback step
    # ------------------------------------------------------------------

    def _show_fallback_step(self) -> None:
        self._clear()
        self._set_step(1)
        ttk.Label(self._frame_container, text="Choose a Fallback Method",
                  style="Section.TLabel").pack(anchor="w", pady=(0, 4))
        ttk.Label(self._frame_container,
                  text="Used if face authentication fails:",
                  style="Hint.TLabel").pack(anchor="w", pady=(0, 8))

        # FALLBACK_NONE intentionally excluded — no fallback is too risky.
        options = [
            (config.FALLBACK_PIN,     "PIN code"),
            (config.FALLBACK_WINDOWS, "Windows Hello / password"),
        ]
        for value, label in options:
            ttk.Radiobutton(self._frame_container, text=label,
                            variable=self._fallback, value=value).pack(anchor="w", pady=3)

        # PIN entry + confirm + eye toggles — shown only when PIN is selected.
        self._pin_section = ttk.Frame(self._frame_container)
        self._build_pin_fields(self._pin_section)
        self._fallback.trace_add("write", self._toggle_pin_field)
        self._toggle_pin_field()

        btn_row = ttk.Frame(self._frame_container)
        btn_row.pack(pady=(12, 0), fill="x")
        ttk.Button(btn_row, text="Back",
                   command=self._show_consent_step).pack(side="left")
        ttk.Button(btn_row, text="Next",
                   command=self._commit_consent_and_enroll).pack(side="right")

    def _build_pin_fields(self, parent: ttk.Frame) -> None:
        """Build PIN + Confirm PIN rows with ⊙ eye-toggle buttons."""
        def _make_row(label_text: str, var: tk.StringVar) -> tk.Entry:
            row = ttk.Frame(parent)
            row.pack(anchor="w", pady=(4, 0))
            ttk.Label(row, text=label_text, width=12).pack(side="left")
            entry = tk.Entry(row, textvariable=var, show="●",
                             font=("Segoe UI", 11), width=14,
                             bg="white", relief="solid")
            entry.pack(side="left", padx=(4, 0))

            def _toggle(e=entry):
                e.configure(show="" if e.cget("show") == "●" else "●")

            tk.Button(row, text="⊙", font=("Segoe UI", 11),
                      bg="white", relief="flat", bd=0, cursor="hand2",
                      activebackground="white", command=_toggle
                      ).pack(side="left", padx=(4, 0))
            return entry

        _make_row("PIN:", self._pin_var)
        _make_row("Confirm PIN:", self._confirm_pin_var)

    def _toggle_pin_field(self, *_) -> None:
        if self._fallback.get() == config.FALLBACK_PIN:
            self._pin_section.pack(anchor="w", pady=(6, 0))
        else:
            self._pin_section.pack_forget()
            # Clear fields when hiding so stale input isn't validated.
            self._pin_var.set("")
            self._confirm_pin_var.set("")

    # ------------------------------------------------------------------
    # Shared: camera check → capture
    # ------------------------------------------------------------------

    def _check_camera_and_enroll(self) -> None:
        """Used in add_user mode: skip consent/fallback, go straight to capture."""
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
        self._show_enrolling_step()

    def _commit_consent_and_enroll(self) -> None:
        pin_hash: str | None = None
        if self._fallback.get() == config.FALLBACK_PIN:
            pin = self._pin_var.get().strip()
            confirm = self._confirm_pin_var.get().strip()
            if not pin:
                messagebox.showwarning("PIN required", "Please enter a PIN.")
                return
            if pin != confirm:
                messagebox.showwarning("PIN mismatch",
                                       "PINs do not match. Please try again.")
                self._pin_var.set("")
                self._confirm_pin_var.set("")
                return
            pin_hash = bcrypt.hashpw(pin.encode(), bcrypt.gensalt()).decode()

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
            record_consent(config.DB_PATH, self._username,
                           self._fallback.get(), pin_hash)
        else:
            update_user_fallback(config.DB_PATH, self._username,
                                 self._fallback.get(), pin_hash)
        self._show_enrolling_step()

    # ------------------------------------------------------------------
    # Capture step (shared by both modes)
    # ------------------------------------------------------------------

    def _show_enrolling_step(self) -> None:
        self._clear()
        capture_step_index = 1 if self._mode == "add_user" else 2
        self._set_step(capture_step_index)

        title = "Adding New Face" if self._mode == "add_user" else "Enrolling Your Face"
        ttk.Label(self._frame_container, text=title,
                  style="Section.TLabel").pack(anchor="w", pady=(0, 4))

        self._status_var = tk.StringVar(master=self,
                                        value="Look straight at the camera, blink naturally")
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

        self._frame_label = tk.StringVar(
            master=self, value=f"0 / {config.ENROLLMENT_FRAMES} frames captured")
        ttk.Label(self._frame_container, textvariable=self._frame_label,
                  style="Hint.TLabel").pack()

        self._enroll_queue = queue.Queue()
        threading.Thread(target=self._run_enroll, daemon=True).start()
        self._poll_enroll_result()

    def _run_enroll(self) -> None:
        # Resolve face name: use provided name or auto-generate.
        face_name = self._face_name_var.get().strip() if self._face_name_var else ""
        if not face_name:
            face_name = "Primary" if self._mode == "enroll" else "User"
        try:
            result = _enroll_via_pipe(
                self._username,
                self._enroll_queue.put,
                mode="add" if self._mode == "add_user" else "replace",
                face_name=face_name,
            )
        except Exception as exc:
            result = {"ok": False, "reason": str(exc)}
        self._enroll_queue.put(result)

    def _poll_enroll_result(self) -> None:
        latest_frame = None
        final = None

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
        boxes    = msg.get("boxes", [])
        progress = msg["progress"]
        total    = msg["total"]

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
            label = "added" if self._mode == "add_user" else "enrolled"
            notify("FaceLock — Enrolled",
                   f"Face {label} successfully for {self._username}.")
            self._show_success_step()
        else:
            reason = result.get("reason", "unknown error")
            messagebox.showerror("Enrollment failed", f"Could not enroll: {reason}")
            if self._mode == "add_user":
                if reason == "no_consent":
                    # Primary enrollment is required before adding extra faces.
                    messagebox.showerror(
                        "Not enrolled",
                        "You must complete your initial enrollment before adding "
                        "additional faces. Please use Enroll first.")
                    self.destroy()
                else:
                    # Camera/timeout failure — let user retry capture.
                    self._show_enrolling_step()
            else:
                # Enroll mode: erase the consent record so the next attempt
                # starts cleanly from the consent step. Going back to capture
                # directly would fail because the core service would find no
                # user record after erasure.
                erase_user_data(config.DB_PATH, config.KEY_PATH, self._username)
                self._show_consent_step()

    def _show_success_step(self) -> None:
        self._clear()
        for lbl in self._step_labels:
            lbl.configure(foreground="#1a73e8", font=("Segoe UI", 9, "bold"))

        ttk.Label(self._frame_container, text="✓",
                  font=("Segoe UI", 52, "bold"), foreground="#1a8f1a").pack(pady=(20, 0))

        title = "Face Added!" if self._mode == "add_user" else "You're all set!"
        ttk.Label(self._frame_container, text=title,
                  font=("Segoe UI", 14, "bold")).pack(pady=(8, 2))
        ttk.Label(self._frame_container,
                  text=f"Enrolled as  {self._username}",
                  font=("Segoe UI", 10)).pack()
        ttk.Label(self._frame_container,
                  text="FaceLock will recognise this face\nwhen unlocking the device.",
                  justify="center", style="Hint.TLabel").pack(pady=(10, 0))

        ttk.Button(self._frame_container, text="Done",
                   command=self.destroy).pack(pady=(20, 8))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def destroy(self) -> None:
        for attr in ("_fallback", "_pin_var", "_confirm_pin_var", "_face_name_var",
                     "_status_var", "_frame_label", "_pct_var"):
            setattr(self, attr, None)
        super().destroy()

    def _clear(self) -> None:
        for widget in self._frame_container.winfo_children():
            widget.destroy()
        self.geometry("")


def launch(mode: str = "enroll") -> None:
    app = EnrollmentWindow(mode=mode)
    app.mainloop()


if __name__ == "__main__":
    launch()
