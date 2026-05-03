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

// --- Structs ---

type Payload struct {
	TargetX    float64 `json:"target_x"`
	TargetY    float64 `json:"target_y"`
	CX         float64 `json:"cx"`
	CY         float64 `json:"cy"`
	Confidence float64 `json:"confidence"`
	TargetID   int     `json:"target_id"`
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

// --- Constants ---

const (
	restHeading = 185.25
	restRoll    = -77.00
	restPitch   = 121.19
)

// --- Helper Functions ---

func getNormalizedOrientation(raw Orientation) Orientation {
	return Orientation{
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

// --- Bridge Structure ---

type BridgeConfig struct {
	PiIP        string
	Port        int
	Ki          float64
	MaxStep     float64
	CooldownMs  int
	Deadzone    float64
	IMUDeadzone float64
}

type Bridge struct {
	config BridgeConfig

	// ZMQ Sockets
	req      zmq4.Socket
	sub      zmq4.Socket
	earSub   zmq4.Socket
	brainPub zmq4.Socket

	// State Mutexes
	stateMutex         sync.Mutex
	panAngle           float64
	tiltAngle          float64
	currentOrientation Orientation
	stateInitialized   bool
	innerEarActive     bool

	reqMutex sync.Mutex

	overrideMutex sync.RWMutex
	isOverriding  bool
}

func NewBridge(cfg BridgeConfig) *Bridge {
	return &Bridge{
		config:    cfg,
		panAngle:  90.0,
		tiltAngle: 90.0,
	}
}

func (b *Bridge) Initialize() error {
	// Setup ZMQ REQ socket for commands
	b.req = zmq4.NewReq(context.Background())
	zmqURL := fmt.Sprintf("tcp://%s:5555", b.config.PiIP)
	if err := b.req.Dial(zmqURL); err != nil {
		return fmt.Errorf("failed to connect to ZMQ server at %s: %v", zmqURL, err)
	}

	// Setup ZMQ SUB socket for state syncing
	b.sub = zmq4.NewSub(context.Background())
	subURL := fmt.Sprintf("tcp://%s:5556", b.config.PiIP)
	if err := b.sub.Dial(subURL); err != nil {
		return fmt.Errorf("failed to connect to ZMQ SUB server at %s: %v", subURL, err)
	}
	if err := b.sub.SetOption(zmq4.OptionSubscribe, "servo_state"); err != nil {
		return fmt.Errorf("failed to subscribe to servo_state: %v", err)
	}

	// Setup ZMQ SUB socket for Inner Ear
	b.earSub = zmq4.NewSub(context.Background())
	earURL := fmt.Sprintf("tcp://%s:5557", b.config.PiIP)
	if err := b.earSub.Dial(earURL); err != nil {
		log.Printf("Warning: Failed to connect to Inner Ear SUB at %s: %v. Inner Ear will be ignored.", earURL, err)
	} else {
		if err := b.earSub.SetOption(zmq4.OptionSubscribe, "orientation"); err != nil {
			log.Printf("Warning: Failed to subscribe to orientation: %v", err)
		}
	}

	// Setup ZMQ PUB socket for the Frontal Lobe (Brain)
	b.brainPub = zmq4.NewPub(context.Background())
	brainURL := "tcp://127.0.0.1:5563"
	if err := b.brainPub.Dial(brainURL); err != nil {
		log.Printf("Warning: Failed to connect to Frontal Lobe at %s: %v", brainURL, err)
	} else {
		log.Printf("[VISUAL CORTEX] Connected to Frontal Lobe on port 5563")
	}

	return nil
}

func (b *Bridge) Close() {
	if b.req != nil {
		b.req.Close()
	}
	if b.sub != nil {
		b.sub.Close()
	}
	if b.earSub != nil {
		b.earSub.Close()
	}
	if b.brainPub != nil {
		b.brainPub.Close()
	}
}

func (b *Bridge) Run() {
	go b.syncServoStateLoop()
	go b.syncInnerEarStateLoop()
	go b.listenManualCommandsLoop()

	b.commandRestPoseAndInit()

	b.startUDPServer()
}

func (b *Bridge) syncServoStateLoop() {
	for {
		msg, err := b.sub.Recv()
		if err != nil {
			log.Printf("ZMQ SUB Recv error: %v", err)
			continue
		}

		if len(msg.Frames) > 0 {
			parts := strings.SplitN(string(msg.Frames[0]), " ", 2)
			if len(parts) == 2 && parts[0] == "servo_state" {
				var state map[string]float64
				if err := json.Unmarshal([]byte(parts[1]), &state); err == nil {
					b.stateMutex.Lock()
					if val, ok := state["4"]; ok {
						b.panAngle = val
					}
					if val, ok := state["0"]; ok {
						b.tiltAngle = val
					}
					if !b.stateInitialized {
						b.stateInitialized = true
						log.Printf("[SYNC] Initial state synced: pan=%.1f, tilt=%.1f", b.panAngle, b.tiltAngle)
					}
					b.stateMutex.Unlock()
				}
			}
		}
	}
}

func (b *Bridge) syncInnerEarStateLoop() {
	for {
		msg, err := b.earSub.Recv()
		if err != nil {
			continue
		}

		if len(msg.Frames) > 0 {
			parts := strings.SplitN(string(msg.Frames[0]), " ", 2)
			if len(parts) == 2 && parts[0] == "orientation" {
				var payload SensorPayload
				if err := json.Unmarshal([]byte(parts[1]), &payload); err == nil {
					b.stateMutex.Lock()
					b.currentOrientation = payload.Orientation
					b.innerEarActive = true
					c := payload.Calibration
					log.Printf("[SENSOR] Calib: Sys:%d G:%d A:%d M:%d", c.S, c.G, c.A, c.M)
					b.stateMutex.Unlock()
				}
			}
		}
	}
}

func (b *Bridge) setOverride(status bool) {
	b.overrideMutex.Lock()
	b.isOverriding = status
	b.overrideMutex.Unlock()
}

func (b *Bridge) listenManualCommandsLoop() {
	scanner := bufio.NewScanner(os.Stdin)
	for scanner.Scan() {
		text := strings.TrimSpace(scanner.Text())
		if text == "calibrate" || text == "rest" {
			b.setOverride(true)

			log.Printf("[MANUAL] Executing manual command: %s", text)
			b.reqMutex.Lock()
			b.req.Send(zmq4.NewMsgString(text))
			b.req.Recv()

			if text == "calibrate" {
				log.Printf("[MANUAL] Calibration done, returning to rest...")
				b.req.Send(zmq4.NewMsgString("rest"))
				b.req.Recv()
			}
			b.reqMutex.Unlock()

			b.setOverride(false)
		} else if text != "" {
			log.Printf("[MANUAL] Unknown command: %s. Use 'calibrate' or 'rest'", text)
		}
	}
}

func (b *Bridge) commandRestPoseAndInit() {
	log.Printf("Commanding Pi to rest pose and requesting initial state...")
	b.reqMutex.Lock()
	b.req.Send(zmq4.NewMsgString("rest"))
	b.req.Recv()
	b.reqMutex.Unlock()

	for i := 0; i < 20; i++ {
		b.stateMutex.Lock()
		init := b.stateInitialized
		b.stateMutex.Unlock()
		if init {
			break
		}
		time.Sleep(100 * time.Millisecond)
	}
}

func (b *Bridge) handleTargetSwitch(payload *Payload, lastTargetID *int) {
	if payload.TargetID != *lastTargetID {
		var thought string
		switch payload.TargetID {
		case 0:
			log.Printf("[TARGET] Lost target. Returning to center.")
		case -1:
			log.Printf("[TARGET] Locked onto FACE.")
			thought = "I have locked my camera onto a human face."
		default:
			log.Printf("[TARGET] Locked onto Person ID: %d", payload.TargetID)
			thought = fmt.Sprintf("I have locked my camera onto person number %d.", payload.TargetID)
		}

		if thought != "" {
			if err := b.brainPub.Send(zmq4.NewMsgString(thought)); err != nil {
				log.Printf("[ZMQ ERROR] Failed to send thought to brain: %v", err)
			} else {
				log.Printf("[VISUAL CORTEX -> BRAIN]: Triggered thought.")
			}
		}

		*lastTargetID = payload.TargetID
	}
}

func (b *Bridge) sendServoCommand(servoID int, angle float64) {
	cmd := fmt.Sprintf("servo %d angle %d", servoID, int(angle))
	b.reqMutex.Lock()
	err := b.req.Send(zmq4.NewMsgString(cmd))
	if err != nil {
		log.Printf("ZMQ Error sending servo %d command: %v", servoID, err)
	} else {
		_, err = b.req.Recv()
		if err != nil {
			log.Printf("ZMQ Error receiving servo %d reply: %v", servoID, err)
		} else {
			name := "PAN"
			if servoID == 0 {
				name = "TILT"
			}
			log.Printf("[DEBUG] %s -> angle=%.1f", name, angle)
		}
	}
	b.reqMutex.Unlock()
}

func (b *Bridge) startUDPServer() {
	addr := net.UDPAddr{
		Port: b.config.Port,
		IP:   net.ParseIP("0.0.0.0"),
	}
	conn, err := net.ListenUDP("udp", &addr)
	if err != nil {
		log.Fatalf("Failed to start UDP server: %v", err)
	}
	defer conn.Close()

	var lastMoveTime time.Time
	cooldownDur := time.Duration(b.config.CooldownMs) * time.Millisecond
	lastTargetID := 0
	buf := make([]byte, 2048)

	log.Printf("Bridge is fully initialized and waiting for UDP coordinates...")

	for {
		n, _, err := conn.ReadFromUDP(buf)
		if err != nil {
			log.Printf("Error reading from UDP: %v", err)
			continue
		}

		var payload Payload
		if err := json.Unmarshal(buf[:n], &payload); err != nil {
			log.Printf("Failed to parse JSON: %v", err)
			continue
		}

		b.handleTargetSwitch(&payload, &lastTargetID)

		b.overrideMutex.RLock()
		override := b.isOverriding
		b.overrideMutex.RUnlock()

		if override {
			continue
		}

		now := time.Now()
		if now.Sub(lastMoveTime) < cooldownDur {
			continue
		}

		b.processMovement(&payload, now, &lastMoveTime)
	}
}

func (b *Bridge) processMovement(payload *Payload, now time.Time, lastMoveTime *time.Time) {
	errorX := payload.CX - payload.TargetX
	errorY := payload.CY - payload.TargetY

	b.stateMutex.Lock()
	currentPan := b.panAngle
	currentTilt := b.tiltAngle
	normOrientation := getNormalizedOrientation(b.currentOrientation)
	sensorPitch := normOrientation.P
	isEarActive := b.innerEarActive
	b.stateMutex.Unlock()

	movedPan := false
	movedTilt := false

	newPan := currentPan
	newTilt := currentTilt

	// Adjust Pan (Vision Tracking)
	if math.Abs(errorX) > b.config.Deadzone {
		step := clip(errorX*b.config.Ki, -b.config.MaxStep, b.config.MaxStep)
		newPan += step
		movedPan = true
		log.Printf("[DEBUG] PAN VISION: error_x=%.1f, step=%.2f", errorX, step)
	}

	// Apply Stabilization
	if isEarActive {
		if math.Abs(sensorPitch) > b.config.IMUDeadzone {
			stabilizationStep := sensorPitch * 0.8
			newTilt -= stabilizationStep
			movedTilt = true
			log.Printf("[DEBUG] TILT STAB: pitch=%.1f, step=%.2f", sensorPitch, stabilizationStep)
		}
	}

	// Adjust Tilt (Vision Tracking)
	if math.Abs(errorY) > b.config.Deadzone {
		step := clip(errorY*b.config.Ki, -b.config.MaxStep, b.config.MaxStep)
		newTilt += step
		movedTilt = true
		log.Printf("[DEBUG] TILT VISION: error_y=%.1f, step=%.2f", errorY, step)
	}

	if movedPan {
		newPan = clip(newPan, 0, 180)
		b.sendServoCommand(4, newPan)
	}

	if movedTilt {
		newTilt = clip(newTilt, 0, 180)
		b.sendServoCommand(0, newTilt)
	}

	if movedPan || movedTilt {
		*lastMoveTime = now

		b.stateMutex.Lock()
		if movedPan {
			b.panAngle = newPan
		}
		if movedTilt {
			b.tiltAngle = newTilt
		}
		b.stateMutex.Unlock()
	}
}

func main() {
	cfg := BridgeConfig{}
	flag.StringVar(&cfg.PiIP, "pi-ip", "192.168.1.158", "IP address of the Raspberry Pi")
	flag.IntVar(&cfg.Port, "port", 8080, "UDP port to listen for coordinates")
	flag.Float64Var(&cfg.Ki, "ki", 0.05, "Integral gain for servos")
	flag.Float64Var(&cfg.MaxStep, "max-step", 30.0, "Maximum degree change per step")
	flag.IntVar(&cfg.CooldownMs, "cooldown", 50, "Cooldown between servo commands in milliseconds")
	flag.Float64Var(&cfg.Deadzone, "deadzone", 35.0, "Pixel margin of error where camera stops moving")
	flag.Float64Var(&cfg.IMUDeadzone, "imu-deadzone", 0.5, "Minimum degree change to trigger stabilization")
	flag.Parse()

	log.Printf("Starting Bridge. Listening on UDP port %d", cfg.Port)
	log.Printf("Raspberry Pi IP: %s", cfg.PiIP)
	log.Printf("Ki: %f, Max Step: %f, Cooldown: %dms", cfg.Ki, cfg.MaxStep, cfg.CooldownMs)

	bridge := NewBridge(cfg)
	if err := bridge.Initialize(); err != nil {
		log.Fatalf("Failed to initialize Bridge: %v", err)
	}
	defer bridge.Close()
	
	bridge.Run()
}
