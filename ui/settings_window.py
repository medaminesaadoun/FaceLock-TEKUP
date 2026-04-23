# ui/settings_window.py
import tkinter as tk
from tkinter import ttk, messagebox
import getpass
import subprocess
import os

import config
from modules.gdpr import erase_user_data, generate_dpia, has_consent
from modules.user_settings import load as load_settings, save as save_settings
from ui._theme import apply as apply_theme, center as center_window


class SettingsWindow(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("FaceLock — Settings")
        self.resizable(False, False)
        apply_theme(self)
        self._username = getpass.getuser()
        self._settings = load_settings(config.SETTINGS_PATH)
        self._build_ui()
        center_window(self)

    def _build_ui(self) -> None:
        # Blue accent bar at top
        tk.Frame(self, bg="#1a73e8", height=4).pack(fill="x")

        outer = ttk.Frame(self, padding=(24, 16, 24, 20))
        outer.pack(fill="both", expand=True)

        # ---- Account ----
        self._section(outer, "Account")
        enrolled = has_consent(config.DB_PATH, self._username)
        if enrolled:
            row = ttk.Frame(outer)
            row.pack(fill="x", pady=(0, 4))
            ttk.Label(row, text=f"Enrolled as  {self._username}",
                      font=("Segoe UI", 10)).pack(side="left")
            ttk.Label(row, text="  Active", foreground="#1a8f1a",
                      font=("Segoe UI", 9, "bold")).pack(side="left")
        else:
            ttk.Label(outer, text="Not enrolled", foreground="#cc0000").pack(
                anchor="w", pady=(0, 4))

        acct_row = ttk.Frame(outer)
        acct_row.pack(anchor="w", pady=(2, 8))
        ttk.Button(acct_row, text="Re-enroll",
                   command=self._re_enroll).pack(side="left", padx=(0, 8))

        # ---- Recognition Sensitivity ----
        self._section(outer, "Recognition Sensitivity")

        tol_frame = ttk.Frame(outer)
        tol_frame.pack(fill="x", pady=(0, 4))

        ttk.Label(tol_frame, text="Strict", style="Hint.TLabel").pack(side="left")
        self._tol_var = tk.DoubleVar(value=self._settings["tolerance"])
        ttk.Scale(tol_frame, from_=0.3, to=0.7, variable=self._tol_var,
                  orient="horizontal", length=180,
                  command=self._on_slider_move).pack(side="left", padx=8)
        ttk.Label(tol_frame, text="Lenient", style="Hint.TLabel").pack(side="left")

        val_row = ttk.Frame(outer)
        val_row.pack(anchor="w", pady=(0, 8))
        ttk.Label(val_row, text="Threshold: ", style="Hint.TLabel").pack(side="left")
        self._tol_display = tk.StringVar(value=f"{self._settings['tolerance']:.2f}")
        ttk.Label(val_row, textvariable=self._tol_display,
                  font=("Segoe UI", 9, "bold")).pack(side="left")
        ttk.Label(val_row, text="  (lower rejects more faces)",
                  style="Hint.TLabel").pack(side="left")

        # ---- Privacy & GDPR ----
        self._section(outer, "Privacy & GDPR")
        gdpr_row = ttk.Frame(outer)
        gdpr_row.pack(anchor="w", pady=(0, 16))
        ttk.Button(gdpr_row, text="Delete My Data",
                   command=self._delete_data).pack(side="left", padx=(0, 8))
        ttk.Button(gdpr_row, text="View / Export DPIA",
                   command=self._view_dpia).pack(side="left")

        # ---- Buttons ----
        ttk.Separator(outer, orient="horizontal").pack(fill="x", pady=(4, 12))
        btn_row = ttk.Frame(outer)
        btn_row.pack(anchor="e")
        ttk.Button(btn_row, text="Cancel",
                   command=self.destroy).pack(side="left", padx=(0, 8))
        ttk.Button(btn_row, text="Save & Close",
                   command=self._save_and_close).pack(side="left")

    def _section(self, parent: ttk.Frame, title: str) -> None:
        ttk.Label(parent, text=title, style="Section.TLabel").pack(
            anchor="w", pady=(8, 2))
        ttk.Separator(parent, orient="horizontal").pack(fill="x", pady=(0, 8))

    def _on_slider_move(self, _=None) -> None:
        self._tol_display.set(f"{self._tol_var.get():.2f}")

    # ------------------------------------------------------------------

    def _save_and_close(self) -> None:
        self._settings["tolerance"] = round(self._tol_var.get(), 2)
        save_settings(config.SETTINGS_PATH, self._settings)
        self.destroy()

    def _re_enroll(self) -> None:
        from ui.enrollment_window import launch as launch_enroll
        self.destroy()
        launch_enroll()

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
            subprocess.Popen(["notepad.exe", config.DPIA_PATH])
        except Exception as exc:
            messagebox.showerror("Error", f"Could not open DPIA: {exc}")


def launch() -> None:
    win = SettingsWindow()
    win.mainloop()


if __name__ == "__main__":
    launch()
