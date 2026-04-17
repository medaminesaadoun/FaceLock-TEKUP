# modules/ipc.py
from multiprocessing.connection import Listener, Client, Connection
from typing import Any

import config


def make_server() -> Listener:
    return Listener(config.PIPE_NAME, authkey=config.PIPE_AUTHKEY)


def make_client() -> Connection:
    return Client(config.PIPE_NAME, authkey=config.PIPE_AUTHKEY)


def send(conn: Connection, msg: dict) -> None:
    conn.send(msg)


def recv(conn: Connection) -> Any:
    return conn.recv()
