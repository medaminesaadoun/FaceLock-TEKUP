# debug_view.py
import time
import getpass
import cv2
import numpy as np

import config
from modules.face_detector import FaceDetector
from modules.face_encoder import extract_embedding, bytes_to_embedding, compare_embedding
from modules.authenticator import Authenticator
from modules.database import get_user, get_embedding, initialize
from modules.encryption import load_key, decrypt


_GREEN  = (0, 220, 0)
_RED    = (0, 0, 220)
_YELLOW = (0, 200, 220)
_WHITE  = (255, 255, 255)
_BLACK  = (0, 0, 0)


def _load_stored_embedding() -> np.ndarray | None:
    try:
        username = getpass.getuser()
        user = get_user(config.DB_PATH, username)
        if not user:
            return None
        blob = get_embedding(config.DB_PATH, user["id"])
        if not blob:
            return None
        key = load_key(config.KEY_PATH)
        return bytes_to_embedding(decrypt(key, blob))
    except Exception:
        return None


def _draw_text(frame, text: str, pos: tuple, color=_WHITE, scale: float = 0.6) -> None:
    x, y = pos
    cv2.putText(frame, text, (x + 1, y + 1), cv2.FONT_HERSHEY_SIMPLEX,
                scale, _BLACK, 2, cv2.LINE_AA)
    cv2.putText(frame, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX,
                scale, color, 1, cv2.LINE_AA)


def run() -> None:
    initialize(config.DB_PATH)
    detector = FaceDetector(config.TFLITE_MODEL_PATH)
    stored = _load_stored_embedding()
    auth = Authenticator(stored) if stored is not None else None

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Cannot open camera")
        return

    print("FaceLock Debug View — press Q to quit")
    prev_time = time.monotonic()

    while True:
        ret, frame = cap.read()
        if not ret:
            continue

        boxes = detector.find_faces(frame)
        face_count = len(boxes)

        # FPS
        now = time.monotonic()
        fps = 1.0 / max(now - prev_time, 1e-6)
        prev_time = now

        # Draw bounding boxes
        for (x, y, w, h) in boxes:
            color = _GREEN if face_count == 1 else _YELLOW
            cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)

        # Per-face info
        status_text = "NOT ENROLLED"
        distance_text = ""
        streak_text = ""
        status_color = _WHITE

        if face_count == 1 and stored is not None:
            emb = extract_embedding(frame, boxes[0])
            if emb is not None:
                dist = float(np.linalg.norm(stored - emb))
                match = dist <= config.DEFAULT_TOLERANCE
                color = _GREEN if match else _RED
                cv2.rectangle(frame, (boxes[0][0], boxes[0][1]),
                              (boxes[0][0] + boxes[0][2], boxes[0][1] + boxes[0][3]),
                              color, 2)
                if auth.feed(emb):
                    status_text = "AUTHENTICATED"
                    status_color = _GREEN
                else:
                    status_text = "MATCH" if match else "NO MATCH"
                    status_color = _GREEN if match else _RED
                distance_text = f"Distance: {dist:.3f}  (threshold: {config.DEFAULT_TOLERANCE})"
                streak_text = f"Streak: {auth.streak} / {config.CONSECUTIVE_FRAMES_REQUIRED}"
        elif face_count == 0:
            if auth:
                auth.reset()
            status_text = "NO FACE"
            status_color = _YELLOW
        elif face_count > 1:
            if auth:
                auth.reset()
            status_text = f"MULTIPLE FACES ({face_count})"
            status_color = _YELLOW

        # HUD overlay
        h_frame = frame.shape[0]
        _draw_text(frame, f"FPS: {fps:.1f}", (10, 25), _WHITE, 0.55)
        _draw_text(frame, f"Faces detected: {face_count}", (10, 50), _WHITE, 0.55)
        if distance_text:
            _draw_text(frame, distance_text, (10, 75), _WHITE, 0.55)
        if streak_text:
            _draw_text(frame, streak_text, (10, 100), _WHITE, 0.55)
        _draw_text(frame, status_text, (10, h_frame - 15), status_color, 0.8)

        cv2.imshow("FaceLock — Debug View", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()
