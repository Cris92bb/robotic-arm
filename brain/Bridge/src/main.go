package main

import (
	"bufio"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"math"
	"net"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/go-zeromq/zmq4"
)

type Payload struct {
	TargetX    float64 `json:"target_x"`
	TargetY    float64 `json:"target_y"`
	CX         float64 `json:"cx"`
	CY         float64 `json:"cy"`
	Confidence float64 `json:"confidence"`
}

type Orientation struct {
	H float64 `json:"h"`
	R float64 `json:"r"`
	P float64 `json:"p"`
}

type Calibration struct {
	S int `json:"s"`
	G int `json:"g"`
	A int `json:"a"`
	M int `json:"m"`
}

type SensorPayload struct {
	Orientation Orientation `json:"orientation"`
	Calibration Calibration `json:"calibration"`
	Timestamp   float64     `json:"timestamp"`
}

const (
	restHeading = 185.25
	restRoll    = -77.00
	restPitch   = 121.19
)

// Inside your main control loop, normalize the raw sensor data:
func getNormalizedOrientation(raw Orientation) Orientation {
	return Orientation{
		// We use modular arithmetic for Heading (0-360)
		H: math.Mod(raw.H-restHeading+360, 360),
		R: raw.R - restRoll,
		P: raw.P - restPitch,
	}
}

func clip(value, min, max float64) float64 {
	if value < min {
		return min
	}
	if value > max {
		return max
	}
	return value
}

