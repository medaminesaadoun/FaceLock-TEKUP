# ui/settings_window.py
import tkinter as tk
from tkinter import ttk, messagebox
import getpass
import subprocess
import os

import config
from modules.gdpr import erase_user_data, generate_dpia, has_consent


class SettingsWindow(tk.Toplevel):
    def __init__(self, parent: tk.Misc | None = None) -> None:
        super().__init__(parent)
        self.title("FaceLock — Settings")
        self.resizable(False, False)
        self._username = getpass.getuser()
        self._build_ui()

    def _build_ui(self) -> None:
        pad = {"padx": 20, "pady": 8}

        # ---- Account section ----
        ttk.Label(self, text="Account", font=("Segoe UI", 11, "bold")).pack(
            anchor="w", padx=20, pady=(16, 0))
        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=20, pady=(2, 6))

        enrolled = has_consent(config.DB_PATH, self._username)
        status_text = f"Enrolled as:  {self._username}" if enrolled else "Not enrolled"
        ttk.Label(self, text=status_text).pack(anchor="w", **pad)

        # ---- GDPR section ----
        ttk.Label(self, text="Privacy & GDPR", font=("Segoe UI", 11, "bold")).pack(
            anchor="w", padx=20, pady=(8, 0))
        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=20, pady=(2, 6))

        btn_frame = ttk.Frame(self)
        btn_frame.pack(anchor="w", **pad)

        ttk.Button(btn_frame, text="Delete My Data",
                   command=self._delete_data).pack(side="left", padx=(0, 8))
        ttk.Button(btn_frame, text="View / Export DPIA",
                   command=self._view_dpia).pack(side="left")

        # ---- Tolerance section ----
        ttk.Label(self, text="Recognition Sensitivity",
                  font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=20, pady=(8, 0))
        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=20, pady=(2, 6))

        tol_frame = ttk.Frame(self)
        tol_frame.pack(anchor="w", **pad)
        ttk.Label(tol_frame, text="Distance tolerance (lower = stricter):").pack(
            side="left")
        self._tol_var = tk.DoubleVar(value=config.DEFAULT_TOLERANCE)
        ttk.Scale(tol_frame, from_=0.3, to=0.7, variable=self._tol_var,
                  orient="horizontal", length=140).pack(side="left", padx=(8, 4))
        ttk.Label(tol_frame, textvariable=self._tol_var).pack(side="left")

        # ---- Close ----
        ttk.Button(self, text="Close", command=self.destroy).pack(
            anchor="e", padx=20, pady=(8, 16))

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _delete_data(self) -> None:
        if not messagebox.askyesno(
            "Delete My Data",
            "This will permanently erase your face data and consent record.\n"
            "You will need to re-enroll to use FaceLock.\n\nProceed?",
        ):
            return
        try:
            erase_user_data(config.DB_PATH, config.KEY_PATH, self._username)
            messagebox.showinfo("Done", "Your data has been erased.")
            self.destroy()
        except Exception as exc:
            messagebox.showerror("Error", f"Could not erase data: {exc}")

    def _view_dpia(self) -> None:
        try:
            generate_dpia(config.DPIA_PATH, self._username)
            if os.path.exists(config.DPIA_PATH):
                os.startfile(config.DPIA_PATH)
        except Exception as exc:
            messagebox.showerror("Error", f"Could not open DPIA: {exc}")


def launch(parent: tk.Misc | None = None) -> None:
    win = SettingsWindow(parent)
    if parent is None:
        win.mainloop()


if __name__ == "__main__":
    root = tk.Tk()
    root.withdraw()
    launch(root)
    root.mainloop()
