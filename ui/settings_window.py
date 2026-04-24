# ui/settings_window.py
import tkinter as tk
from tkinter import ttk, messagebox
import getpass
import subprocess
import threading
import os

import config
from modules.database import get_user
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
        self._tol_var = tk.DoubleVar(master=self, value=self._settings["tolerance"])
        ttk.Scale(tol_frame, from_=0.3, to=0.7, variable=self._tol_var,
                  orient="horizontal", length=180,
                  command=self._on_slider_move).pack(side="left", padx=8)
        ttk.Label(tol_frame, text="Lenient", style="Hint.TLabel").pack(side="left")

        val_row = ttk.Frame(outer)
        val_row.pack(anchor="w", pady=(0, 8))
        ttk.Label(val_row, text="Threshold: ", style="Hint.TLabel").pack(side="left")
        self._tol_display = tk.StringVar(master=self, value=f"{self._settings['tolerance']:.2f}")
        ttk.Label(val_row, textvariable=self._tol_display,
                  font=("Segoe UI", 9, "bold")).pack(side="left")
        ttk.Label(val_row, text="  (lower rejects more faces)",
                  style="Hint.TLabel").pack(side="left")

        # ---- Locking Behaviour ----
        self._section(outer, "Locking Behaviour")

        def _int_slider(parent, label, key, from_, to, unit="s") -> tk.IntVar:
            """Helper that builds a labelled integer slider and returns its var."""
            var = tk.IntVar(master=self, value=int(self._settings.get(key, 0)))
            row = ttk.Frame(parent)
            row.pack(fill="x", pady=(0, 2))
            ttk.Label(row, text=label, width=28).pack(side="left")
            ttk.Scale(row, from_=from_, to=to, variable=var,
                      orient="horizontal", length=140,
                      command=lambda _: var.set(int(var.get()))
                      ).pack(side="left", padx=6)
            disp = tk.StringVar(master=self,
                                value=f"{var.get()} {unit}")
            ttk.Label(row, textvariable=disp,
                      font=("Segoe UI", 9, "bold"), width=7).pack(side="left")
            # Keep display label in sync with slider.
            var.trace_add("write",
                lambda *_: disp.set(f"{var.get()} {unit}"))
            self._slider_vars.append((key, var, disp))
            return var

        # Initialise the list that _save_and_close will iterate.
        if not hasattr(self, "_slider_vars"):
            self._slider_vars = []

        _int_slider(outer, "Lock timeout",
                    "lock_timeout", 3, 30, "s")
        _int_slider(outer, "Unlock grace period",
                    "unlock_grace", 0, 60, "s")
        _int_slider(outer, "Auth fallback timeout",
                    "auth_fallback_timeout", 30, 300, "s")

        ttk.Label(outer,
                  text="Lock timeout: seconds without a face before locking.\n"
                       "Grace period: cooldown after unlock before monitoring resumes.\n"
                       "Auth fallback: overlay duration before Windows lock activates.",
                  style="Hint.TLabel").pack(anchor="w", pady=(2, 10))

        # ---- Lock Overlay ----
        self._section(outer, "Lock Overlay")

        # Hidden mode requires a PIN fallback — without it the user has no way
        # to interact with the disguised overlay if face auth fails.
        user = get_user(config.DB_PATH, self._username)
        has_pin = (
            user is not None
            and user.get("fallback_method") == config.FALLBACK_PIN
            and user.get("pin_hash")
        )

        # Force the setting off if PIN was removed since last save.
        if not has_pin:
            self._settings["hidden_mode"] = False

        self._hidden_mode_var = tk.BooleanVar(
            master=self, value=self._settings.get("hidden_mode", False))

        cb = ttk.Checkbutton(
            outer,
            text="Hidden mode — disguise overlay as Windows lock screen",
            variable=self._hidden_mode_var,
        )
        cb.pack(anchor="w")

        if not has_pin:
            # Disable and explain why.
            cb.configure(state="disabled")
            ttk.Label(outer, text="Requires PIN fallback — re-enroll with PIN to enable",
                      style="Hint.TLabel").pack(anchor="w", pady=(2, 8))
        else:
            cb.pack_configure(pady=(0, 8))

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

    def destroy(self) -> None:
        # Nullify tk.Variable refs before Tcl teardown so their __del__
        # doesn't fire in the GC thread and corrupt the interpreter.
        self._tol_var = None
        self._tol_display = None
        self._hidden_mode_var = None
        for _, var, disp in getattr(self, "_slider_vars", []):
            var._name = None  # type: ignore[attr-defined]
        self._slider_vars = []
        super().destroy()

    def _on_slider_move(self, _=None) -> None:
        self._tol_display.set(f"{self._tol_var.get():.2f}")

    # ------------------------------------------------------------------

    def _save_and_close(self) -> None:
        self._settings["tolerance"] = round(self._tol_var.get(), 2)
        self._settings["hidden_mode"] = bool(self._hidden_mode_var.get())
        # Save all integer sliders from the Locking Behaviour section.
        for key, var, _ in getattr(self, "_slider_vars", []):
            self._settings[key] = int(var.get())
        save_settings(config.SETTINGS_PATH, self._settings)
        self.destroy()

    def _re_enroll(self) -> None:
        from ui.enrollment_window import launch as launch_enroll
        self.destroy()
        threading.Thread(target=launch_enroll, daemon=True).start()

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
