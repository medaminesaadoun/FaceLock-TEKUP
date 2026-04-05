import cv2
import os
from modules.camera_handler import CameraHandler
from modules.face_detector import FaceDetector

def run_application():
    # 1. Define paths and settings
    # Ensure this matches the folder where you put the .tflite file
    MODEL_PATH = os.path.join("data", "face_detector.tflite")
    CONFIDENCE_THRESHOLD = 0.6

    print("Initializing FaceLock Modules...")
    
    try:
        # 2. Initialize the modules
        camera = CameraHandler(camera_index=0)
        
        # Pass the arguments HERE
        detector = FaceDetector(
            model_path=MODEL_PATH, 
            min_confidence=CONFIDENCE_THRESHOLD
        )
        
    except Exception as e:
        print(f"Initialization Error: {e}")
        return

    print("System Active. Press 'q' to quit.")

    while True:
        success, frame = camera.get_frame()
        if not success:
            break

        # 3. Use the detector
        face_boxes, detection_result = detector.find_faces(frame)

        # 4. Draw boxes for visual feedback
        for (x, y, w, h) in face_boxes:
            cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
            cv2.putText(frame, "User Detected", (x, y - 10), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

        cv2.imshow('FaceLock - Live Detection', frame)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    camera.release()

if __name__ == "__main__":
    run_application()