func main() {
	// Customizable parameters
	piIP := flag.String("pi-ip", "192.168.1.158", "IP address of the Raspberry Pi")
	port := flag.Int("port", 8080, "UDP port to listen for coordinates")
	ki := flag.Float64("ki", 0.05, "Integral gain for servos")
	maxStep := flag.Float64("max-step", 30.0, "Maximum degree change per step")
	cooldownMs := flag.Int("cooldown", 50, "Cooldown between servo commands in milliseconds")
	deadzone := flag.Float64("deadzone", 35.0, "Pixel margin of error where camera stops moving")
	imuDeadzone := flag.Float64("imu-deadzone", 0.5, "Minimum degree change to trigger stabilization")
	flag.Parse()

	log.Printf("Starting Bridge. Listening on UDP port %d", *port)
	log.Printf("Raspberry Pi IP: %s", *piIP)
	log.Printf("Ki: %f, Max Step: %f, Cooldown: %dms", *ki, *maxStep, *cooldownMs)

	// Setup ZMQ REQ socket for commands
	req := zmq4.NewReq(context.Background())
	defer req.Close()

	zmqURL := fmt.Sprintf("tcp://%s:5555", *piIP)
	err := req.Dial(zmqURL)
	if err != nil {
		log.Fatalf("Failed to connect to ZMQ server at %s: %v", zmqURL, err)
	}

	// Setup ZMQ SUB socket for state syncing
	sub := zmq4.NewSub(context.Background())
	defer sub.Close()

	subURL := fmt.Sprintf("tcp://%s:5556", *piIP)
	err = sub.Dial(subURL)
	if err != nil {
		log.Fatalf("Failed to connect to ZMQ SUB server at %s: %v", subURL, err)
	}

	err = sub.SetOption(zmq4.OptionSubscribe, "servo_state")
	if err != nil {
		log.Fatalf("Failed to subscribe to servo_state: %v", err)
	}

	// --- NEW: Setup ZMQ SUB socket for Inner Ear ---
	earSub := zmq4.NewSub(context.Background())
	defer earSub.Close()

	earURL := fmt.Sprintf("tcp://%s:5557", *piIP)
	err = earSub.Dial(earURL)
	if err != nil {
		log.Printf("Warning: Failed to connect to Inner Ear SUB at %s: %v. Inner Ear will be ignored.", earURL, err)
	} else {
		err = earSub.SetOption(zmq4.OptionSubscribe, "orientation")
		if err != nil {
			log.Printf("Warning: Failed to subscribe to orientation: %v", err)
		}
	}

	var stateMutex sync.Mutex
	panAngle := 90.0
	tiltAngle := 90.0
	currentOrientation := Orientation{} // NEW: Store inner ear data
	stateInitialized := false
	innerEarActive := false // Track if Inner Ear is sending data

	// Goroutine to sync state from the Pi
	go func() {
		for {
			msg, err := sub.Recv()
			if err != nil {
				log.Printf("ZMQ SUB Recv error: %v", err)
				continue
			}

			if len(msg.Frames) > 0 {
				parts := strings.SplitN(string(msg.Frames[0]), " ", 2)
				if len(parts) == 2 && parts[0] == "servo_state" {
					var state map[string]float64
					err := json.Unmarshal([]byte(parts[1]), &state)
					if err == nil {
						stateMutex.Lock()
						if val, ok := state["4"]; ok {
							panAngle = val
						}
						if val, ok := state["0"]; ok {
							tiltAngle = val
						}
						if !stateInitialized {
							stateInitialized = true
							log.Printf("[SYNC] Initial state synced: pan=%.1f, tilt=%.1f", panAngle, tiltAngle)
						}
						stateMutex.Unlock()
					}
				}
			}
		}
	}()

	// --- NEW: Goroutine to sync Inner Ear state ---
	go func() {
		for {
			msg, err := earSub.Recv()
			if err != nil {
				continue
			}

			if len(msg.Frames) > 0 {
				// Parse "orientation {json...}"
				parts := strings.SplitN(string(msg.Frames[0]), " ", 2)
				if len(parts) == 2 && parts[0] == "orientation" {
					var payload SensorPayload
					err := json.Unmarshal([]byte(parts[1]), &payload)
					if err == nil {
						// Thread-safe update of orientation
						stateMutex.Lock()
						currentOrientation = payload.Orientation
						innerEarActive = true

						// LOG THE CALIBRATION STATUS
						// 0 = Uncalibrated, 3 = Fully Calibrated
						c := payload.Calibration
						log.Printf("[SENSOR] Calib: Sys:%d G:%d A:%d M:%d", c.S, c.G, c.A, c.M)

						stateMutex.Unlock()
					}
				}
			}
		}
	}()

	var reqMutex sync.Mutex
	var overrideMutex sync.RWMutex
	isOverriding := false

	// --- NEW: Goroutine to listen for manual commands from Stdin ---
	go func() {
		scanner := bufio.NewScanner(os.Stdin)
		for scanner.Scan() {
			text := strings.TrimSpace(scanner.Text())
			if text == "calibrate" || text == "rest" {
				overrideMutex.Lock()
				isOverriding = true
				overrideMutex.Unlock()

				log.Printf("[MANUAL] Executing manual command: %s", text)
				reqMutex.Lock()
				req.Send(zmq4.NewMsgString(text))
				req.Recv()

				if text == "calibrate" {
					log.Printf("[MANUAL] Calibration done, returning to rest...")
					req.Send(zmq4.NewMsgString("rest"))
					req.Recv()
				}
				reqMutex.Unlock()

				overrideMutex.Lock()
				isOverriding = false
				overrideMutex.Unlock()
			} else if text != "" {
				log.Printf("[MANUAL] Unknown command: %s. Use 'calibrate' or 'rest'", text)
			}
		}
	}()

	// Command the Pi to go to rest pose and trigger an initial state publish
	log.Printf("Commanding Pi to rest pose and requesting initial state...")
	reqMutex.Lock()
	req.Send(zmq4.NewMsgString("rest"))
	req.Recv()
	reqMutex.Unlock()

	// Wait up to 2 seconds for the state to initialize
	for i := 0; i < 20; i++ {
		stateMutex.Lock()
		init := stateInitialized
		stateMutex.Unlock()
		if init {
			break
		}
		time.Sleep(100 * time.Millisecond)
	}

	// Setup UDP Server
	addr := net.UDPAddr{
		Port: *port,
		IP:   net.ParseIP("0.0.0.0"),
	}
	conn, err := net.ListenUDP("udp", &addr)
	if err != nil {
		log.Fatalf("Failed to start UDP server: %v", err)
	}
	defer conn.Close()

	var lastMoveTime time.Time
	cooldownDur := time.Duration(*cooldownMs) * time.Millisecond

	buf := make([]byte, 2048)
	log.Printf("Bridge is fully initialized and waiting for UDP coordinates...")

	for {
		n, _, err := conn.ReadFromUDP(buf)
		if err != nil {
			log.Printf("Error reading from UDP: %v", err)
			continue
		}

		var payload Payload
		err = json.Unmarshal(buf[:n], &payload)
		if err != nil {
			log.Printf("Failed to parse JSON: %v", err)
			continue
		}

		overrideMutex.RLock()
		override := isOverriding
		overrideMutex.RUnlock()

		if override {
			continue // Drop frames while manually overriding
		}

		now := time.Now()
		if now.Sub(lastMoveTime) < cooldownDur {
			continue // Skip to prevent servo windup
		}

		// Calculate errors
		errorX := payload.CX - payload.TargetX
		errorY := payload.CY - payload.TargetY

		stateMutex.Lock()
		currentPan := panAngle
		currentTilt := tiltAngle
		normOrientation := getNormalizedOrientation(currentOrientation)
		sensorPitch := normOrientation.P // Get physical tilt
		sensorRoll := normOrientation.R  // Get physical roll
		isEarActive := innerEarActive
		stateMutex.Unlock()

		_ = sensorPitch
		_ = sensorRoll

		movedPan := false
		movedTilt := false

		newPan := currentPan
		newTilt := currentTilt

		// Adjust Pan (Vision Tracking)
		if math.Abs(errorX) > *deadzone {
			step := clip(errorX*(*ki), -*maxStep, *maxStep)
			newPan += step // Fixed axis direction
			movedPan = true
			log.Printf("[DEBUG] PAN VISION: error_x=%.1f, step=%.2f", errorX, step)
		}

		// Apply the Deadzone Logic (Stabilization)
		if isEarActive {
			if math.Abs(sensorPitch) > *imuDeadzone {
				// Only if we've tilted more than deadzone degrees do we calculate a correction
				stabilizationStep := sensorPitch * 0.8 // Adjust gain as needed
				newTilt -= stabilizationStep
				movedTilt = true
				log.Printf("[DEBUG] TILT STAB: pitch=%.1f, step=%.2f", sensorPitch, stabilizationStep)
			} else {
				// We are within the "Safe Zone" - keep current tilt to prevent buzzing
			}
		}

		// Adjust Tilt (Vision Tracking)
		if math.Abs(errorY) > *deadzone {
			step := clip(errorY*(*ki), -*maxStep, *maxStep)
			newTilt += step // Fixed axis direction
			movedTilt = true
			log.Printf("[DEBUG] TILT VISION: error_y=%.1f, step=%.2f", errorY, step)
		}

		// Send Pan Command
		if movedPan {
			newPan = clip(newPan, 0, 180)
			cmd := fmt.Sprintf("servo 4 angle %d", int(newPan))
			reqMutex.Lock()
			err = req.Send(zmq4.NewMsgString(cmd))
			if err != nil {
				log.Printf("ZMQ Error sending pan: %v", err)
			} else {
				_, err = req.Recv()
				if err != nil {
					log.Printf("ZMQ Error receiving pan reply: %v", err)
				} else {
					log.Printf("[DEBUG] PAN -> angle=%.1f", newPan)
				}
			}
			reqMutex.Unlock()
		}

		// Send Tilt Command
		if movedTilt {
			newTilt = clip(newTilt, 0, 180)
			cmd := fmt.Sprintf("servo 0 angle %d", int(newTilt))
			reqMutex.Lock()
			err = req.Send(zmq4.NewMsgString(cmd))
			if err != nil {
				log.Printf("ZMQ Error sending tilt: %v", err)
			} else {
				_, err = req.Recv()
				if err != nil {
					log.Printf("ZMQ Error receiving tilt reply: %v", err)
				} else {
					log.Printf("[DEBUG] TILT -> angle=%.1f", newTilt)
				}
			}
			reqMutex.Unlock()
		}

		if movedPan || movedTilt {
			lastMoveTime = now

			// Update local state immediately so we don't recalculate from stale state
			stateMutex.Lock()
			if movedPan {
				panAngle = newPan
			}
			if movedTilt {
				tiltAngle = newTilt
			}
			stateMutex.Unlock()
		}
	}
}
