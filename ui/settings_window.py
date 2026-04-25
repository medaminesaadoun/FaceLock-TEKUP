# ui/settings_window.py
import tkinter as tk
from tkinter import ttk, messagebox
import getpass
import subprocess
import threading

import config
from modules.database import get_user
from modules.gdpr import erase_user_data, generate_dpia, has_consent
from modules.user_settings import (
    load as load_settings, save as save_settings,
    PRESETS, get_active_preset,
)
from ui._theme import apply as apply_theme, center as center_window

# Brief descriptions shown under each preset name.
_PRESET_HINTS: dict[str, str] = {
    "Max Security": "Locks fast, strict matching",
    "Balanced":     "Recommended for daily use",
    "Relaxed":      "Lenient matching, slow lock",
}


def _detect_preset(settings: dict) -> str:
    """Return the preset name matching current settings, or '' if custom."""
    for name, vals in PRESETS.items():
        if (
            abs(settings.get("tolerance", 0) - vals["tolerance"]) < 0.01
            and settings.get("lock_timeout") == vals["lock_timeout"]
            and settings.get("unlock_grace") == vals["unlock_grace"]
            and settings.get("auth_fallback_timeout") == vals["auth_fallback_timeout"]
        ):
            return name
    return ""


class SettingsWindow(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("FaceLock — Settings")
        self.resizable(False, False)
        apply_theme(self)
        self._username = getpass.getuser()
        self._settings = load_settings(config.SETTINGS_PATH)
        # Track slider vars: (key, IntVar, StringVar_display, trace_id)
        self._slider_vars: list[tuple] = []
        self._build_ui()
        center_window(self)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
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
            ttk.Label(outer, text="Not enrolled",
                      foreground="#cc0000").pack(anchor="w", pady=(0, 4))

        acct_row = ttk.Frame(outer)
        acct_row.pack(anchor="w", pady=(2, 8))
        ttk.Button(acct_row, text="Re-enroll",
                   command=self._re_enroll).pack(side="left", padx=(0, 8))

        # ---- Sensitivity & Locking ----
        self._section(outer, "Sensitivity & Locking")

        # Mode toggle — Simple shows preset picker, Advanced shows sliders.
        mode_row = ttk.Frame(outer)
        mode_row.pack(anchor="w", pady=(0, 10))
        self._mode_var = tk.StringVar(
            master=self,
            value=self._settings.get("settings_mode", "simple"))

        ttk.Radiobutton(
            mode_row, text="Simple", variable=self._mode_var, value="simple",
            command=self._on_mode_change,
        ).pack(side="left", padx=(0, 16))
        ttk.Radiobutton(
            mode_row, text="Advanced", variable=self._mode_var, value="advanced",
            command=self._on_mode_change,
        ).pack(side="left")

        # Fixed container — always occupies the same slot in outer so that
        # pack_forget/pack inside it doesn't scramble the surrounding sections.
        self._mode_container = ttk.Frame(outer)
        self._mode_container.pack(fill="x", pady=(0, 8))

        self._simple_frame = ttk.Frame(self._mode_container)
        self._build_simple_content(self._simple_frame)

        self._advanced_frame = ttk.Frame(self._mode_container)
        self._build_advanced_content(self._advanced_frame)

        # Show whichever frame matches the current mode.
        self._apply_mode_visibility()

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
            cb.configure(state="disabled")
            ttk.Label(
                outer,
                text="Requires PIN fallback — re-enroll with PIN to enable",
                style="Hint.TLabel",
            ).pack(anchor="w", pady=(2, 8))
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

    def _build_simple_content(self, parent: ttk.Frame) -> None:
        """Three preset radio buttons with hint text."""
        active = _detect_preset(self._settings)
        self._preset_var = tk.StringVar(master=self, value=active)

        for name, hint in _PRESET_HINTS.items():  # _PRESET_HINTS keys match PRESETS keys
            row = ttk.Frame(parent)
            row.pack(fill="x", pady=3)
            ttk.Radiobutton(
                row, text=name, variable=self._preset_var, value=name,
                command=lambda n=name: self._select_preset(n),
            ).pack(side="left")
            ttk.Label(row, text=f"  —  {hint}",
                      style="Hint.TLabel").pack(side="left")

        # Placeholder when no preset matches (custom advanced values).
        ttk.Label(
            parent,
            text="Switch to Advanced to fine-tune individual values.",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(8, 4))

    def _build_advanced_content(self, parent: ttk.Frame) -> None:
        """Tolerance slider + three integer locking sliders."""
        # Tolerance slider.
        tol_frame = ttk.Frame(parent)
        tol_frame.pack(fill="x", pady=(0, 4))
        ttk.Label(tol_frame, text="Strict",
                  style="Hint.TLabel").pack(side="left")
        self._tol_var = tk.DoubleVar(
            master=self, value=self._settings["tolerance"])
        ttk.Scale(
            tol_frame, from_=0.3, to=0.7, variable=self._tol_var,
            orient="horizontal", length=180,
            command=self._on_slider_move,
        ).pack(side="left", padx=8)
        ttk.Label(tol_frame, text="Lenient",
                  style="Hint.TLabel").pack(side="left")

        val_row = ttk.Frame(parent)
        val_row.pack(anchor="w", pady=(0, 10))
        ttk.Label(val_row, text="Threshold: ",
                  style="Hint.TLabel").pack(side="left")
        self._tol_display = tk.StringVar(
            master=self, value=f"{self._settings['tolerance']:.2f}")
        ttk.Label(val_row, textvariable=self._tol_display,
                  font=("Segoe UI", 9, "bold")).pack(side="left")
        ttk.Label(val_row, text="  (lower = stricter)",
                  style="Hint.TLabel").pack(side="left")

        # Integer locking sliders.
        self._add_int_slider(parent, "Lock timeout",
                             "lock_timeout", 3, 30, "s")
        self._add_int_slider(parent, "Unlock grace period",
                             "unlock_grace", 0, 60, "s")
        self._add_int_slider(parent, "Auth fallback timeout",
                             "auth_fallback_timeout", 30, 120, "s")

        ttk.Label(
            parent,
            text="Lock timeout: seconds without a face before locking.\n"
                 "Grace period: cooldown after unlock before monitoring resumes.\n"
                 "Auth fallback: overlay duration before Windows lock activates.",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(4, 8))

    def _add_int_slider(self, parent, label, key, from_, to, unit) -> None:
        """Build one labelled integer slider and register it in _slider_vars."""
        var = tk.IntVar(master=self, value=int(self._settings.get(key, 0)))
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(0, 2))
        ttk.Label(row, text=label, width=26).pack(side="left")
        ttk.Scale(
            row, from_=from_, to=to, variable=var,
            orient="horizontal", length=150,
            command=lambda _: var.set(int(var.get())),
        ).pack(side="left", padx=6)
        disp = tk.StringVar(master=self, value=f"{var.get()} {unit}")
        ttk.Label(row, textvariable=disp,
                  font=("Segoe UI", 9, "bold"), width=7).pack(side="left")
        # Save trace ID for explicit removal in destroy().
        tid = var.trace_add(
            "write",
            lambda *_, v=var, d=disp, u=unit: d.set(f"{v.get()} {u}"),
        )
        self._slider_vars.append((key, var, disp, tid))

    # ------------------------------------------------------------------

    def _section(self, parent: ttk.Frame, title: str) -> None:
        ttk.Label(parent, text=title, style="Section.TLabel").pack(
            anchor="w", pady=(8, 2))
        ttk.Separator(parent, orient="horizontal").pack(fill="x", pady=(0, 8))

    def _apply_mode_visibility(self) -> None:
        """Show the correct frame inside the fixed mode container."""
        if self._mode_var.get() == "simple":
            self._advanced_frame.pack_forget()
            self._simple_frame.pack(fill="x")
        else:
            self._simple_frame.pack_forget()
            self._advanced_frame.pack(fill="x")
        # Let tkinter recalculate window height for the new content.
        self.geometry("")

    def _on_mode_change(self) -> None:
        self._apply_mode_visibility()

    def _select_preset(self, name: str) -> None:
        """Apply a preset's values to settings dict and sync slider vars."""
        vals = PRESETS[name]
        self._settings.update(vals)
        # Sync advanced sliders so they reflect the preset if user switches.
        for key, var, _d, _t in self._slider_vars:
            if key in vals:
                var.set(int(vals[key]))
        if hasattr(self, "_tol_var") and self._tol_var:
            self._tol_var.set(vals["tolerance"])
            if hasattr(self, "_tol_display") and self._tol_display:
                self._tol_display.set(f"{vals['tolerance']:.2f}")

    def _on_slider_move(self, _=None) -> None:
        if self._tol_display:
            self._tol_display.set(f"{self._tol_var.get():.2f}")

    # ------------------------------------------------------------------

    def destroy(self) -> None:
        # Remove Tcl traces before teardown — unremoved traces cause
        # Tcl_AsyncDelete when the interpreter is in a daemon thread.
        for _, var, _disp, tid in self._slider_vars:
            try:
                var.trace_remove("write", tid)
            except Exception:
                pass
        self._slider_vars = []
        self._tol_var = None
        self._tol_display = None
        self._hidden_mode_var = None
        self._mode_var = None
        self._preset_var = None
        super().destroy()

    def _save_and_close(self) -> None:
        mode = self._mode_var.get()
        self._settings["settings_mode"] = mode

        if mode == "advanced":
            # Save tolerance and all integer sliders.
            self._settings["tolerance"] = round(self._tol_var.get(), 2)
            for key, var, _d, _t in self._slider_vars:
                self._settings[key] = int(var.get())
        # In simple mode the preset was already written to self._settings
        # when the user clicked it — nothing extra to do here.

        self._settings["hidden_mode"] = bool(self._hidden_mode_var.get())
        save_settings(config.SETTINGS_PATH, self._settings)
        self.destroy()

    # ------------------------------------------------------------------

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
