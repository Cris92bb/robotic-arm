import cv2
import numpy as np
import socket
import json
import os
import time
from ultralytics import YOLO

# ==========================================
# 1. CONNECTIVITY CONFIG
# ==========================================
BRIDGE_IP = "127.0.0.1"
BRIDGE_PORT = 8080

udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

# UDP_STREAM_URL = "udp://@0.0.0.0:5000"
# The 0.0.0.0:5000 tells it to listen on all interfaces at port 5000
# overrun_nonfatal=1 prevents the app from crashing if your laptop lags slightly
# fifo_size=5000000 ensures the network buffer doesn't overflow
UDP_STREAM_URL = "udp://0.0.0.0:5000?overrun_nonfatal=1&fifo_size=5000000"
# Zero-latency environment flag for OpenCV
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "fflags;nobuffer|flags;low_delay"

# ==========================================
# 2. TRACKING PARAMETERS
# ==========================================
YOLO_CONF = 0.4 # Confidence Threshold (0.0 to 1.0) - Tweak this for IR cameras

face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')

# ==========================================
# 3. SPIRAL & MASK LOGIC
# ==========================================
def generate_logarithmic_spiral(a=5, b=0.15, max_theta=6*np.pi, num_points=1000):
    """
    Generates the (x, y) coordinates of a logarithmic spiral.
    Uses the polar equation: r = a * e^(b*theta)
    """
    theta = np.linspace(0, max_theta, num_points)
    r = a * np.exp(b * theta)
    x = r * np.cos(theta)
    y = r * np.sin(theta)
    return np.column_stack((x, y))

def rotate_points(x, y, cx, cy, roll_rad):
    """
    Applies a 2D rotation matrix to (x, y) coordinates 
    using (cx, cy) as the pivot point.
    """
    cos_val = np.cos(roll_rad)
    sin_val = np.sin(roll_rad)
    x_new = (x - cx) * cos_val - (y - cy) * sin_val + cx
    y_new = (x - cx) * sin_val + (y - cy) * cos_val + cy
    return x_new, y_new

