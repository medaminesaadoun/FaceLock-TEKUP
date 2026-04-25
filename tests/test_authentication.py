# tests/test_authentication.py
# TC2, TC3, TC4, TC7 — Authentication tests (TC2/TC3/TC7 require webcam)
import cv2
import numpy as np
import pytest
import config
from modules.face_detector import FaceDetector
from modules.face_encoder import (
    extract_embedding, average_embeddings,
    embedding_to_bytes, bytes_to_embedding,
)
from modules.authenticator import Authenticator


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def detector():
    return FaceDetector(config.TFLITE_MODEL_PATH)


def _capture_frames(n: int) -> list:
    cap = cv2.VideoCapture(0)
    assert cap.isOpened(), "Webcam not available"
    for _ in range(5):
        cap.read()
    frames = []
    while len(frames) < n:
        ret, frame = cap.read()
        if ret:
            frames.append(frame)
    cap.release()
    return frames


@pytest.fixture(scope="module")
def enrolled_embedding(detector):
    """Capture ENROLLMENT_FRAMES frames and return averaged embedding."""
    embeddings = []
    cap = cv2.VideoCapture(0)
    assert cap.isOpened(), "Webcam not available"
    for _ in range(5):
        cap.read()
    while len(embeddings) < config.ENROLLMENT_FRAMES:
        ret, frame = cap.read()
        if not ret:
            continue
        if not detector.has_exactly_one_face(frame):
            continue
        boxes = detector.find_faces(frame)
        emb = extract_embedding(frame, boxes[0])
        if emb is not None:
            embeddings.append(emb)
    cap.release()
    return average_embeddings(embeddings)


# ---------------------------------------------------------------------------
# TC2 — Successful authentication
# ---------------------------------------------------------------------------

@pytest.mark.camera
def test_tc2_auth_passes_on_consecutive_matches(detector, enrolled_embedding):
    """TC2 — Authenticator grants access after CONSECUTIVE_FRAMES_REQUIRED matches."""
    auth = Authenticator(enrolled_embedding)
    granted = False
    frames = _capture_frames(config.CONSECUTIVE_FRAMES_REQUIRED + 5)
    for frame in frames:
        if not detector.has_exactly_one_face(frame):
            auth.reset()
            continue
        boxes = detector.find_faces(frame)
        emb = extract_embedding(frame, boxes[0])
        if emb is None:
            continue
        if auth.feed(emb):
            granted = True
            break
    assert granted, "Auth did not pass — ensure your face is clearly visible"


# ---------------------------------------------------------------------------
# TC3 — Streak resets when face disappears
# ---------------------------------------------------------------------------

@pytest.mark.camera
def test_tc3_streak_resets_on_no_face(enrolled_embedding):
    """TC3 — Streak counter resets to 0 when find_faces returns no boxes."""
    detector = FaceDetector(config.TFLITE_MODEL_PATH)
    auth = Authenticator(enrolled_embedding)

    frames = _capture_frames(config.CONSECUTIVE_FRAMES_REQUIRED - 1)
    for frame in frames:
        boxes = detector.find_faces(frame)
        if len(boxes) == 1:
            emb = extract_embedding(frame, boxes[0])
            if emb is not None:
                auth.feed(emb)

    streak_before = auth.streak
    assert streak_before > 0, "Could not build a partial streak — check camera"

    blank = np.zeros((480, 640, 3), dtype=np.uint8)
    assert detector.find_faces(blank) == [], "Blank frame should yield no detections"
    auth.reset()
    assert auth.streak == 0


# ---------------------------------------------------------------------------
# TC4 — Wrong face does not authenticate (no webcam needed)
# ---------------------------------------------------------------------------

def test_tc4_wrong_embedding_does_not_auth():
    """TC4 — Random embedding does not match enrolled embedding."""
    enrolled = np.random.rand(128).astype(np.float64)
    impostor = np.random.rand(128).astype(np.float64)
    while np.linalg.norm(enrolled - impostor) <= config.DEFAULT_TOLERANCE:
        impostor = np.random.rand(128).astype(np.float64)
    auth = Authenticator(enrolled)
    result = False
    for _ in range(config.CONSECUTIVE_FRAMES_REQUIRED * 2):
        result = auth.feed(impostor)
    assert not result, "Impostor embedding incorrectly granted access"


# ---------------------------------------------------------------------------
# TC7 — Enrolled embedding persists through bytes serialization
# ---------------------------------------------------------------------------

@pytest.mark.camera
def test_tc7_auth_works_after_serialization_roundtrip(enrolled_embedding, detector):
    """TC7 — Auth succeeds when stored embedding is serialized then restored."""
    restored = bytes_to_embedding(embedding_to_bytes(enrolled_embedding))
    auth = Authenticator(restored)
    granted = False
    frames = _capture_frames(config.CONSECUTIVE_FRAMES_REQUIRED + 5)
    for frame in frames:
        if not detector.has_exactly_one_face(frame):
            auth.reset()
            continue
        boxes = detector.find_faces(frame)
        emb = extract_embedding(frame, boxes[0])
        if emb is None:
            continue
        if auth.feed(emb):
            granted = True
            break
    assert granted, "Auth failed after serialization roundtrip"
