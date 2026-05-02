import cv2
import numpy as np
import socket
import json
import os
from ultralytics import YOLO

# ==========================================
# 1. CONFIGURATION
# ==========================================
UDP_STREAM_URL = "udp://0.0.0.0:5000?overrun_nonfatal=1&fifo_size=5000000"
BRIDGE_IP = "127.0.0.1"
BRIDGE_PORT = 8080

os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "fflags;nobuffer|flags;low_delay"

# ==========================================
# 2. INITIALIZATION
# ==========================================
face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')

try:
    from ultralytics import YOLOE
    yolo_model = YOLOE("yoloe-26n-seg.pt")
except ImportError:
    yolo_model = YOLO("yoloe-26n-seg.pt")

try:
    yolo_model.set_classes(["person"], yolo_model.get_text_pe(["person"]))
except AttributeError:
    pass

udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

# ==========================================
# 3. DETECTION HELPERS
# ==========================================
def detect_haar_faces(gray):
    """Detects faces using Haar Cascades and returns a list of target dicts."""
    faces, rejectLevels, levelWeights = face_cascade.detectMultiScale3(
        gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30), outputRejectLevels=True
    )
    targets = []
    if len(faces) > 0:
        for face, weight in zip(faces, levelWeights):
            fx, fy, fw, fh = face
            confidence = weight[0] if isinstance(weight, (list, np.ndarray)) else weight
            targets.append({
                'coords': (fx + fw / 2.0, fy + fh / 2.0),
                'area': fw * fh,
                'type': 'face',
                'bbox': (fx, fy, fw, fh),
                'confidence': confidence
            })
    return targets

def detect_yolo_persons(frame):
    """Detects and TRACKS persons using YOLO's built-in ByteTrack."""
    results = yolo_model.track(frame, persist=True, verbose=False, conf=0.55, tracker="bytetrack.yaml")
    targets = []
    if len(results[0].boxes) > 0 and results[0].boxes.id is not None:
        track_ids = results[0].boxes.id.int().cpu().tolist()
        boxes = results[0].boxes.xyxy.cpu().numpy()
        for box, track_id in zip(boxes, track_ids):
            px, py, px2, py2 = int(box[0]), int(box[1]), int(box[2]), int(box[3])
            pw, ph = px2 - px, py2 - py
            targets.append({
                'coords': (px + pw / 2.0, py + ph * 0.3),  # Aiming for upper chest/head
                'area': pw * ph,
                'type': 'person',
                'bbox': (px, py, pw, ph),
                'track_id': track_id
            })
    return targets, results

def select_best_target(targets, cx, cy, last_coords, current_locked_id):
    """Ranks targets by area and center bias, prioritizing faces."""
    if not targets:
        return None
        
    max_dist = np.hypot(cx, cy)
    for t in targets:
        tx, ty = t['coords']
        
        # 1. Base Center Boost
        dist = np.hypot(tx - cx, ty - cy)
        center_boost = 1.0 + 0.3 * (1.0 - (dist / max_dist))
        
        # 2. Hysteresis "Lock-On" Boost
        stickiness_boost = 1.0
        if current_locked_id is not None and t['type'] == 'person' and t.get('track_id') == current_locked_id:
            stickiness_boost = 3.0  # Massive 300% boost to never let go of this ID
        elif last_coords is not None:
            # Fallback to coordinate-based distance for faces or untracked persons
            dist_from_last = np.hypot(tx - last_coords[0], ty - last_coords[1])
            if dist_from_last < 100:
                stickiness_boost = 1.5
                
        t['score'] = t['area'] * center_boost * stickiness_boost

    targets.sort(key=lambda x: x['score'], reverse=True)
    
    faces = [t for t in targets if t['type'] == 'face']
    persons = [t for t in targets if t['type'] == 'person']
    
    if faces and persons:
        # Scale face score to fairly compare with larger person bounding boxes
        if faces[0]['score'] * 15 > persons[0]['score']:
            return faces[0]
        return persons[0]
    
    return faces[0] if faces else persons[0]

