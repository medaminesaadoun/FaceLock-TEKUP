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
from modules.face_encoder import bytes_to_embedding
from modules.authenticator import Authenticator
from modules.database import get_user, get_embeddings, initialize
from modules.encryption import load_key, decrypt
from modules.user_settings import get_tolerance


_GREEN  = (0, 220, 0)
_RED    = (0, 0, 220)
_YELLOW = (0, 200, 220)
_WHITE  = (255, 255, 255)
_BLACK  = (0, 0, 0)


def _load_stored_embeddings() -> list[tuple[str, np.ndarray]]:
    """Load all enrolled face embeddings as (name, embedding) pairs."""
    try:
        user = get_user(config.DB_PATH, getpass.getuser())
        if not user:
            return []
        rows = get_embeddings(config.DB_PATH, user["id"])
        if not rows:
            return []
        key = load_key(config.KEY_PATH)
        result = []
        for _, blob, name in rows:
            try:
                result.append((name or "Unnamed", bytes_to_embedding(decrypt(key, blob))))
            except Exception:
                continue
        return result
    except Exception:
        return []


def _stream_frames():
    """Open one persistent pipe connection and yield frames until disconnected."""
    conn = make_client()
    send(conn, {"cmd": "debug_stream"})
    try:
        while True:
            yield recv(conn)
    except Exception:
        pass
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
    # Load all enrolled embeddings  -  one Authenticator per face.
    stored_list = _load_stored_embeddings()   # [(name, embedding), ...]
    tolerance   = get_tolerance(config.SETTINGS_PATH)
    auths = [Authenticator(emb, tolerance) for _, emb in stored_list]

    print("FaceLock Debug View  -  press Q to quit")
    print("Connecting to core service...")

    prev_time = time.monotonic()

    try:
        for result in _stream_frames():
            if not result.get("ok"):
                continue

            frame = cv2.imdecode(np.frombuffer(result["jpeg"], np.uint8), cv2.IMREAD_COLOR)
            boxes = result["boxes"]
            face_count = len(boxes)

            now = time.monotonic()
            fps = 1.0 / max(now - prev_time, 1e-6)
            prev_time = now

            status_text = "NOT ENROLLED" if not stored_list else "NO FACE"
            status_color = _WHITE
            distance_text = ""
            streak_text = ""

            for (x, y, w, h) in boxes:
                cv2.rectangle(frame, (x, y), (x + w, y + h), _YELLOW, 2)

            emb_bytes = result.get("embedding")
            if face_count == 1 and stored_list and emb_bytes:
                emb = bytes_to_embedding(emb_bytes)
                # Compare against all enrolled faces; find best (minimum) distance.
                dists = [float(np.linalg.norm(s - emb)) for _, s in stored_list]
                best_idx  = int(np.argmin(dists))
                best_dist = dists[best_idx]
                best_name = stored_list[best_idx][0]
                match = best_dist <= tolerance
                x, y, w, h = boxes[0]
                cv2.rectangle(frame, (x, y), (x + w, y + h),
                              _GREEN if match else _RED, 2)
                results = [a.feed(emb) for a in auths]
                granted  = any(results)
                best_streak = max(a.streak for a in auths)
                if granted:
                    status_text  = f"AUTHENTICATED  -  {best_name}"
                    status_color = _GREEN
                elif match:
                    status_text  = f"MATCH  -  {best_name}"
                    status_color = _GREEN
                else:
                    status_text  = "NO MATCH"
                    status_color = _RED
                face_label    = f"({len(stored_list)} face{'s' if len(stored_list) != 1 else ''} enrolled)"
                distance_text = f"Best dist: {best_dist:.3f}  threshold: {tolerance}  {face_label}"
                streak_text   = f"Streak: {best_streak} / {config.CONSECUTIVE_FRAMES_REQUIRED}"
            elif face_count == 0 and auths:
                for a in auths:
                    a.reset()
            elif face_count > 1:
                for a in auths:
                    a.reset()
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

            cv2.imshow("FaceLock  -  Debug View", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

    except Exception as exc:
        print(f"Cannot reach core service: {exc}")

    cv2.destroyAllWindows()
