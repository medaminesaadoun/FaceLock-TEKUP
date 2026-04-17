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


import tempfile
from modules.database import (
    initialize, check_integrity, add_user, get_user,
    save_embedding, get_embedding, log_auth_event, erase_user
)

def _tmp_db():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    return f.name

def test_database_initializes_and_passes_integrity():
    db = _tmp_db()
    initialize(db)
    assert check_integrity(db)
    os.unlink(db)

def test_add_and_get_user():
    db = _tmp_db()
    initialize(db)
    add_user(db, "alice", "2026-04-17T00:00:00", "1.0", "none", None)
    user = get_user(db, "alice")
    assert user["windows_username"] == "alice"
    os.unlink(db)

def test_save_and_get_embedding():
    db = _tmp_db()
    initialize(db)
    add_user(db, "alice", "2026-04-17T00:00:00", "1.0", "none", None)
    user = get_user(db, "alice")
    save_embedding(db, user["id"], b"fake_encrypted_blob")
    assert get_embedding(db, user["id"]) == b"fake_encrypted_blob"
    os.unlink(db)

def test_erase_user_removes_all_records():
    db = _tmp_db()
    initialize(db)
    add_user(db, "alice", "2026-04-17T00:00:00", "1.0", "none", None)
    user = get_user(db, "alice")
    save_embedding(db, user["id"], b"fake_encrypted_blob")
    log_auth_event(db, "alice", "pass", "core")
    erase_user(db, "alice")
    assert get_user(db, "alice") is None
    assert get_embedding(db, user["id"]) is None
    os.unlink(db)


def test_update_last_used_sets_timestamp():
    from modules.database import get_connection, update_last_used
    db = _tmp_db()
    initialize(db)
    add_user(db, "alice", "2026-04-17T00:00:00", "1.0", "none", None)
    user = get_user(db, "alice")
    save_embedding(db, user["id"], b"fake_blob")
    update_last_used(db, user["id"])
    conn = get_connection(db)
    row = conn.execute(
        "SELECT last_used_at FROM embeddings WHERE user_id = ?", (user["id"],)
    ).fetchone()
    conn.close()
    assert row["last_used_at"] is not None
    os.unlink(db)
