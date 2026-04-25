# tests/test_detection.py
# TC5 — Live face detection tests (require webcam + person in frame)
import cv2
import pytest
import config
from modules.face_detector import FaceDetector

pytestmark = pytest.mark.camera  # all tests in this file need a webcam


@pytest.fixture(scope="module")
def detector():
    return FaceDetector(config.TFLITE_MODEL_PATH)


@pytest.fixture(scope="module")
def live_frame():
    cap = cv2.VideoCapture(0)
    assert cap.isOpened(), "Webcam not available"
    # Discard first few frames while camera warms up.
    for _ in range(5):
        cap.read()
    ret, frame = cap.read()
    cap.release()
    assert ret, "Could not read frame from webcam"
    return frame


def test_detector_loads(detector):
    """TC5a — FaceDetector initialises without error."""
    assert detector is not None


def test_detects_face_in_live_frame(detector, live_frame):
    """TC5b — At least one face detected when user is in front of camera."""
    boxes = detector.find_faces(live_frame)
    assert len(boxes) >= 1, "No face detected — make sure your face is visible"


def test_bounding_box_shape(detector, live_frame):
    """TC5c — Each bounding box is a 4-tuple of non-negative integers."""
    boxes = detector.find_faces(live_frame)
    for box in boxes:
        assert len(box) == 4
        assert all(isinstance(v, int) and v >= 0 for v in box)


def test_has_exactly_one_face(detector, live_frame):
    """TC5d — has_exactly_one_face returns True when one user is in frame."""
    assert detector.has_exactly_one_face(live_frame), \
        "Expected exactly one face — ensure only you are in frame"
