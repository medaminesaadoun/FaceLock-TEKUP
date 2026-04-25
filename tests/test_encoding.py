# tests/test_encoding.py
# Encryption, database, and face encoding tests.
# Unit tests run offline; camera tests are marked with @pytest.mark.camera.
import os
import tempfile

import numpy as np
import pytest

import config
from modules.encryption import generate_key, encrypt, decrypt, secure_clear
from modules.database import (
    initialize, check_integrity, add_user, get_user,
    save_embedding, get_embedding, log_auth_event, erase_user,
    get_connection, update_last_used,
)


# ---------------------------------------------------------------------------
# Encryption unit tests
# ---------------------------------------------------------------------------

def test_key_is_256_bits():
    assert len(generate_key()) == 32


def test_encrypt_decrypt_roundtrip():
    key = generate_key()
    plaintext = b"test embedding bytes"
    assert decrypt(key, encrypt(key, plaintext)) == plaintext


def test_ciphertext_does_not_contain_plaintext():
    key = generate_key()
    plaintext = b"test embedding bytes"
    assert plaintext not in encrypt(key, plaintext)


def test_secure_clear_zeros_buffer():
    buf = bytearray(b"sensitive face data")
    secure_clear(buf)
    assert all(b == 0 for b in buf)


# ---------------------------------------------------------------------------
# Database unit tests
# ---------------------------------------------------------------------------

def _tmp_db() -> str:
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


# ---------------------------------------------------------------------------
# Live camera tests — TC1, TC6, TC8 (require webcam + person in frame)
# ---------------------------------------------------------------------------

import cv2
from modules.face_detector import FaceDetector
from modules.face_encoder import (
    extract_embedding, embedding_to_bytes, bytes_to_embedding, compare_embedding,
)


def _capture_embedding() -> np.ndarray:
    detector = FaceDetector(config.TFLITE_MODEL_PATH)
    cap = cv2.VideoCapture(0)
    assert cap.isOpened(), "Webcam not available"
    for _ in range(5):
        cap.read()
    ret, frame = cap.read()
    cap.release()
    assert ret, "Could not read frame from webcam"
    assert detector.has_exactly_one_face(frame), \
        "Exactly one face required — ensure only you are in frame"
    boxes = detector.find_faces(frame)
    emb = extract_embedding(frame, boxes[0])
    assert emb is not None, "Embedding extraction failed"
    return emb


@pytest.mark.camera
def test_tc1_embedding_is_128d():
    """TC1 — Extracted face embedding is 128-dimensional."""
    emb = _capture_embedding()
    assert emb.shape == (128,), f"Expected (128,), got {emb.shape}"


@pytest.mark.camera
def test_tc6_embedding_serialization_roundtrip():
    """TC6 — Embedding survives bytes serialization roundtrip."""
    emb = _capture_embedding()
    restored = bytes_to_embedding(embedding_to_bytes(emb))
    assert np.allclose(emb, restored), "Roundtrip mismatch"


@pytest.mark.camera
def test_tc8_same_face_matches_within_tolerance():
    """TC8 — Two embeddings captured seconds apart from the same face match."""
    emb1 = _capture_embedding()
    emb2 = _capture_embedding()
    assert compare_embedding(emb1, emb2, config.DEFAULT_TOLERANCE), \
        "Same face did not match — check lighting or camera position"
