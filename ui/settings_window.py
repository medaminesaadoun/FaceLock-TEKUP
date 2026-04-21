# ui/settings_window.py
import tkinter as tk
from tkinter import ttk, messagebox
import getpass
import subprocess
import os

import config
from modules.gdpr import erase_user_data, generate_dpia, has_consent
from modules.user_settings import load as load_settings, save as save_settings


class SettingsWindow(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("FaceLock — Settings")
        self.resizable(False, False)
        self._username = getpass.getuser()
        self._settings = load_settings(config.SETTINGS_PATH)
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
        ttk.Label(tol_frame, text="Distance tolerance (lower = stricter):").pack(side="left")

        self._tol_var = tk.DoubleVar(value=self._settings["tolerance"])
        ttk.Scale(tol_frame, from_=0.3, to=0.7, variable=self._tol_var,
                  orient="horizontal", length=140,
                  command=self._on_slider_move).pack(side="left", padx=(8, 4))

        self._tol_display = tk.StringVar(value=f"{self._settings['tolerance']:.2f}")
        ttk.Label(tol_frame, textvariable=self._tol_display, width=4).pack(side="left")

        # ---- Buttons ----
        btn_row = ttk.Frame(self)
        btn_row.pack(anchor="e", padx=20, pady=(8, 16))
        ttk.Button(btn_row, text="Cancel", command=self.destroy).pack(side="left", padx=(0, 8))
        ttk.Button(btn_row, text="Save & Close", command=self._save_and_close).pack(side="left")

    def _on_slider_move(self, _=None) -> None:
        self._tol_display.set(f"{self._tol_var.get():.2f}")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _save_and_close(self) -> None:
        self._settings["tolerance"] = round(self._tol_var.get(), 2)
        save_settings(config.SETTINGS_PATH, self._settings)
        self.destroy()

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


def launch() -> None:
    win = SettingsWindow()
    win.mainloop()


if __name__ == "__main__":
    launch()
