# ui/dashboard.py
import getpass
import threading
import tkinter as tk
from tkinter import ttk
from datetime import datetime, timezone

import config
from modules.database import get_user, get_connection
from modules.gdpr import has_consent
from modules.ipc import make_client, send, recv
from ui._theme import apply as apply_theme, center as center_window


def _last_auth_label(username: str) -> str:
    """Return a human-readable string for the last successful authentication."""
    try:
        with get_connection(config.DB_PATH) as conn:
            row = conn.execute(
                "SELECT timestamp FROM audit_log "
                "WHERE windows_username = ? AND result = 'pass' "
                "ORDER BY timestamp DESC LIMIT 1",
                (username,)
            ).fetchone()
        if not row:
            return "Never"
        ts = datetime.fromisoformat(row["timestamp"]).replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - ts
        s = int(delta.total_seconds())
        if s < 60:
            return "Just now"
        if s < 3600:
            return f"{s // 60} min ago"
        if s < 86400:
            return f"{s // 3600} h ago"
        return f"{s // 86400} days ago"
    except Exception:
        return "Unknown"


def _query_paused() -> bool:
    try:
        conn = make_client()
        send(conn, {"cmd": "status"})
        result = recv(conn)
        conn.close()
        return result.get("paused", False)
    except Exception:
        return False


class Dashboard(tk.Tk):
    def __init__(self, locked: bool, paused: bool,
                 on_pause_toggle, on_quit, on_open_settings,
                 on_open_enroll, on_open_debug) -> None:
        super().__init__()
        self.title("FaceLock")
        self.resizable(False, False)
        apply_theme(self)

        self._locked = locked
        self._paused = paused
        self._username = getpass.getuser()
        self._on_pause_toggle = on_pause_toggle
        self._on_quit = on_quit
        self._on_open_settings = on_open_settings
        self._on_open_enroll = on_open_enroll
        self._on_open_debug = on_open_debug

        self._build()
        center_window(self)
        self.bind("<FocusOut>", self._on_focus_out)
        self.after(2000, self._refresh)

    def _build(self) -> None:
        # Accent bar
        tk.Frame(self, bg="#1a73e8", height=4).pack(fill="x")

        # Header
        header = ttk.Frame(self, padding=(20, 14, 20, 8))
        header.pack(fill="x")
        ttk.Label(header, text="FaceLock",
                  font=("Segoe UI", 16, "bold")).pack(side="left")
        ttk.Label(header, text=f"v{config.APP_VERSION}",
                  foreground="#aaaaaa", font=("Segoe UI", 9)).pack(
                      side="left", padx=(6, 0), anchor="s", pady=(0, 3))

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=20)

        body = ttk.Frame(self, padding=(20, 12, 20, 4))
        body.pack(fill="x")

        # Status card
        self._status_frame = tk.Frame(body, bd=1, relief="solid",
                                      bg="#f4f4f4", padx=14, pady=10)
        self._status_frame.pack(fill="x", pady=(0, 10))
        ttk.Label(self._status_frame, text="Status",
                  font=("Segoe UI", 9, "bold"),
                  background="#f4f4f4").pack(anchor="w")
        self._status_dot = tk.Label(self._status_frame, font=("Segoe UI", 11),
                                    background="#f4f4f4")
        self._status_dot.pack(anchor="w", pady=(2, 0))
        self._update_status_card()

        # Account card
        enrolled = has_consent(config.DB_PATH, self._username)
        acct_frame = tk.Frame(body, bd=1, relief="solid",
                              bg="#f4f4f4", padx=14, pady=10)
        acct_frame.pack(fill="x", pady=(0, 12))
        ttk.Label(acct_frame, text="Account",
                  font=("Segoe UI", 9, "bold"),
                  background="#f4f4f4").pack(anchor="w")

        user_row = tk.Frame(acct_frame, bg="#f4f4f4")
        user_row.pack(fill="x", pady=(4, 0))
        tk.Label(user_row, text=self._username,
                 font=("Segoe UI", 11, "bold"),
                 background="#f4f4f4").pack(side="left")
        badge_color = "#1a8f1a" if enrolled else "#cc0000"
        badge_text  = "Enrolled" if enrolled else "Not enrolled"
        tk.Label(user_row, text=f"  {badge_text}",
                 foreground=badge_color, font=("Segoe UI", 9, "bold"),
                 background="#f4f4f4").pack(side="left")

        if enrolled:
            self._last_auth_var = tk.StringVar(value=f"Last auth:  {_last_auth_label(self._username)}")
            tk.Label(acct_frame, textvariable=self._last_auth_var,
                     font=("Segoe UI", 9), foreground="#666666",
                     background="#f4f4f4").pack(anchor="w", pady=(2, 0))
        else:
            self._last_auth_var = None

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=20)

        # Action buttons
        btn_row = ttk.Frame(self, padding=(20, 10, 20, 14))
        btn_row.pack(fill="x")

        self._pause_btn = ttk.Button(btn_row, text=self._pause_label(),
                                     command=self._toggle_pause)
        self._pause_btn.pack(side="left", padx=(0, 6))

        ttk.Button(btn_row, text="Settings",
                   command=self._settings).pack(side="left", padx=(0, 6))
        ttk.Button(btn_row, text="Enroll",
                   command=self._enroll).pack(side="left", padx=(0, 6))
        ttk.Button(btn_row, text="Debug",
                   command=self._debug).pack(side="left", padx=(0, 6))
        ttk.Button(btn_row, text="Quit",
                   command=self._quit).pack(side="right")

    # ------------------------------------------------------------------

    def update_state(self, locked: bool, paused: bool) -> None:
        self._locked = locked
        self._paused = paused
        self._update_status_card()
        if hasattr(self, "_pause_btn"):
            self._pause_btn.configure(text=self._pause_label())

    def _update_status_card(self) -> None:
        if self._paused:
            dot, color = "⏸  Paused", "#e6a817"
        elif self._locked:
            dot, color = "🔒  Locked", "#cc0000"
        else:
            dot, color = "✅  Active", "#1a8f1a"
        if hasattr(self, "_status_dot"):
            self._status_dot.configure(text=dot, foreground=color)

    def _pause_label(self) -> str:
        return "Resume" if self._paused else "Pause"

    def _refresh(self) -> None:
        if self._last_auth_var:
            self._last_auth_var.set(f"Last auth:  {_last_auth_label(self._username)}")
        self.after(2000, self._refresh)

    # ------------------------------------------------------------------

    def _on_focus_out(self, event) -> None:
        if event.widget is self:
            self.after(100, self._check_focus)

    def _check_focus(self) -> None:
        if self.focus_displayof() is None:
            self.destroy()

    def destroy(self) -> None:
        self._last_auth_var = None
        super().destroy()

    # ------------------------------------------------------------------

    def _toggle_pause(self) -> None:
        self._on_pause_toggle()
        self._paused = not self._paused
        self._update_status_card()
        self._pause_btn.configure(text=self._pause_label())

    def _settings(self) -> None:
        self._on_open_settings()
        self.after(50, self.destroy)

    def _enroll(self) -> None:
        self._on_open_enroll()
        self.after(50, self.destroy)

    def _debug(self) -> None:
        self._on_open_debug()
        self.after(50, self.destroy)

    def _quit(self) -> None:
        self._on_quit()
        self.after(50, self.destroy)


def launch(locked: bool, paused: bool,
           on_pause_toggle, on_quit,
           on_open_settings, on_open_enroll, on_open_debug) -> None:
    app = Dashboard(locked, paused, on_pause_toggle, on_quit,
                    on_open_settings, on_open_enroll, on_open_debug)
    app.mainloop()