def generate_masks(R, Theta, roll_rad, width, height, a=5, b=0.15):
    Theta_adj = Theta - roll_rad
    Theta_norm = np.mod(Theta_adj, 2 * np.pi)
    slice_idx = np.floor(Theta_norm / (np.pi / 3)).astype(np.int32)
    
    r1 = a * np.exp(b * (Theta_norm + 2 * np.pi))
    r2 = a * np.exp(b * (Theta_norm + 4 * np.pi))
    band_idx = np.zeros_like(R, dtype=np.int32)
    band_idx[R >= r1] = 1
    band_idx[R >= r2] = 2
    
    MIN_CROP = 300
    
    masks_data = []
    for s in range(6):
        for b_idx in range(3):
            m = np.logical_and(slice_idx == s, band_idx == b_idx).astype(np.uint8) * 255
            x, y, w, h = cv2.boundingRect(m)
            # Only store valid masks that have size
            if w > 0 and h > 0:
                cx_box = x + (w // 2)
                cy_box = y + (h // 2)

                new_w = max(w, MIN_CROP)
                new_h = max(h, MIN_CROP)

                new_x = max(0, cx_box - (new_w // 2))
                new_y = max(0, cy_box - (new_h // 2))

                new_w = min(new_w, width - new_x)
                new_h = min(new_h, height - new_y)

                masks_data.append({
                    "crop_mask": m[y:y+h, x:x+w],
                    "bbox": (x, y, w, h),
                    "yolo_bbox": (new_x, new_y, new_w, new_h)
                })
    return masks_data

# ==========================================
# 3. THE BRAIN LOOP
# ==========================================
def main():
    # 1. Window & UI Setup
    print(f"Waiting for Optic Nerve on {UDP_STREAM_URL}...")
    cap = cv2.VideoCapture(UDP_STREAM_URL, cv2.CAP_FFMPEG)
    
    
    if not cap.isOpened():
        print("ERROR: Could not catch the video stream.")
        return

    window_name = "Foveated Vision - Raspberry Pi"
    cv2.namedWindow(window_name)
    
    def on_trackbar(val):
        pass
    
    cv2.createTrackbar("Roll Angle", window_name, 0, 180, on_trackbar)
    try:
        cv2.setTrackbarMin("Roll Angle", window_name, -180)
        cv2.setTrackbarMax("Roll Angle", window_name, 180)
        cv2.setTrackbarPos("Roll Angle", window_name, 0)
        use_fallback_offset = False
    except AttributeError:
        cv2.createTrackbar("Roll Angle (Offset +180)", window_name, 180, 360, on_trackbar)
        use_fallback_offset = True
        
    # Create Binary Toggles for UI Visualization
    cv2.createTrackbar("Show Grid", window_name, 1, 1, on_trackbar)
    cv2.createTrackbar("Show Motion", window_name, 1, 1, on_trackbar)
    cv2.createTrackbar("Show Recognition", window_name, 1, 1, on_trackbar)

    # Mathematical parameters for the logarithmic spiral
    spiral_a = 20
    spiral_b = 0.15

    base_spiral = generate_logarithmic_spiral(a=spiral_a, b=spiral_b)
    
    # 1. YOLO Initialization
    try:
        from ultralytics import YOLOE
        model = YOLOE("yoloe-26n-seg.pt")
    except ImportError:
        model = YOLO("yoloe-26n-seg.pt")
        
    # Define what the model should look for (Open Vocabulary)
    classes = ["person", "hand"]

    # Generate text embeddings and set them in the model (if supported)
    try:
        model.set_classes(classes, model.get_text_pe(classes))
    except AttributeError:
        pass
        
    # Init Temporal Tripwire state and grid
    previous_frame = None
    R_grid = None
    Theta_grid = None
    
    # Track previous roll to avoid redundant mask generation
    last_roll_deg = None
    masks = []
    
    # Init Tracking Variables
    target_aim = None
    current_aim = None
    pan_angle, tilt_angle = 90, 90
    
    print("Foveated Brain Online. Hunting for targets...")
    
    while True:
        target_detected_this_frame = False
        target_is_face = False
        
        # NEW: Flush the buffer to ensure we only see the absolute newest frame
        for _ in range(5):
            cap.grab() 
            
        ret, frame = cap.read()
        if not ret:
            continue
            
        height, width = frame.shape[:2]
        cx, cy = width // 2, height // 2
        
        # Precompute the standard X,Y static grid once size is known
        if R_grid is None or Theta_grid is None:
            Y, X = np.mgrid[0:height, 0:width]
            dx = X - cx
            dy = Y - cy
            R_grid = np.sqrt(dx**2 + dy**2)
            Theta_grid = np.arctan2(dy, dx)
        
        # Determine trackbar value
        if not use_fallback_offset:
            roll_deg = cv2.getTrackbarPos("Roll Angle", window_name)
        else:
            roll_deg = cv2.getTrackbarPos("Roll Angle (Offset +180)", window_name) - 180
            
        roll_rad = np.radians(roll_deg)
        
        # Determine visibility toggles
        show_grid = cv2.getTrackbarPos("Show Grid", window_name) == 1
        show_motion = cv2.getTrackbarPos("Show Motion", window_name) == 1
        show_recognition = cv2.getTrackbarPos("Show Recognition", window_name) == 1
        
        # 2. The Temporal Tripwire (Motion Detection)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (21, 21), 0)
        
        if previous_frame is None:
            previous_frame = blurred
            continue
            
        # Calculate frame difference
        frame_diff = cv2.absdiff(previous_frame, blurred)
        
        # Apply binary threshold
        _, thresh = cv2.threshold(frame_diff, 25, 255, cv2.THRESH_BINARY)
        
        # 1. Generate Masks (Only if roll angle changes)
        if roll_deg != last_roll_deg or not masks:
            masks = generate_masks(R_grid, Theta_grid, roll_rad, width, height, a=spiral_a, b=spiral_b)
            last_roll_deg = roll_deg
        
        # 3. Zonal Scoring & Visual Feedback
        max_score = 0
        best_mask_data = None
        
        # Loop through generated masks using ROIs
        for m_data in masks:
            x, y, w, h = m_data["bbox"]
            # Crop the threshold frame to just this box
            thresh_crop = thresh[y:y+h, x:x+w]
            
            # Apply bitwise_and ONLY to the tiny crop
            isolated = cv2.bitwise_and(thresh_crop, thresh_crop, mask=m_data["crop_mask"])
            score = cv2.countNonZero(isolated)
            
            if score > max_score:
                max_score = score
                best_mask_data = m_data
                
        # Update previous frame
        previous_frame = blurred

        # The Trigger: If the highest motion score exceeds baseline
        if max_score > 500 and best_mask_data is not None:
            x, y, w, h = best_mask_data["bbox"]
            
            # Overlay red tint ONLY on the cropped region (if enabled)
            if show_motion:
                roi_color = frame[y:y+h, x:x+w]
                overlay_roi = roi_color.copy()
                overlay_roi[best_mask_data["crop_mask"] == 255] = (0, 0, 255) # Red tint
                cv2.addWeighted(overlay_roi, 0.5, roi_color, 0.5, 0, roi_color)
            
            # The Foveated Crop (Padded Context for YOLO)
            yx, yy, yw, yh = best_mask_data["yolo_bbox"]
            roi_crop = frame[yy:yy+yh, yx:yx+yw].copy()
            
            if roi_crop.size > 0:
                # NEW: Normalize the image for Infrared Cameras
                # Convert to LAB color space to equalize only the Lightness channel (preserves structural color data better than BGR or GRAY equalize)
                lab = cv2.cvtColor(roi_crop, cv2.COLOR_BGR2LAB)
                l_channel, a, b = cv2.split(lab)
                
                # Apply Contrast Limited Adaptive Histogram Equalization (CLAHE)
                clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
                cl = clahe.apply(l_channel)
                
                # Merge and convert back to BGR for YOLO (YOLO expects 3-channel BGR)
                limg = cv2.merge((cl, a, b))
                roi_norm = cv2.cvtColor(limg, cv2.COLOR_LAB2BGR)
                
                # --- HAAR CASCADE FACE DETECTION ---
                gray_roi = cl # cl is already the CLAHE-equalized lightness channel
                faces, rejectLevels, levelWeights = face_cascade.detectMultiScale3(
                    gray_roi, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30), outputRejectLevels=True
                )
                
                valid_faces = []
                if len(faces) > 0:
                    for face, weight in zip(faces, levelWeights):
                        conf = weight[0] if isinstance(weight, (list, np.ndarray)) else weight
                        if conf > 5.5:
                            valid_faces.append((face, conf))
                
                if len(valid_faces) > 0:
                    target_detected_this_frame = True
                    target_is_face = True
                    
                    largest_face_tuple = max(valid_faces, key=lambda data: data[0][2] * data[0][3])
                    largest_face, confidence = largest_face_tuple
                    fx, fy, fw, fh = largest_face
                    
                    target_x = yx + fx + (fw / 2.0)
                    target_y = yy + fy + (fh / 2.0)
                    target_aim = [target_x, target_y]
                    
                    cv2.rectangle(frame, (yx + fx, yy + fy), (yx + fx + fw, yy + fy + fh), (255, 0, 0), 2)
                    cv2.putText(frame, f"FACE DETECTED ({confidence:.2f})", (x, max(20, y - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 0, 0), 2)
                else:
                    # Inference and The "Magic Paste"
                    # Increase conf (0.0 to 1.0) to reduce false positive detections from the IR noise
                    results = model(roi_crop, verbose=False, conf=YOLO_CONF)
                    
                    # Check if we have boxes
                    if len(results[0].boxes) > 0:
                        if show_recognition and hasattr(results[0], 'masks') and results[0].masks is not None:
                            annotated_crop = results[0].plot()
                            frame[yy:yy+yh, yx:yx+yw] = annotated_crop
                        
                        target_detected_this_frame = True
                        target_is_face = False
                        
                        # Extract target center for the robotic aim simulation
                        box = results[0].boxes[0].xyxy[0].cpu().numpy() # [x1, y1, x2, y2]
                        pw = box[2] - box[0]
                        ph = box[3] - box[1]
                        
                        local_cx = box[0] + pw / 2.0
                        local_cy = box[1] + ph * 0.3 # Aim for upper chest/head for person
                        
                        target_x = yx + local_cx
                        target_y = yy + local_cy
                        target_aim = [target_x, target_y]

                        cv2.putText(frame, "HUMAN DETECTED", (x, max(20, y - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            
        # --- The Proportional Controller & Robotic Crosshair ---
        if target_aim is None or current_aim is None:
            # Default to resting at the center of the frame
            target_aim = [float(cx), float(cy)]
            current_aim = [float(cx), float(cy)]
            
        if not target_detected_this_frame:
            # Drift back to center if no target is found
            target_aim = [float(cx), float(cy)]

        # P-Controller Smoothing Math
        # Increased to 0.4 for tighter tracking
        current_aim[0] += (target_aim[0] - current_aim[0]) * 0.4
        current_aim[1] += (target_aim[1] - current_aim[1]) * 0.4
        
        target_id_val = 0
        if target_detected_this_frame:
            target_id_val = -1 if target_is_face else 1

        # SEND TO BRIDGE
        payload = {
            "target_x": float(current_aim[0]),
            "target_y": float(current_aim[1]),
            "cx": float(cx),
            "cy": float(cy),
            "confidence": 1.0 if target_detected_this_frame else 0.0,
            "target_id": target_id_val
        }
        try:
            udp_socket.sendto(json.dumps(payload).encode('utf-8'), (BRIDGE_IP, BRIDGE_PORT))
        except Exception:
            pass
        
        # Draw the Robotic Crosshair (Magenta)
        c_x, c_y = int(current_aim[0]), int(current_aim[1])
        cv2.circle(frame, (c_x, c_y), 15, (255, 0, 255), 2)
        cv2.line(frame, (c_x - 25, c_y), (c_x + 25, c_y), (255, 0, 255), 2)
        cv2.line(frame, (c_x, c_y - 25), (c_x, c_y + 25), (255, 0, 255), 2)
            
        # --- Draw Geometric Overlay lines on top for visualization ---
        if show_grid:
            x_spiral = base_spiral[:, 0] + cx
            y_spiral = base_spiral[:, 1] + cy
            x_rot, y_rot = rotate_points(x_spiral, y_spiral, cx, cy, roll_rad)
            pts = np.column_stack((x_rot, y_rot)).astype(np.int32).reshape((-1, 1, 2))
            cv2.polylines(frame, [pts], isClosed=False, color=(0, 255, 0), thickness=2)
            
            line_length = max(width, height)
            for sp_angle in [0, 60, 120]:
                total_rad = roll_rad + np.radians(sp_angle)
                sx1 = cx + line_length * np.cos(total_rad)
                sy1 = cy + line_length * np.sin(total_rad)
                sx2 = cx - line_length * np.cos(total_rad)
                sy2 = cy - line_length * np.sin(total_rad)
                cv2.line(frame, (int(sx1), int(sy1)), (int(sx2), int(sy2)), (0, 0, 255), 1)

            hx1 = cx + line_length * np.cos(roll_rad)
            hy1 = cy + line_length * np.sin(roll_rad)
            hx2 = cx - line_length * np.cos(roll_rad)
            hy2 = cy - line_length * np.sin(roll_rad)
            cv2.line(frame, (int(hx1), int(hy1)), (int(hx2), int(hy2)), (255, 0, 0), 2)

        cv2.imshow(window_name, frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
            
    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
