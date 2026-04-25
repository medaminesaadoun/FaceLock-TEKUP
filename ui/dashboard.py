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
from modules.user_settings import get_active_preset
from ui._theme import apply as apply_theme, center as center_window


# ---------------------------------------------------------------------------
# Data helpers — all return safe defaults on any DB/IPC failure
# ---------------------------------------------------------------------------

def _last_auth_label(username: str) -> str:
    """Human-readable string for the last successful authentication."""
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


def _today_stats(username: str) -> dict:
    """Total auth attempts, failures, and success % for today (UTC)."""
    try:
        today = (datetime.now(timezone.utc)
                 .replace(hour=0, minute=0, second=0, microsecond=0)
                 .isoformat())
        with get_connection(config.DB_PATH) as conn:
            rows = conn.execute(
                "SELECT result, COUNT(*) as cnt FROM audit_log "
                "WHERE windows_username = ? AND timestamp >= ? GROUP BY result",
                (username, today)
            ).fetchall()
        counts = {r["result"]: r["cnt"] for r in rows}
        total = sum(counts.values())
        failures = counts.get("fail", 0)
        pct = int((total - failures) / total * 100) if total else 0
        return {"total": total, "failures": failures, "pct": pct}
    except Exception:
        return {"total": 0, "failures": 0, "pct": 0}


def _recent_events(username: str, limit: int = 5) -> list[tuple[str, str]]:
    """Last N (timestamp ISO, result) rows from audit_log, newest first."""
    try:
        with get_connection(config.DB_PATH) as conn:
            rows = conn.execute(
                "SELECT timestamp, result FROM audit_log "
                "WHERE windows_username = ? ORDER BY timestamp DESC LIMIT ?",
                (username, limit)
            ).fetchall()
        return [(r["timestamp"], r["result"]) for r in rows]
    except Exception:
        return []


def _fallback_label(username: str) -> str:
    """Return a short label for the user's configured fallback method."""
    try:
        user = get_user(config.DB_PATH, username)
        if not user:
            return ""
        return {
            "pin":     "PIN",
            "windows": "Windows",
            "none":    "",
        }.get(user.get("fallback_method", "none"), "")
    except Exception:
        return ""


def _event_age_label(iso_ts: str) -> str:
    """Short human-readable age for a single audit event timestamp."""
    try:
        ts = datetime.fromisoformat(iso_ts).replace(tzinfo=timezone.utc)
        s = int((datetime.now(timezone.utc) - ts).total_seconds())
        if s < 60:
            return "now"
        if s < 3600:
            return f"{s // 60}m"
        if s < 86400:
            return f"{s // 3600}h"
        return f"{s // 86400}d"
    except Exception:
        return "?"


def _query_paused() -> bool:
    try:
        conn = make_client()
        send(conn, {"cmd": "status"})
        result = recv(conn)
        conn.close()
        return result.get("paused", False)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Dashboard window
