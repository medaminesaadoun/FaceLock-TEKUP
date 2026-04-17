import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision


class FaceDetector:
    def __init__(self, model_path: str, min_confidence: float = 0.6):
        base_options = mp_python.BaseOptions(model_asset_path=model_path)
        options = mp_vision.FaceDetectorOptions(
            base_options=base_options,
            min_detection_confidence=min_confidence,
        )
        self._detector = mp_vision.FaceDetector.create_from_options(options)

    def find_faces(self, frame_bgr: np.ndarray) -> list[tuple[int, int, int, int]]:
        """Returns list of (origin_x, origin_y, width, height) bounding boxes."""
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = self._detector.detect(mp_image)
        return [
            (d.bounding_box.origin_x, d.bounding_box.origin_y,
             d.bounding_box.width, d.bounding_box.height)
            for d in result.detections
        ]

    def has_exactly_one_face(self, frame_bgr: np.ndarray) -> bool:
        return len(self.find_faces(frame_bgr)) == 1