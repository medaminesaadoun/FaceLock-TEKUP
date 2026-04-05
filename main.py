import cv2
import sys

def test_setup():
    print("Initializing FaceLock Environment...")
    
    # Check OpenCV and Camera
    cap = cv2.Session = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Error: Could not access the webcam.")
        return

    print("Webcam accessed successfully. Press 'q' to exit the test.")
    
    while True:
        ret, frame = cap.read()
        if not ret:
            break
            
        cv2.imshow('FaceLock Environment Test', frame)
        
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()
    print("Setup verified.")

if __name__ == "__main__":
    test_setup()