# ---------------------------------------------------------------------------

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

        # StringVars updated by _refresh — nullified in destroy().
        self._last_auth_var: tk.StringVar | None = None
        self._stats_var: tk.StringVar | None = None
        self._recent_frame: tk.Frame | None = None

        self._build()
        center_window(self)
        self.bind("<FocusOut>", self._on_focus_out)
        self.after(2000, self._refresh)

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

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

        # -- Status card --------------------------------------------------
        self._status_frame = tk.Frame(body, bd=1, relief="solid",
                                      bg="#f4f4f4", padx=14, pady=10)
        self._status_frame.pack(fill="x", pady=(0, 10))

        status_header = tk.Frame(self._status_frame, bg="#f4f4f4")
        status_header.pack(fill="x")
        ttk.Label(status_header, text="Status",
                  font=("Segoe UI", 9, "bold"),
                  background="#f4f4f4").pack(side="left", anchor="w")

        # Active preset shown on the right of the status card header.
        preset = get_active_preset(config.SETTINGS_PATH)
        tk.Label(status_header, text=preset,
                 font=("Segoe UI", 9), bg="#f4f4f4",
                 fg="#888888").pack(side="right", anchor="e")

        self._status_dot = tk.Label(self._status_frame, font=("Segoe UI", 11),
                                    background="#f4f4f4")
        self._status_dot.pack(anchor="w", pady=(2, 0))
        self._update_status_card()

        # -- Two-column card: Account | Today ----------------------------
        two_col = tk.Frame(body, bd=1, relief="solid", bg="#f4f4f4")
        two_col.pack(fill="x", pady=(0, 10))

        # Left: account info
        acct = tk.Frame(two_col, bg="#f4f4f4", padx=14, pady=10)
        acct.pack(side="left", fill="both", expand=True)

        ttk.Label(acct, text="Account",
                  font=("Segoe UI", 9, "bold"),
                  background="#f4f4f4").pack(anchor="w")

        enrolled = has_consent(config.DB_PATH, self._username)
        user_row = tk.Frame(acct, bg="#f4f4f4")
        user_row.pack(fill="x", pady=(4, 0))

        tk.Label(user_row, text=self._username,
                 font=("Segoe UI", 11, "bold"),
                 background="#f4f4f4").pack(side="left")

        badge_color = "#1a8f1a" if enrolled else "#cc0000"
        badge_text  = "Enrolled" if enrolled else "Not enrolled"
        tk.Label(user_row, text=f"  {badge_text}",
                 foreground=badge_color, font=("Segoe UI", 9, "bold"),
                 background="#f4f4f4").pack(side="left")

        # Fallback badge (PIN / Windows — hidden when None)
        fb = _fallback_label(self._username)
        if fb:
            tk.Label(user_row, text=f"  {fb}",
                     foreground="#555555", font=("Segoe UI", 9),
                     background="#f4f4f4").pack(side="left")

        if enrolled:
            self._last_auth_var = tk.StringVar(
                master=self,
                value=f"Last auth:  {_last_auth_label(self._username)}")
            tk.Label(acct, textvariable=self._last_auth_var,
                     font=("Segoe UI", 9), foreground="#666666",
                     background="#f4f4f4").pack(anchor="w", pady=(2, 0))
        else:
            self._last_auth_var = None

        # Divider between columns
        tk.Frame(two_col, bg="#e0e0e0", width=1).pack(
            side="left", fill="y", pady=8)

        # Right: today's stats
        stats = tk.Frame(two_col, bg="#f4f4f4", padx=14, pady=10)
        stats.pack(side="left", fill="both")

        ttk.Label(stats, text="Today",
                  font=("Segoe UI", 9, "bold"),
                  background="#f4f4f4").pack(anchor="w")

        self._stats_var = tk.StringVar(master=self)
        tk.Label(stats, textvariable=self._stats_var,
                 font=("Segoe UI", 9), foreground="#444444",
                 background="#f4f4f4", justify="left").pack(anchor="w", pady=(4, 0))
        self._refresh_stats()

        # -- Recent Activity card ----------------------------------------
        recent_card = tk.Frame(body, bd=1, relief="solid",
                               bg="#f4f4f4", padx=14, pady=10)
        recent_card.pack(fill="x", pady=(0, 12))
        ttk.Label(recent_card, text="Recent Activity",
                  font=("Segoe UI", 9, "bold"),
                  background="#f4f4f4").pack(anchor="w")

        # Inner frame holds the event chips — rebuilt in _refresh_recent.
        self._recent_frame = tk.Frame(recent_card, bg="#f4f4f4")
        self._recent_frame.pack(anchor="w", pady=(4, 0))
        self._refresh_recent()

        # -- Buttons ------------------------------------------------------
        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=20)
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
    # State updates
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

    def _refresh_stats(self) -> None:
        """Update the Today stats label from live DB data."""
        if not self._stats_var:
            return
        s = _today_stats(self._username)
        if s["total"] == 0:
            self._stats_var.set("No activity today")
        else:
            line1 = f"{s['total']} auth{'s' if s['total'] != 1 else ''}"
            line2 = (f"{s['failures']} fail  {s['pct']}% ok"
                     if s["failures"] else f"{s['pct']}% ok")
            self._stats_var.set(f"{line1}\n{line2}")

    def _refresh_recent(self) -> None:
        """Rebuild the recent activity chips from live DB data."""
        if not self._recent_frame:
            return
        for w in self._recent_frame.winfo_children():
            w.destroy()

        events = _recent_events(self._username)
        if not events:
            tk.Label(self._recent_frame, text="No activity yet",
                     font=("Segoe UI", 9), foreground="#aaaaaa",
                     background="#f4f4f4").pack(side="left")
            return

        for ts, result in events:
            is_pass = result == "pass"
            symbol = "✓" if is_pass else "✗"
            color  = "#1a8f1a" if is_pass else "#cc0000"
            age    = _event_age_label(ts)
            tk.Label(self._recent_frame,
                     text=f"{symbol} {age}",
                     font=("Segoe UI", 9, "bold"),
                     foreground=color, background="#f4f4f4",
                     padx=6).pack(side="left")

    def _refresh(self) -> None:
        """Periodic refresh of all live data (every 2 s)."""
        if self._last_auth_var:
            self._last_auth_var.set(
                f"Last auth:  {_last_auth_label(self._username)}")
        self._refresh_stats()
        self._refresh_recent()
        self.after(2000, self._refresh)

    # ------------------------------------------------------------------
    # Focus / close
    # ------------------------------------------------------------------

    def _on_focus_out(self, event) -> None:
        if event.widget is self:
            self.after(100, self._check_focus)

    def _check_focus(self) -> None:
        if self.focus_displayof() is None:
            self.destroy()

    def destroy(self) -> None:
        # Nullify StringVars before Tcl teardown to prevent GC thread crash.
        self._last_auth_var = None
        self._stats_var = None
        self._recent_frame = None
        super().destroy()

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------

    def _toggle_pause(self) -> None:
        self._on_pause_toggle()
        self._paused = not self._paused
        self._update_status_card()
        self._pause_btn.configure(text=self._pause_label())

    def _settings(self) -> None:
        # The settings thread calls _close_all_windows() which destroys this
        # dashboard — no need to schedule a redundant destroy here.
        self._on_open_settings()

    def _enroll(self) -> None:
        self._on_open_enroll()

    def _debug(self) -> None:
        self._on_open_debug()
        self.after(50, self.destroy)  # debug is a subprocess, so close manually

    def _quit(self) -> None:
        self._on_quit()
        self.after(50, self.destroy)


def launch(locked: bool, paused: bool,
           on_pause_toggle, on_quit,
           on_open_settings, on_open_enroll, on_open_debug) -> None:
    app = Dashboard(locked, paused, on_pause_toggle, on_quit,
                    on_open_settings, on_open_enroll, on_open_debug)
    app.mainloop()
