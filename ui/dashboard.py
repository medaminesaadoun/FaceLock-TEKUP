# ui/dashboard.py
import getpass
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime, timezone

import config
from modules.database import (
    get_user, get_connection, get_embeddings,
    delete_embedding_by_id, rename_embedding,
)
from modules.gdpr import has_consent
from modules.ipc import make_client, send, recv
from modules.user_settings import get_active_preset
from ui._theme import apply as apply_theme, center as center_window


# ---------------------------------------------------------------------------
# Data helpers — all return safe defaults on any DB/IPC failure
# ---------------------------------------------------------------------------

def _last_auth_label(username: str) -> str:
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
        s = int((datetime.now(timezone.utc) - ts).total_seconds())
        if s < 60:   return "Just now"
        if s < 3600: return f"{s // 60} min ago"
        if s < 86400:return f"{s // 3600} h ago"
        return f"{s // 86400} days ago"
    except Exception:
        return "Unknown"


def _today_stats(username: str) -> dict:
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
    try:
        user = get_user(config.DB_PATH, username)
        if not user:
            return ""
        return {"pin": "PIN", "windows": "Windows", "none": ""
                }.get(user.get("fallback_method", "none"), "")
    except Exception:
        return ""


def _event_age_label(iso_ts: str) -> str:
    try:
        ts = datetime.fromisoformat(iso_ts).replace(tzinfo=timezone.utc)
        s = int((datetime.now(timezone.utc) - ts).total_seconds())
        if s < 60:    return "now"
        if s < 3600:  return f"{s // 60}m"
        if s < 86400: return f"{s // 3600}h"
        return f"{s // 86400}d"
    except Exception:
        return "?"


def _face_age_label(iso_ts: str | None) -> str:
    """Human-readable last-used label for an enrolled face."""
    if not iso_ts:
        return "never used"
    try:
        ts = datetime.fromisoformat(iso_ts).replace(tzinfo=timezone.utc)
        s = int((datetime.now(timezone.utc) - ts).total_seconds())
        if s < 60:    return "just now"
        if s < 3600:  return f"{s // 60} min ago"
        if s < 86400: return f"{s // 3600} h ago"
        return f"{s // 86400} days ago"
    except Exception:
        return "?"


def _get_faces(username: str) -> list[dict]:
    """Return [{id, name, last_used_label}] for all enrolled faces."""
    try:
        user = get_user(config.DB_PATH, username)
        if not user:
            return []
        rows = get_embeddings(config.DB_PATH, user["id"])
        result = []
        with get_connection(config.DB_PATH) as conn:
            for emb_id, _, name in rows:
                row = conn.execute(
                    "SELECT last_used_at FROM embeddings WHERE id = ?",
                    (emb_id,)
                ).fetchone()
                last_used = row["last_used_at"] if row else None
                result.append({
                    "id":       emb_id,
                    "name":     name or "Unnamed",
                    "last_used": _face_age_label(last_used),
                })
        return result
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Dashboard window
# ---------------------------------------------------------------------------

