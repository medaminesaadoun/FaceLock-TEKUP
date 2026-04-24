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


# How long to attempt face auth via overlay before falling back to Windows lock.
# Two full auth cycles (AUTO_LOCK_TIMEOUT_SECONDS = 60) plus a small buffer.
_FACE_AUTH_TIMEOUT = 120


def run_mode_a(poll_interval: float = 1.0, absence_threshold: int = 5) -> None:
    """Monitors presence; shows overlay lock on absence, falls back to Windows lock on timeout."""
    username = _username()
    absence_streak = 0
    lock_time: float | None = None  # when we sent the lock command, None if not locked

    while True:
        # Skip monitoring entirely if user hasn't consented.
        if not has_consent(config.DB_PATH, username):
            absence_streak = 0
            lock_time = None
            time.sleep(poll_interval)
            continue

        # Poll the core service for current lock state — the overlay may have
        # already unlocked it via face auth.
        status = _request({"cmd": "status"})
        locked = status.get("locked", False)

        if not locked:
            # Reset lock timer if the overlay successfully unlocked.
            lock_time = None
            # Monitor presence to detect when user leaves.
            if _presence():
                absence_streak = 0
            else:
                absence_streak += 1
                if absence_streak >= absence_threshold:
                    from modules.notifications import notify
                    notify("FaceLock — Locked",
                           "No face detected. Look at the camera to unlock.")
                    # Signal core service to lock — tray will show the overlay.
                    _request({"cmd": "lock"})
                    lock_time = time.monotonic()
                    absence_streak = 0
        else:
            # Locked: check if the face auth timeout has expired.
            # If so, fall back to Windows lock screen as a hard fallback.
            if lock_time and time.monotonic() - lock_time > _FACE_AUTH_TIMEOUT:
                _lock_workstation()
                # Clean up lock state so the tray stops showing the overlay.
                _request({"cmd": "unlock"})
                lock_time = None

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
