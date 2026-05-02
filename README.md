# Robotic Arm: Advanced Target Tracking & Stabilization System

An advanced robotic arm system featuring real-time visual tracking (faces and persons) combined with active hardware stabilization (IMU). The system is designed with a decoupled architecture, separating the heavy visual processing, state orchestration, and hardware control into specialized components.

## Architecture Overview

The system is logically divided into two main sections: the **Brain** (running on a host PC/Pi) and the **Cerebellum** (hardware layer on the Raspberry Pi).

### 1. Brain: Bridge (Go)
The central orchestrator written in Go (`brain/Bridge/src/main.go`). It acts as the middleware between the vision system and the physical robotic arm.
*   **PID Control:** Calculates pan and tilt adjustments to keep the target centered.
*   **Sensor Fusion:** Combines visual error data with physical IMU orientation data (pitch/roll) to stabilize the head movement.
*   **Communication:** Listens for visual coordinates via UDP (Port 8080) and sends servo commands/receives state via ZeroMQ (ZMQ).

### 2. Brain: Sight (Python)
The vision processing unit (`brain/Sight/raspberry_haar_tracking.py`).
*   **Visual Tracking:** Utilizes YOLO (ByteTrack) for persistent person tracking and Haar Cascades for face detection.
*   **Target Selection:** Intelligently ranks and selects targets based on area, center bias, and a hysteresis "lock-on" system to prevent jitter.
*   **Streaming:** Consumes a low-latency UDP video stream and transmits smoothed P-controller target coordinates to the Go Bridge via UDP.

### 3. Brain: Frontal Lobe (Python)
The LLM integration unit (`brain/brain.py`).
*   **Cognition:** Utilizes the Gemini API to generate contextual, conversational responses based on real-time visual events (e.g., locking onto a target).
*   **Streaming Output:** Chunks generated sentences on-the-fly to minimize audio generation latency.
*   **Communication:** Listens for visual events from the Go Bridge via ZMQ (Port 5563) and publishes text chunks to the Vocal Cords via ZMQ (Port 5562).

### 4. Cerebellum (Raspberry Pi / Python)
The hardware interfacing layer running on the robot itself.
*   **Movement Server (`cerebellum/Movement/robotic_arm_server.py`):** Uses the `Adafruit_ServoKit` library to control PCA9685 servos over I2C. Receives commands via ZMQ (Port 5555) and publishes state (Port 5556). Features a built-in calibration dance to initialize the IMU.
*   **Inner Ear (`cerebellum/Inner-ear/inner-ear.py`):** Interfaces with a BNO055 absolute orientation sensor via I2C to provide real-time physical pitch, roll, and heading. Publishes this telemetry via ZMQ (Port 5557) to inform the Bridge's stabilization logic.
*   **Vocal Cords (`cerebellum/Voice/voice.py`):** The Text-to-Speech (TTS) engine. Uses Piper TTS running continuously from a RAM disk (`/dev/shm`) for ultra-low latency. Receives text chunks from the Frontal Lobe via ZMQ (Port 5562) and queues them for gapless playback.

## Key Technologies

*   **Go:** High-performance orchestration and mathematical smoothing.
*   **Python:** Hardware interfacing and heavy ML/Vision processing.
*   **ZeroMQ (ZMQ):** Reliable, asynchronous inter-process communication for commands and hardware state telemetry.
*   **UDP:** Low-latency video streaming and visual coordinate transmission.
*   **OpenCV & YOLO:** Computer vision and object tracking.
*   **I2C / BNO055 / PCA9685:** Hardware protocols and components.
*   **Gemini API:** LLM integration for contextual conversations.
*   **Piper TTS:** Fast, local neural text-to-speech engine.

## Hardware Requirements

*   Raspberry Pi (or similar SBC) to run the `Cerebellum` components.
*   Adafruit 16-Channel PWM/Servo HAT (PCA9685).
*   Standard Servos for Pan/Tilt mechanisms.
*   BNO055 9-DOF Absolute Orientation Sensor (Inner Ear).
*   Webcam (streaming via UDP).
*   Speaker or Audio output for the Raspberry Pi (Vocal Cords).
*   Host PC / Server (optional, but recommended) for running the `Brain` components.

## Setup & Execution

### 0. Prerequisites (Piper TTS & API Keys)
1. **Piper TTS Setup (Raspberry Pi):**
   * Download the Piper TTS binary for your Raspberry Pi architecture from the [Piper GitHub Releases](https://github.com/rhasspy/piper/releases) and extract it into `cerebellum/Voice/piper/` (so that the executable is located at `cerebellum/Voice/piper/piper`).
   * Download the `en_US-lessac-low.onnx` model and its corresponding `.json` config file and place both files directly into the `cerebellum/Voice/` directory.
2. **Brain Setup (Host PC/Pi):**
   * Create a file named `api_key.txt` inside the `brain/` directory and paste your Gemini API key inside it.

### 1. Start the Cerebellum (On Raspberry Pi)
Start the hardware control, sensor, and voice servers:
```bash
python cerebellum/Movement/robotic_arm_server.py
python cerebellum/Inner-ear/inner-ear.py
python cerebellum/Voice/voice.py
```
*(Optionally, start the camera UDP stream script `start_stream.sh`)*

### 2. Start the Brain (On Host PC or Pi)
Start the Go Bridge, Vision Tracking, and Frontal Lobe:
```bash
# Start the Go Bridge
cd brain/Bridge/src
go run main.go --pi-ip <RASPBERRY_PI_IP>

# Start Vision Tracking
cd brain/Sight
python raspberry_haar_tracking.py

# Start Frontal Lobe (Ensure brain/api_key.txt contains your Gemini API key)
cd brain
python brain.py
```

### Manual Commands
The Go Bridge supports manual input overrides via `stdin`:
*   `rest`: Moves the robotic arm to its default resting position.
*   `calibrate`: Triggers a predefined servo movement pattern to calibrate the BNO055 sensor.
