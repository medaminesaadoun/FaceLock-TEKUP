import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

class FaceDetector:
    def __init__(self, model_path='data/face_detector.tflite', min_confidence=0.5):
        """
        Initializes the MediaPipe Tasks Face Detector.
        :param model_path: Path to the .tflite model file.
        :param min_confidence: Minimum confidence score (0.0 to 1.0).
        """
        # Create the BaseOptions with the model path
        base_options = python.BaseOptions(model_asset_path=model_path)
        
        # Create FaceDetectorOptions and set the confidence threshold here
        options = vision.FaceDetectorOptions(
            base_options=base_options,
            min_detection_confidence=min_confidence
        )
        
        # Initialize the detector
        self.detector = vision.FaceDetector.create_from_options(options)

    def find_faces(self, frame):
        """
        Detects faces and returns bounding boxes in (x, y, w, h) format.
        """
        # Convert BGR (OpenCV) to RGB (MediaPipe)
        image_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        
        # Create a MediaPipe Image object
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=image_rgb)
        
        # Run detection
        detection_result = self.detector.detect(mp_image)
        
        face_boxes = []
        
        if detection_result.detections:
            for detection in detection_result.detections:
                bbox = detection.bounding_box
                # The Tasks API returns absolute pixel values: origin_x, origin_y, width, height
                face_boxes.append((bbox.origin_x, bbox.origin_y, bbox.width, bbox.height))
                
        return face_boxes, detection_result