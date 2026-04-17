# tests/test_encoding.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from modules.encryption import generate_key, encrypt, decrypt, secure_clear

def test_key_is_256_bits():
    assert len(generate_key()) == 32

def test_encrypt_decrypt_roundtrip():
    key = generate_key()
    plaintext = b"test embedding bytes"
    assert decrypt(key, encrypt(key, plaintext)) == plaintext

def test_ciphertext_does_not_contain_plaintext():
    key = generate_key()
    plaintext = b"test embedding bytes"
    ciphertext = encrypt(key, plaintext)
    assert plaintext not in ciphertext

def test_secure_clear_zeros_buffer():
    buf = bytearray(b"sensitive face data")
    secure_clear(buf)
    assert all(b == 0 for b in buf)
