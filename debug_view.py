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


def _load_stored_embeddings() -> list[tuple[int, np.ndarray, str]]:
    """Return all stored faces as (id, embedding, name) tuples."""
    try:
        user = get_user(config.DB_PATH, getpass.getuser())
        if not user:
            return []
        rows = get_embeddings(config.DB_PATH, user["id"])
        if not rows:
            return []
        key = load_key(config.KEY_PATH)
        result = []
        for emb_id, blob, name in rows:
            try:
                result.append((emb_id, bytes_to_embedding(decrypt(key, blob)), name))
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
    embeddings = _load_stored_embeddings()
    emb_arrays = [emb for _, emb, _ in embeddings]
    names = [name for _, _, name in embeddings]
    tolerance = get_tolerance(config.SETTINGS_PATH)
    auths = [Authenticator(emb, tolerance) for emb in emb_arrays]

    print("FaceLock Debug View — press Q to quit")
    print(f"Loaded {len(names)} face(s): {', '.join(names) if names else 'none'}")
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

            status_text = "NOT ENROLLED" if not emb_arrays else "NO FACE"
            status_color = _WHITE
            distance_text = ""
            streak_text = ""

            for (x, y, w, h) in boxes:
                cv2.rectangle(frame, (x, y), (x + w, y + h), _YELLOW, 2)

            emb_bytes = result.get("embedding")
            if face_count == 1 and emb_arrays and emb_bytes:
                emb = bytes_to_embedding(emb_bytes)
                dists = [float(np.linalg.norm(e - emb)) for e in emb_arrays]
                best_idx = int(np.argmin(dists))
                best_dist = dists[best_idx]
                best_name = names[best_idx]
                match = best_dist <= tolerance
                x, y, w, h = boxes[0]
                cv2.rectangle(frame, (x, y), (x + w, y + h),
                              _GREEN if match else _RED, 2)
                results = [a.feed(emb) for a in auths]
                granted = any(results)
                if granted:
                    winner = names[results.index(True)]
                    status_text = f"AUTHENTICATED as {winner}"
                    status_color = _GREEN
                elif match:
                    status_text = f"MATCHED: {best_name}"
                    status_color = _YELLOW
                else:
                    status_text = "NO MATCH"
                    status_color = _RED
                distance_text = f"Distance: {best_dist:.3f}  →  {best_name}  (threshold: {tolerance})"
                streak_text = f"Streak: {max(a.streak for a in auths)} / {config.CONSECUTIVE_FRAMES_REQUIRED}"
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

            cv2.imshow("FaceLock — Debug View", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

    except Exception as exc:
        print(f"Cannot reach core service: {exc}")

    cv2.destroyAllWindows()
