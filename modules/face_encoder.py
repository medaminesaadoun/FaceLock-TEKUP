# modules/face_encoder.py
import numpy as np
import cv2
import face_recognition


def extract_embedding(
    frame_bgr: np.ndarray,
    face_box: tuple[int, int, int, int],
) -> np.ndarray | None:
    """Extract 128-d embedding for a face region. Returns None if dlib fails."""
    x, y, w, h = face_box
    # face_recognition expects (top, right, bottom, left)
    location = (y, x + w, y + h, x)
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    encodings = face_recognition.face_encodings(rgb, known_face_locations=[location])
    return encodings[0] if encodings else None


def average_embeddings(embeddings: list[np.ndarray]) -> np.ndarray:
    return np.mean(embeddings, axis=0)


def embedding_to_bytes(embedding: np.ndarray) -> bytes:
    return embedding.astype(np.float64).tobytes()


def bytes_to_embedding(data: bytes) -> np.ndarray:
    return np.frombuffer(data, dtype=np.float64)


def compare_embedding(stored: np.ndarray, live: np.ndarray, tolerance: float) -> bool:
    return float(np.linalg.norm(stored - live)) <= tolerance
