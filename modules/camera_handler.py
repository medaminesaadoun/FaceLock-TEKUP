import cv2

class CameraHandler:
    def __init__(self, camera_index=0):
        """
        Initializes the webcam.
        :param camera_index: The ID of the webcam (default is 0).
        """
        self.cap = cv2.VideoCapture(camera_index)
        if not self.cap.isOpened():
            raise Exception("Could not open webcam.")

    def get_frame(self):
        """
        Captures a single frame from the webcam.
        :return: (ret, frame) ret is a boolean indicating success, frame is the image array.
        """
        ret, frame = self.cap.read()
        return ret, frame

    def release(self):
        """
        Releases the webcam resource.
        """
        if self.cap.isOpened():
            self.cap.release()
            cv2.destroyAllWindows()

    def __del__(self):
        self.release()