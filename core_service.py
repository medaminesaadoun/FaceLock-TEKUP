# core_service.py
import os
import threading
import time
import logging
import cv2
import numpy as np

import config
from modules.database import (
    initialize, get_user, get_embedding, save_embedding,
    log_auth_event, update_last_used,
)
from modules.encryption import generate_key, save_key, load_key, encrypt, decrypt
from modules.face_detector import FaceDetector
from modules.face_encoder import (
    extract_embedding, average_embeddings,
    embedding_to_bytes, bytes_to_embedding,
)
from modules.authenticator import Authenticator
from modules.ipc import make_server, send, recv
from modules.gdpr import setup_audit_logger
from modules.user_settings import get_tolerance

log = setup_audit_logger(config.LOG_PATH)

# Serialises all camera access — only one operation may hold the camera at a time.
_camera_lock = threading.Lock()

_paused = False
_paused_lock = threading.Lock()


def _open_camera() -> cv2.VideoCapture:
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        raise RuntimeError("Cannot open camera")
    return cap


def _load_stored_embedding(username: str) -> np.ndarray | None:
    user = get_user(config.DB_PATH, username)
    if not user:
        return None
    blob = get_embedding(config.DB_PATH, user["id"])
    if not blob:
        return None
    key = load_key(config.KEY_PATH)
    return bytes_to_embedding(decrypt(key, blob))


def _handle_auth(username: str, detector: FaceDetector) -> dict:
    embedding = _load_stored_embedding(username)
    if embedding is None:
        return {"ok": False, "reason": "not_enrolled"}

    user = get_user(config.DB_PATH, username)
    tolerance = get_tolerance(config.SETTINGS_PATH)
    auth = Authenticator(embedding, tolerance)

    with _camera_lock:
        cap = _open_camera()
        try:
            deadline = time.monotonic() + config.AUTO_LOCK_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                ret, frame = cap.read()
                if not ret:
                    time.sleep(0.1)
                    continue
                small = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
                boxes = detector.find_faces(small)
                if len(boxes) != 1:
                    auth.reset()
                    time.sleep(0.1)
                    continue
                live_emb = extract_embedding(small, boxes[0])
                if live_emb is None:
                    time.sleep(0.1)
                    continue
                if auth.feed(live_emb):
                    log_auth_event(config.DB_PATH, username, "pass", "face")
                    update_last_used(config.DB_PATH, user["id"])
                    return {"ok": True}
                time.sleep(0.1)
        finally:
            cap.release()

    log_auth_event(config.DB_PATH, username, "fail", "face")
    return {"ok": False, "reason": "timeout"}


def _handle_enroll(conn, username: str, detector: FaceDetector) -> dict:
    user = get_user(config.DB_PATH, username)
    if not user:
        return {"ok": False, "reason": "no_consent"}

    embeddings: list[np.ndarray] = []

    with _camera_lock:
        cap = _open_camera()
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        try:
            deadline = time.monotonic() + config.AUTO_LOCK_TIMEOUT_SECONDS
            last_capture = 0.0
            while len(embeddings) < config.ENROLLMENT_FRAMES:
                if time.monotonic() > deadline:
                    return {"ok": False, "reason": "timeout"}
                ret, frame = cap.read()
                if not ret:
                    continue
                small = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
                boxes_small = detector.find_faces(small)
                boxes = [(x*2, y*2, w*2, h*2) for x, y, w, h in boxes_small]
                if len(boxes_small) == 1:
                    now = time.monotonic()
                    if now - last_capture >= config.ENROLLMENT_CAPTURE_INTERVAL:
                        emb = extract_embedding(small, boxes_small[0])
                        if emb is not None:
                            embeddings.append(emb)
                            last_capture = now
                _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 50])
                try:
                    send(conn, {
                        "jpeg": buf.tobytes(),
                        "boxes": boxes,
                        "progress": len(embeddings),
                        "total": config.ENROLLMENT_FRAMES,
                    })
                except Exception:
                    return {"ok": False, "reason": "connection_lost"}
        finally:
            cap.release()

    averaged = average_embeddings(embeddings)
    raw_bytes = embedding_to_bytes(averaged)

    if not os.path.exists(config.KEY_PATH):
        key = generate_key()
        save_key(key, config.KEY_PATH)
    else:
        key = load_key(config.KEY_PATH)

    save_embedding(config.DB_PATH, user["id"], encrypt(key, raw_bytes))
    return {"ok": True}


