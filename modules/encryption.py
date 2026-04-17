# modules/encryption.py
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import win32crypt


def generate_key() -> bytes:
    return os.urandom(32)


def _protect(key: bytes) -> bytes:
    # DPAPI binds this key to the current Windows user account — only that user can unprotect it
    return win32crypt.CryptProtectData(key, None, None, None, None, 0)


def _unprotect(protected: bytes) -> bytes:
    return win32crypt.CryptUnprotectData(protected, None, None, None, 0)[1]


def save_key(key: bytes, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(_protect(key))


def load_key(path: str) -> bytes:
    with open(path, "rb") as f:
        return _unprotect(f.read())


def encrypt(key: bytes, plaintext: bytes) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, None)


def decrypt(key: bytes, data: bytes) -> bytes:
    return AESGCM(key).decrypt(data[:12], data[12:], None)


def secure_wipe(path: str) -> None:
    size = os.path.getsize(path)
    with open(path, "r+b") as f:
        f.write(b"\x00" * size)
    os.remove(path)


def secure_clear(buf: bytearray) -> None:
    for i in range(len(buf)):
        buf[i] = 0