class Dashboard(tk.Tk):
    def __init__(self, locked: bool, paused: bool,
                 on_pause_toggle, on_quit, on_open_settings,
                 on_open_enroll, on_open_add_user, on_open_debug) -> None:
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
        self._on_open_add_user = on_open_add_user
        self._on_open_debug = on_open_debug

        self._last_auth_var: tk.StringVar | None = None
        self._stats_var:     tk.StringVar | None = None
        self._camera_var:    tk.StringVar | None = None
        self._badge_var:     tk.StringVar | None = None
        self._recent_frame:  tk.Frame | None = None
        self._faces_inner:   tk.Frame | None = None
        self._cached_faces:  list[dict] = []  # fallback cache on DB error
        # Counter incremented while a dialog/popup is open so the FocusOut
        # handler does not destroy the dashboard behind the dialog.
        self._dialog_count:  int = 0

        self._build()
        center_window(self)
        self.bind("<FocusOut>", self._on_focus_out)
        self.after(2000, self._refresh)
        self.after(500,  self._refresh_camera)

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build(self) -> None:
        tk.Frame(self, bg="#1a73e8", height=4).pack(fill="x")

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

        # ---- Status card ------------------------------------------------
        self._status_frame = tk.Frame(body, bd=1, relief="solid",
                                      bg="#f4f4f4", padx=14, pady=10)
        self._status_frame.pack(fill="x", pady=(0, 10))

        status_header = tk.Frame(self._status_frame, bg="#f4f4f4")
        status_header.pack(fill="x")
        ttk.Label(status_header, text="Status",
                  font=("Segoe UI", 9, "bold"),
                  background="#f4f4f4").pack(side="left", anchor="w")

        preset = get_active_preset(config.SETTINGS_PATH)
        tk.Label(status_header, text=preset,
                 font=("Segoe UI", 9), bg="#f4f4f4",
                 fg="#888888").pack(side="right", anchor="e")

        self._status_dot = tk.Label(self._status_frame, font=("Segoe UI", 11),
                                    background="#f4f4f4")
        self._status_dot.pack(anchor="w", pady=(2, 0))
        self._update_status_card()

        self._camera_var = tk.StringVar(master=self, value="📷  Checking camera…")
        tk.Label(self._status_frame, textvariable=self._camera_var,
                 font=("Segoe UI", 9), background="#f4f4f4",
                 foreground="#888888").pack(anchor="w", pady=(2, 0))

        # ---- Account | Today two-column card ----------------------------
        two_col = tk.Frame(body, bd=1, relief="solid", bg="#f4f4f4")
        two_col.pack(fill="x", pady=(0, 10))

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

        # Dynamic badge — count updates in _refresh_faces().
        badge_color = "#1a8f1a" if enrolled else "#cc0000"
        initial_badge = f"  {self._badge_text()}"
        self._badge_var = tk.StringVar(master=self, value=initial_badge)
        self._badge_lbl = tk.Label(user_row, textvariable=self._badge_var,
                                   foreground=badge_color,
                                   font=("Segoe UI", 9, "bold"),
                                   background="#f4f4f4")
        self._badge_lbl.pack(side="left")

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

        tk.Frame(two_col, bg="#e0e0e0", width=1).pack(
            side="left", fill="y", pady=8)

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

        # ---- Enrolled Faces card ----------------------------------------
        faces_card = tk.Frame(body, bd=1, relief="solid",
                              bg="#f4f4f4", padx=14, pady=10)
        faces_card.pack(fill="x", pady=(0, 10))
        ttk.Label(faces_card, text="Enrolled Faces",
                  font=("Segoe UI", 9, "bold"),
                  background="#f4f4f4").pack(anchor="w")

        # Inner frame rebuilt on each refresh.
        self._faces_inner = tk.Frame(faces_card, bg="#f4f4f4")
        self._faces_inner.pack(fill="x", pady=(4, 0))
        self._refresh_faces()

        # ---- Recent Activity card ---------------------------------------
        recent_card = tk.Frame(body, bd=1, relief="solid",
                               bg="#f4f4f4", padx=14, pady=10)
        recent_card.pack(fill="x", pady=(0, 12))
        ttk.Label(recent_card, text="Recent Activity",
                  font=("Segoe UI", 9, "bold"),
                  background="#f4f4f4").pack(anchor="w")

        self._recent_frame = tk.Frame(recent_card, bg="#f4f4f4")
        self._recent_frame.pack(anchor="w", pady=(4, 0))
        self._refresh_recent()

        # ---- Buttons ----------------------------------------------------
        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=20)
        btn_row = ttk.Frame(self, padding=(20, 10, 20, 14))
        btn_row.pack(fill="x")

        self._pause_btn = ttk.Button(btn_row, text=self._pause_label(),
                                     command=self._toggle_pause)
        self._pause_btn.pack(side="left", padx=(0, 6))

        ttk.Button(btn_row, text="Settings",
                   command=self._settings).pack(side="left", padx=(0, 6))
        self._enroll_btn = ttk.Button(btn_row, text=self._enroll_label(),
                                      command=self._enroll)
        self._enroll_btn.pack(side="left", padx=(0, 6))
        ttk.Button(btn_row, text="Add User",
                   command=self._add_user).pack(side="left", padx=(0, 6))
        ttk.Button(btn_row, text="Debug",
                   command=self._debug).pack(side="left", padx=(0, 6))
        ttk.Button(btn_row, text="Quit",
                   command=self._quit).pack(side="right")

    # ------------------------------------------------------------------
    # Enrolled Faces helpers
    # ------------------------------------------------------------------

    def _badge_text(self) -> str:
        """Return the badge string for the enrolled status."""
        enrolled = has_consent(config.DB_PATH, self._username)
        if not enrolled:
            return "Not enrolled"
        try:
            user = get_user(config.DB_PATH, self._username)
            count = len(get_embeddings(config.DB_PATH, user["id"])) if user else 0
            return f"Enrolled ({count})"
        except Exception:
            return "Enrolled"

    def _refresh_faces(self) -> None:
        """Rebuild the Enrolled Faces card from live DB data."""
        if not self._faces_inner:
            return

        # Fetch fresh data; fall back to cache on error.
        try:
            faces = _get_faces(self._username)
            self._cached_faces = faces
            stale = False
        except Exception:
            faces = self._cached_faces
            stale = True

        # Update the badge count.
        if self._badge_var:
            self._badge_var.set(f"  {self._badge_text()}")
            enrolled = has_consent(config.DB_PATH, self._username)
            self._badge_lbl.configure(
                foreground="#1a8f1a" if enrolled else "#cc0000")

        # Rebuild the inner frame.
        for w in self._faces_inner.winfo_children():
            w.destroy()

        if not faces:
            tk.Label(self._faces_inner, text="No faces enrolled",
                     font=("Segoe UI", 9), foreground="#aaaaaa",
                     background="#f4f4f4").pack(anchor="w")
            return

        for face in faces:
            row = tk.Frame(self._faces_inner, bg="#f4f4f4")
            row.pack(fill="x", pady=(0, 4))

            # Name — double-click to rename.
            name_lbl = tk.Label(row, text=face["name"],
                                font=("Segoe UI", 10, "bold"),
                                bg="#f4f4f4", fg="#222222", width=14, anchor="w")
            name_lbl.pack(side="left")
            name_lbl.bind("<Double-Button-1>",
                          lambda e, fid=face["id"], fn=face["name"]:
                          self._rename_popup(fid, fn))

            tk.Label(row, text=face["last_used"],
                     font=("Segoe UI", 9), bg="#f4f4f4",
                     fg="#888888").pack(side="left", padx=(4, 12))

            tk.Button(row, text="Rename",
                      font=("Segoe UI", 8), bg="#f4f4f4", relief="flat",
                      fg="#1a73e8", cursor="hand2", bd=0,
                      command=lambda fid=face["id"], fn=face["name"]:
                      self._rename_popup(fid, fn)
                      ).pack(side="left", padx=(0, 4))

            tk.Button(row, text="Delete",
                      font=("Segoe UI", 8), bg="#f4f4f4", relief="flat",
                      fg="#cc0000", cursor="hand2", bd=0,
                      command=lambda fid=face["id"], fn=face["name"]:
                      self._delete_face(fid, fn)
                      ).pack(side="left")

        if stale:
            tk.Label(self._faces_inner, text="⚠ refresh failed — showing cached data",
                     font=("Segoe UI", 8), fg="#aaaaaa",
                     background="#f4f4f4").pack(anchor="w", pady=(4, 0))

    def _rename_popup(self, embedding_id: int, current_name: str) -> None:
        """Open a Toplevel popup to rename an enrolled face."""
        self._dialog_count += 1
        popup = tk.Toplevel(self)
        popup.title("Rename Face")
        popup.resizable(False, False)
        popup.grab_set()

        ttk.Label(popup, text="New name:", padding=(12, 10, 12, 4)).pack(anchor="w")
        var = tk.StringVar(master=popup, value=current_name)
        entry = ttk.Entry(popup, textvariable=var, width=22)
        entry.pack(padx=12, pady=(0, 8))
        entry.select_range(0, "end")
        entry.focus_set()

        def _close():
            self._dialog_count -= 1
            popup.destroy()

        def _confirm():
            new = var.get().strip()
            if new:
                rename_embedding(config.DB_PATH, embedding_id, new)
            _close()
            self._refresh_faces()

        entry.bind("<Return>", lambda e: _confirm())
        popup.protocol("WM_DELETE_WINDOW", _close)
        btn_row = ttk.Frame(popup, padding=(12, 0, 12, 12))
        btn_row.pack()
        ttk.Button(btn_row, text="Cancel",
                   command=_close).pack(side="left", padx=(0, 8))
        ttk.Button(btn_row, text="OK",
                   command=_confirm).pack(side="left")

        # Centre over dashboard.
        popup.update_idletasks()
        x = self.winfo_x() + (self.winfo_width()  - popup.winfo_width())  // 2
        y = self.winfo_y() + (self.winfo_height() - popup.winfo_height()) // 2
        popup.geometry(f"+{x}+{y}")

    def _delete_face(self, embedding_id: int, name: str) -> None:
        """Confirm and delete one enrolled face."""
        self._dialog_count += 1
        confirmed = messagebox.askyesno(
            "Delete Face",
            f"Remove enrolled face '{name}'?\n\n"
            "This cannot be undone. If this is the only face enrolled, "
            "you will need to re-enroll to use FaceLock.",
            parent=self,
        )
        self._dialog_count -= 1
        if confirmed:
            delete_embedding_by_id(config.DB_PATH, embedding_id)
            self._refresh_faces()

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

    def _enroll_label(self) -> str:
        return "Re-enroll" if has_consent(config.DB_PATH, self._username) else "Enroll"

    def _refresh_stats(self) -> None:
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
            color = "#1a8f1a" if is_pass else "#cc0000"
            tk.Label(self._recent_frame,
                     text=f"{'✓' if is_pass else '✗'} {_event_age_label(ts)}",
                     font=("Segoe UI", 9, "bold"),
                     foreground=color, background="#f4f4f4",
                     padx=6).pack(side="left")

    def _refresh_camera(self) -> None:
        if not self._camera_var:
            return
        try:
            conn = make_client()
            send(conn, {"cmd": "check_camera"})
            result = recv(conn)
            conn.close()
            if result.get("ok"):
                self._camera_var.set("📷  Camera ready")
                color = "#1a8f1a"
            else:
                self._camera_var.set(
                    f"📷  Camera unavailable — {result.get('reason', 'unknown')}")
                color = "#cc0000"
            for w in self._status_frame.winfo_children():
                if (isinstance(w, tk.Label) and self._camera_var
                        and w.cget("textvariable") == str(self._camera_var)):
                    w.configure(foreground=color)
                    break
        except Exception:
            if self._camera_var:
                self._camera_var.set("📷  Core service unreachable")
        self.after(5000, self._refresh_camera)

    def _refresh(self) -> None:
        if self._last_auth_var:
            self._last_auth_var.set(
                f"Last auth:  {_last_auth_label(self._username)}")
        self._refresh_stats()
        self._refresh_recent()
        self._refresh_faces()
        if hasattr(self, "_enroll_btn"):
            self._enroll_btn.configure(text=self._enroll_label())
        self.after(2000, self._refresh)

    # ------------------------------------------------------------------
    # Focus / close
    # ------------------------------------------------------------------

    def _on_focus_out(self, event) -> None:
        if event.widget is self:
            self.after(100, self._check_focus)

    def _check_focus(self) -> None:
        # Do not close while a messagebox or Toplevel popup is open —
        # those steal focus and would incorrectly trigger auto-close.
        if self._dialog_count > 0:
            return
        if self.focus_displayof() is None:
            self.destroy()

    def destroy(self) -> None:
        self._last_auth_var = None
        self._stats_var = None
        self._camera_var = None
        self._badge_var = None
        self._recent_frame = None
        self._faces_inner = None
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
        self._on_open_settings()

    def _enroll(self) -> None:
        self._on_open_enroll()

    def _add_user(self) -> None:
        self._on_open_add_user()

    def _debug(self) -> None:
        self._on_open_debug()
        self.after(50, self.destroy)

    def _quit(self) -> None:
        self._on_quit()
        self.after(50, self.destroy)


def launch(locked: bool, paused: bool,
           on_pause_toggle, on_quit,
           on_open_settings, on_open_enroll, on_open_add_user, on_open_debug) -> None:
    app = Dashboard(locked, paused, on_pause_toggle, on_quit,
                    on_open_settings, on_open_enroll, on_open_add_user, on_open_debug)
    app.mainloop()