def _handle_check_camera() -> dict:
    with _camera_lock:
        try:
            cap = _open_camera()
            ret, _ = cap.read()
            cap.release()
            if not ret:
                return {"ok": False, "reason": "Camera opened but could not read a frame"}
            return {"ok": True}
        except RuntimeError as exc:
            return {"ok": False, "reason": str(exc)}


def _handle_presence(detector: FaceDetector) -> dict:
    with _paused_lock:
        if _paused:
            return {"present": True}
    with _camera_lock:
        cap = _open_camera()
        try:
            ret, frame = cap.read()
            if ret:
                small = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
                present = detector.has_exactly_one_face(small)
            else:
                present = False
            return {"present": present}
        finally:
            cap.release()


def _handle_pause() -> dict:
    global _paused
    with _paused_lock:
        _paused = True
    return {"ok": True}


def _handle_resume() -> dict:
    global _paused
    with _paused_lock:
        _paused = False
    return {"ok": True}


def _handle_status() -> dict:
    with _paused_lock:
        return {"paused": _paused}


def _handle_debug_stream(conn, detector: FaceDetector) -> None:
    """Stream frames continuously, including embeddings, over one persistent connection."""
    with _camera_lock:
        cap = _open_camera()
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        try:
            face_frame_counter = 0
            last_embedding_bytes = None
            while True:
                ret, frame = cap.read()
                if not ret:
                    time.sleep(0.05)
                    continue
                small = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
                boxes_small = detector.find_faces(small)
                boxes = [(x*2, y*2, w*2, h*2) for x, y, w, h in boxes_small]
                if len(boxes_small) == 1:
                    face_frame_counter += 1
                    if face_frame_counter % 3 == 0:
                        emb = extract_embedding(small, boxes_small[0])
                        if emb is not None:
                            last_embedding_bytes = embedding_to_bytes(emb)
                else:
                    face_frame_counter = 0
                    last_embedding_bytes = None
                _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 50])
                try:
                    send(conn, {
                        "ok": True,
                        "jpeg": buf.tobytes(),
                        "boxes": boxes,
                        "embedding": last_embedding_bytes,
                    })
                except Exception:
                    break
                time.sleep(0.067)  # ~15fps
        finally:
            cap.release()


def _handle_client(conn, detector: FaceDetector) -> None:
    try:
        msg = recv(conn)
        cmd = msg.get("cmd")
        username = msg.get("username", "")
        if cmd == "auth":
            send(conn, _handle_auth(username, detector))
        elif cmd == "enroll":
            send(conn, _handle_enroll(conn, username, detector))
        elif cmd == "presence":
            send(conn, _handle_presence(detector))
        elif cmd == "debug_stream":
            _handle_debug_stream(conn, detector)
            return  # connection already closed inside stream handler
        elif cmd == "check_camera":
            send(conn, _handle_check_camera())
        elif cmd == "pause":
            send(conn, _handle_pause())
        elif cmd == "resume":
            send(conn, _handle_resume())
        elif cmd == "status":
            send(conn, _handle_status())
        else:
            send(conn, {"ok": False, "reason": "unknown_cmd"})
    except Exception as exc:
        log.error("client handler error: %s", exc)
        try:
            send(conn, {"ok": False, "reason": "internal_error"})
        except Exception:
            pass
    finally:
        conn.close()


def run() -> None:
    initialize(config.DB_PATH)
    detector = FaceDetector(config.TFLITE_MODEL_PATH)
    server = make_server()
    log.info("core service started")
    try:
        while True:
            conn = server.accept()
            threading.Thread(
                target=_handle_client, args=(conn, detector), daemon=True
            ).start()
    except KeyboardInterrupt:
        log.info("core service stopped")
    finally:
        server.close()


if __name__ == "__main__":
    run()
