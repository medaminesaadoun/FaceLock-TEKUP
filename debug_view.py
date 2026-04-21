# debug_view.py
import os
import time
import getpass

os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
os.environ["GLOG_minloglevel"] = "3"

import cv2
if hasattr(cv2, "setLogLevel"):
    cv2.setLogLevel(0)

import numpy as np

import config
from modules.ipc import make_client, send, recv
from modules.face_encoder import bytes_to_embedding, compare_embedding
from modules.database import get_user, get_embedding, initialize
from modules.encryption import load_key, decrypt
from modules.authenticator import Authenticator


_GREEN  = (0, 220, 0)
_RED    = (0, 0, 220)
_YELLOW = (0, 200, 220)
_WHITE  = (255, 255, 255)
_BLACK  = (0, 0, 0)


def _load_stored_embedding() -> np.ndarray | None:
    try:
        user = get_user(config.DB_PATH, getpass.getuser())
        if not user:
            return None
        blob = get_embedding(config.DB_PATH, user["id"])
        if not blob:
            return None
        return bytes_to_embedding(decrypt(load_key(config.KEY_PATH), blob))
    except Exception:
        return None


def _get_debug_frame() -> dict:
    conn = make_client()
    try:
        send(conn, {"cmd": "debug_frame"})
        return recv(conn)
    finally:
        conn.close()


def _draw_text(frame, text: str, pos: tuple, color=_WHITE, scale: float = 0.6) -> None:
    x, y = pos
    cv2.putText(frame, text, (x + 1, y + 1), cv2.FONT_HERSHEY_SIMPLEX,
                scale, _BLACK, 2, cv2.LINE_AA)
    cv2.putText(frame, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX,
                scale, color, 1, cv2.LINE_AA)


def run() -> None:
    initialize(config.DB_PATH)
    stored = _load_stored_embedding()
    auth = Authenticator(stored) if stored is not None else None

    print("FaceLock Debug View — press Q to quit")
    print("(frames fetched from core service — make sure it is running)")

    prev_time = time.monotonic()

    while True:
        try:
            result = _get_debug_frame()
        except Exception as exc:
            print(f"Cannot reach core service: {exc}")
            time.sleep(1)
            continue

        if not result.get("ok"):
            continue

        frame = cv2.imdecode(np.frombuffer(result["jpeg"], np.uint8), cv2.IMREAD_COLOR)
        boxes = result["boxes"]
        face_count = len(boxes)

        now = time.monotonic()
        fps = 1.0 / max(now - prev_time, 1e-6)
        prev_time = now

        status_text = "NOT ENROLLED" if stored is None else "NO FACE"
        status_color = _WHITE
        distance_text = ""
        streak_text = ""

        for (x, y, w, h) in boxes:
            cv2.rectangle(frame, (x, y), (x + w, y + h), _YELLOW, 2)

        if face_count == 1 and stored is not None:
            from modules.face_encoder import extract_embedding
            emb = extract_embedding(frame, boxes[0])
            if emb is not None:
                dist = float(np.linalg.norm(stored - emb))
                match = dist <= config.DEFAULT_TOLERANCE
                box_color = _GREEN if match else _RED
                x, y, w, h = boxes[0]
                cv2.rectangle(frame, (x, y), (x + w, y + h), box_color, 2)

                granted = auth.feed(emb)
                status_text = "AUTHENTICATED" if granted else ("MATCH" if match else "NO MATCH")
                status_color = _GREEN if (match or granted) else _RED
                distance_text = f"Distance: {dist:.3f}  (threshold: {config.DEFAULT_TOLERANCE})"
                streak_text = f"Streak: {auth.streak} / {config.CONSECUTIVE_FRAMES_REQUIRED}"
        elif face_count == 0 and auth:
            auth.reset()
        elif face_count > 1:
            if auth:
                auth.reset()
            status_text = f"MULTIPLE FACES ({face_count})"
            status_color = _YELLOW

        h_frame = frame.shape[0]
        _draw_text(frame, f"FPS: {fps:.1f}", (10, 25), _WHITE, 0.55)
        _draw_text(frame, f"Faces: {face_count}", (10, 50), _WHITE, 0.55)
        if distance_text:
            _draw_text(frame, distance_text, (10, 75), _WHITE, 0.55)
        if streak_text:
            _draw_text(frame, streak_text, (10, 100), _WHITE, 0.55)
        _draw_text(frame, status_text, (10, h_frame - 15), status_color, 0.8)

        cv2.imshow("FaceLock — Debug View", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cv2.destroyAllWindows()
