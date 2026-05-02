import cv2
import numpy as np
import socket
import json
import os
from ultralytics import YOLO

# ==========================================
# 1. CONNECTIVITY CONFIG
# ==========================================
# UDP stream from Raspberry Pi camera
UDP_STREAM_URL = "udp://0.0.0.0:5000?overrun_nonfatal=1&fifo_size=5000000"

# Zero-latency environment flag for OpenCV
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "fflags;nobuffer|flags;low_delay"

# Setup UDP Bridge to Go Orchestrator
BRIDGE_IP = "127.0.0.1"
BRIDGE_PORT = 8080
udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

# ==========================================
# 2. HAAR CASCADE & HOG CONFIG
# ==========================================
# Load the pre-trained Haar Cascade for frontal face detection built into OpenCV
face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')

# Initialize YOLO for person detection (Fallback)
try:
    from ultralytics import YOLOE
    yolo_model = YOLOE("yoloe-26n-seg.pt")
except ImportError:
    yolo_model = YOLO("yoloe-26n-seg.pt")

# Restrict YOLO to finding persons
try:
    yolo_model.set_classes(["person"], yolo_model.get_text_pe(["person"]))
except AttributeError:
    pass

def main():
    print(f"Connecting to video stream at {UDP_STREAM_URL}...")
    cap = cv2.VideoCapture(UDP_STREAM_URL, cv2.CAP_FFMPEG)
    
    if not cap.isOpened():
        print("Error: Could not open video stream.")
        return
        
    window_name = "Haar Cascade Tracking"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    
    # Init Tracking Variables
    target_aim = None
    current_aim = None
    
    print("Haar Cascade Brain Online. Hunting for faces...")
    
    while True:
        # Flush the buffer to ensure we only see the absolute newest frame
        for _ in range(5):
            cap.grab() 
            
        ret, frame = cap.read()
        if not ret:
            continue
            
        height, width = frame.shape[:2]
        cx, cy = width // 2, height // 2
        
        # Convert frame to grayscale for Haar Cascade
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        
        # Detect faces and get confidences using detectMultiScale3
        # scaleFactor=1.1, minNeighbors=5 are standard reliable parameters
        faces, rejectLevels, levelWeights = face_cascade.detectMultiScale3(
            gray, 
            scaleFactor=1.1, 
            minNeighbors=5, 
            minSize=(30, 30),
            outputRejectLevels=True
        )
        
        face_detected_this_frame = False
        
        if len(faces) > 0:
            face_detected_this_frame = True
            
            # Find the largest face by area, pairing it with its confidence weight
            face_data = list(zip(faces, levelWeights))
            largest_face_tuple = max(face_data, key=lambda data: data[0][2] * data[0][3])
            
            largest_face = largest_face_tuple[0]
            # Handle format of levelWeights depending on OpenCV version
            confidence = largest_face_tuple[1][0] if isinstance(largest_face_tuple[1], (list, np.ndarray)) else largest_face_tuple[1]
            
            fx, fy, fw, fh = largest_face
            
            print(f"[HAAR] Face Detected at ({fx}, {fy}) | Confidence (Weight): {confidence:.2f}")
            
            # Draw bounding box around the detected face
            cv2.rectangle(frame, (fx, fy), (fx+fw, fy+fh), (255, 0, 0), 2)
            cv2.putText(frame, f"FACE (Conf: {confidence:.2f})", (fx, max(20, fy - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 0, 0), 2)
            
            # Calculate center of the face
            target_x = fx + (fw / 2.0)
            target_y = fy + (fh / 2.0)
            target_aim = [target_x, target_y]
        else:
            # Fallback to Person Detection if no face is found
            # Run YOLO on the full frame
            results = yolo_model(frame, verbose=False, conf=0.55)
            
            if len(results[0].boxes) > 0:
                face_detected_this_frame = True # Re-use flag so it doesn't drift back to center
                
                # We can just pick the first person detected
                box = results[0].boxes[0].xyxy[0].cpu().numpy() # [x1, y1, x2, y2]
                px, py, px2, py2 = int(box[0]), int(box[1]), int(box[2]), int(box[3])
                pw = px2 - px
                ph = py2 - py
                
                # Draw the YOLO results on the frame
                annotated_frame = results[0].plot()
                # Overwrite frame with annotated version
                frame = annotated_frame
                
                cv2.putText(frame, "PERSON DETECTED (YOLO)", (px, max(20, py - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                
                target_x = px + (pw / 2.0)
                # For a person, aim for the upper chest/head area rather than the stomach (center)
                target_y = py + (ph * 0.3)
                target_aim = [target_x, target_y]

        # --- The Proportional Controller & Robotic Crosshair ---
        if target_aim is None or current_aim is None:
            # Default to resting at the center of the frame
            target_aim = [float(cx), float(cy)]
            current_aim = [float(cx), float(cy)]
            
        if not face_detected_this_frame:
            # Drift back to center when no face is found
            target_aim = [float(cx), float(cy)]
            
        # P-Controller Smoothing Math
        # Increased from 0.1 to 0.4 for much tighter and faster tracking
        current_aim[0] += (target_aim[0] - current_aim[0]) * 0.4
        current_aim[1] += (target_aim[1] - current_aim[1]) * 0.4

        # SEND TO BRIDGE
        # Send the current smoothed crosshair coordinates to the bridge
        payload = {
            "target_x": float(current_aim[0]),
            "target_y": float(current_aim[1]),
            "cx": float(cx),
            "cy": float(cy),
            "confidence": 1.0 if face_detected_this_frame else 0.0
        }
        try:
            udp_socket.sendto(json.dumps(payload).encode('utf-8'), (BRIDGE_IP, BRIDGE_PORT))
        except Exception as e:
            pass
        
        # Draw the Robotic Crosshair (Magenta)
        c_x, c_y = int(current_aim[0]), int(current_aim[1])
        cv2.circle(frame, (c_x, c_y), 15, (255, 0, 255), 2)
        cv2.line(frame, (c_x - 25, c_y), (c_x + 25, c_y), (255, 0, 255), 2)
        cv2.line(frame, (c_x, c_y - 25), (c_x, c_y + 25), (255, 0, 255), 2)

        cv2.imshow(window_name, frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
            
    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
