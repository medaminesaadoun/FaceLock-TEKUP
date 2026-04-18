# modules/ipc.py
import os
import time
from multiprocessing.connection import Listener, Client, Connection
from typing import Any

import config

_RETRY_ATTEMPTS = 10
_RETRY_DELAY = 1.0


def _get_authkey() -> bytes:
    """Load pipe authkey from disk, generating it on first call."""
    os.makedirs(os.path.dirname(config.PIPE_AUTHKEY_PATH), exist_ok=True)
    if os.path.exists(config.PIPE_AUTHKEY_PATH):
        with open(config.PIPE_AUTHKEY_PATH, "rb") as f:
            return f.read()
    key = os.urandom(32)
    with open(config.PIPE_AUTHKEY_PATH, "wb") as f:
        f.write(key)
    return key


def make_server() -> Listener:
    return Listener(config.PIPE_NAME, authkey=_get_authkey())


def make_client() -> Connection:
    """Connect to the pipe server, retrying until the server is ready."""
    authkey = _get_authkey()
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            return Client(config.PIPE_NAME, authkey=authkey)
        except (ConnectionRefusedError, FileNotFoundError):
            if attempt == _RETRY_ATTEMPTS - 1:
                raise
            time.sleep(_RETRY_DELAY)


def send(conn: Connection, msg: dict) -> None:
    conn.send(msg)


def recv(conn: Connection) -> Any:
    return conn.recv()
