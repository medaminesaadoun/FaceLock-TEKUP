# modules/system_controller.py
import ctypes
import subprocess
import time
import getpass

import config
from modules.ipc import make_client, send, recv


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


def run_mode_a(poll_interval: float = 1.0, absence_threshold: int = 5) -> None:
    """Continuously monitors presence; locks after consecutive absences, unlocks on auth."""
    username = _username()
    locked = False
    absence_streak = 0
    while True:
        if not locked:
            if _presence():
                absence_streak = 0
            else:
                absence_streak += 1
                if absence_streak >= absence_threshold:
                    _lock_workstation()
                    locked = True
                    absence_streak = 0
        else:
            if _auth(username):
                locked = False
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
