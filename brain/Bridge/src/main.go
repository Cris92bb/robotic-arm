package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"math"
	"net"
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

	var stateMutex sync.Mutex
	panAngle := 90.0
	tiltAngle := 90.0
	stateInitialized := false

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

	// Command the Pi to go to rest pose and trigger an initial state publish
	log.Printf("Commanding Pi to rest pose and requesting initial state...")
	req.Send(zmq4.NewMsgString("rest"))
	req.Recv()

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
		stateMutex.Unlock()

		movedPan := false
		movedTilt := false

		newPan := currentPan
		newTilt := currentTilt

		// Adjust Pan
		if math.Abs(errorX) > *deadzone {
			step := clip(errorX*(*ki), -*maxStep, *maxStep)
			newPan += step // Fixed axis direction
			newPan = clip(newPan, 0, 180)

			cmd := fmt.Sprintf("servo 4 angle %d", int(newPan))
			err = req.Send(zmq4.NewMsgString(cmd))
			if err != nil {
				log.Printf("ZMQ Error sending pan: %v", err)
			} else {
				_, err = req.Recv()
				if err != nil {
					log.Printf("ZMQ Error receiving pan reply: %v", err)
				} else {
					movedPan = true
					log.Printf("[DEBUG] PAN: error_x=%.1f, step=%.2f -> pan_angle=%.1f", errorX, step, newPan)
				}
			}
		}

		// Adjust Tilt
		if math.Abs(errorY) > *deadzone {
			step := clip(errorY*(*ki), -*maxStep, *maxStep)
			newTilt += step // Fixed axis direction
			newTilt = clip(newTilt, 0, 180)

			cmd := fmt.Sprintf("servo 0 angle %d", int(newTilt))
			err = req.Send(zmq4.NewMsgString(cmd))
			if err != nil {
				log.Printf("ZMQ Error sending tilt: %v", err)
			} else {
				_, err = req.Recv()
				if err != nil {
					log.Printf("ZMQ Error receiving tilt reply: %v", err)
				} else {
					movedTilt = true
					log.Printf("[DEBUG] TILT: error_y=%.1f, step=%.2f -> tilt_angle=%.1f", errorY, step, newTilt)
				}
			}
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
