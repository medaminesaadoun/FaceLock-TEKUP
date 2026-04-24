# modules/system_controller.py
import ctypes
import subprocess
import time
import getpass

import config
from modules.ipc import make_client, send, recv
from modules.gdpr import has_consent


def _username() -> str:
    return getpass.getuser()


def _request(msg: dict) -> dict:
    conn = make_client()
    try:
        send(conn, msg)
        return recv(conn)
    finally:
        conn.close()


def _auth(username: str) -> bool:
    return _request({"cmd": "auth", "username": username}).get("ok", False)


def _presence() -> bool:
    return _request({"cmd": "presence"}).get("present", False)


# ---------------------------------------------------------------------------
# Mode A — Session locker: lock when user leaves, unlock when face matches
# ---------------------------------------------------------------------------

def _lock_workstation() -> None:
    ctypes.windll.user32.LockWorkStation()


def run_mode_a(poll_interval: float = 1.0) -> None:
    """Monitors presence; shows overlay lock on absence, falls back to Windows lock on timeout.

    All timing values are read from user settings each iteration so changes
    in the Settings window take effect without restarting the app.
    """
    from modules.user_settings import load as load_settings

    username = _username()
    absence_streak = 0
    lock_time: float | None = None       # monotonic timestamp when lock was sent
    grace_until: float | None = None     # monotonic timestamp when grace period ends
    prev_locked = False                  # previous locked state for transition detection

    while True:
        # Re-read settings each cycle so UI changes apply immediately.
        settings = load_settings(config.SETTINGS_PATH)
        lock_timeout = int(settings.get("lock_timeout", 5))
        unlock_grace = float(settings.get("unlock_grace", 10))
        auth_fallback_timeout = float(settings.get("auth_fallback_timeout", 120))

        # Skip monitoring entirely if user hasn't consented.
        if not has_consent(config.DB_PATH, username):
            absence_streak = 0
            lock_time = None
            grace_until = None
            time.sleep(poll_interval)
            continue

        # Poll the core service for current lock state — the overlay may have
        # already unlocked it via face auth.
        status = _request({"cmd": "status"})
        locked = status.get("locked", False)

        if not locked:
            if prev_locked:
                # Unlock transition detected — start grace period so the user
                # isn't immediately re-locked before settling back at their desk.
                grace_until = time.monotonic() + unlock_grace
                lock_time = None
                absence_streak = 0

            if grace_until and time.monotonic() < grace_until:
                # Grace period active — skip presence monitoring.
                pass
            else:
                grace_until = None
                # Monitor presence to detect when user leaves.
                if _presence():
                    absence_streak = 0
                else:
                    absence_streak += 1
                    if absence_streak >= lock_timeout:
                        from modules.notifications import notify
                        notify("FaceLock — Locked",
                               "No face detected. Look at the camera to unlock.")
                        # Signal core service to lock — tray will show overlay.
                        _request({"cmd": "lock"})
                        lock_time = time.monotonic()
                        absence_streak = 0
        else:
            # Locked: fall back to Windows lock if face auth takes too long.
            if lock_time and time.monotonic() - lock_time > auth_fallback_timeout:
                _lock_workstation()
                # Clean up lock state so the tray stops showing the overlay.
                _request({"cmd": "unlock"})
                lock_time = None

        prev_locked = locked
        time.sleep(poll_interval)


# ---------------------------------------------------------------------------
# Mode B — App guard: authenticate before launching a wrapped application
# ---------------------------------------------------------------------------

def run_mode_b(app_cmd: list[str]) -> bool:
    """Authenticate user, then launch app_cmd. Returns False if auth fails."""
    username = _username()
    if not _auth(username):
        return False
    subprocess.Popen(app_cmd)
    return True


# ---------------------------------------------------------------------------
# Mode C1 — Post-login startup gate: block until auth succeeds
# ---------------------------------------------------------------------------

def run_mode_c1() -> None:
    """Block until the enrolled user authenticates. Intended for startup."""
    username = _username()
    while not _auth(username):
        time.sleep(1)