def draw_winner(frame, winner):
    """Draws bounding boxes and labels for the selected target."""
    color = (255, 0, 0) if winner['type'] == 'face' else (0, 255, 0)
    label = f"FACE TARGET (Conf: {winner['confidence']:.2f})" if winner['type'] == 'face' else "PERSON TARGET"
    
    x, y, w, h = winner['bbox']
    cv2.rectangle(frame, (x, y), (x+w, y+h), color, 2)
    cv2.putText(frame, label, (x, max(20, y - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
    
    print(f"[{winner['type'].upper()}] Tracking at ({x}, {y})")
    return frame

def draw_crosshair(frame, cx, cy):
    """Draws a magenta crosshair representing the current smoothed aim."""
    cv2.circle(frame, (cx, cy), 15, (255, 0, 255), 2)
    cv2.line(frame, (cx - 25, cy), (cx + 25, cy), (255, 0, 255), 2)
    cv2.line(frame, (cx, cy - 25), (cx, cy + 25), (255, 0, 255), 2)

# ==========================================
# 4. MAIN LOOP
# ==========================================
def main():
    print(f"Connecting to video stream at {UDP_STREAM_URL}...")
    cap = cv2.VideoCapture(UDP_STREAM_URL, cv2.CAP_FFMPEG)
    
    if not cap.isOpened():
        print("Error: Could not open video stream.")
        return
        
    window_name = "Target Tracking"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    
    target_aim = None
    current_aim = None
    last_winner_coords = None
    current_locked_id = None
    print("Tracking System Online. Hunting for targets...")
    
    while True:
        for _ in range(5): cap.grab()  # Flush buffer
            
        ret, frame = cap.read()
        if not ret:
            continue
            
        height, width = frame.shape[:2]
        cx, cy = width // 2, height // 2
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        
        # 1. Gather all potential targets
        potential_targets = detect_haar_faces(gray)
        yolo_targets, yolo_results = detect_yolo_persons(frame)
        potential_targets.extend(yolo_targets)

        # 2. Select the optimal target
        winner = select_best_target(potential_targets, cx, cy, last_winner_coords, current_locked_id)
        
        # 3. Handle aiming logic
        if winner:
            last_winner_coords = winner['coords']
            if winner['type'] == 'person':
                current_locked_id = winner['track_id']
            # If it's a face, we deliberately DO NOT clear current_locked_id. 
            # This keeps the person's body "warm" in memory just in case the face disappears.
                
            target_aim = [winner['coords'][0], winner['coords'][1]]
            frame = draw_winner(frame, winner)
        elif target_aim is None or current_aim is None:
            last_winner_coords = None
            current_locked_id = None
            target_aim = [float(cx), float(cy)]
            current_aim = [float(cx), float(cy)]
        else:
            last_winner_coords = None
            current_locked_id = None
            # Drift back to center if no target is found
            target_aim = [float(cx), float(cy)]
            
        if current_aim is None:
            current_aim = target_aim.copy()

        # 4. P-Controller Smoothing Math
        current_aim[0] += (target_aim[0] - current_aim[0]) * 0.4
        current_aim[1] += (target_aim[1] - current_aim[1]) * 0.4

        # 5. Send telemetry to the Go Bridge
        payload = {
            "target_x": float(current_aim[0]),
            "target_y": float(current_aim[1]),
            "cx": float(cx),
            "cy": float(cy),
            "confidence": 1.0 if winner else 0.0
        }
        try:
            udp_socket.sendto(json.dumps(payload).encode('utf-8'), (BRIDGE_IP, BRIDGE_PORT))
        except Exception:
            pass
        
        # 6. Render UI
        draw_crosshair(frame, int(current_aim[0]), int(current_aim[1]))
        cv2.imshow(window_name, frame)
        
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
            
    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
