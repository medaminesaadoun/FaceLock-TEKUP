import cv2
from modules.camera_handler import CameraHandler

def run_application():
    print("Starting FaceLock...")
    
    # Initialize the camera module
    try:
        camera = CameraHandler(camera_index=0)
    except Exception as e:
        print(f"Initialization Error: {e}")
        return

    print("Camera Module Active. Press 'q' to stop.")

    try:
        while True:
            success, frame = camera.get_frame()
            
            if not success:
                print("Failed to grab frame.")
                break

            # Placeholder for future modules (Detection/Recognition)
            # Display the resulting frame
            cv2.imshow('FaceLock - Acquisition Module', frame)

            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
    finally:
        # Ensure resources are released even if an error occurs
        camera.release()
        print("System shutdown cleanly.")

if __name__ == "__main__":
    run_application()