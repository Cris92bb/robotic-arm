# Import necessary libraries
import time
import zmq
import json
from adafruit_servokit import ServoKit

# --- Servo Calibration (Use your found values) ---
MIN_PULSE = 500
MAX_PULSE = 2850

# --- ZMQ Configuration ---
COMMAND_PORT = "5555"  # Port for REQ/REP commands
PUBLISH_PORT = "5556"  # Port for PUB/SUB state publishing
PUB_TOPIC = "servo_state" # ZMQ topic for publishing

# --- Initialize ServoKit ---
kit = ServoKit(channels=16)

# --- Servo Definitions ---
# We use a dictionary to easily access servos by their ID
servos = {
    0: kit.servo[0],
    1: kit.servo[1],
    2: kit.servo[2],
    3: kit.servo[3],
    4: kit.servo[4],
}

# --- State Management ---
# Holds the last known angle for each servo.
# Initialize with your rest pose values.
current_angles = {
    0: 12,   # servo_A
    1: 90,   # servo_B
    2: 130,  # servo_C
    3: 100,  # servo_D
    4: 90    # servo_E
}

# --- Helper Functions (from your script) ---

def set_servo_calibration(servo_object):
    """Applies the calibrated pulse width to a servo object."""
    servo_object.actuation_range = 180
    servo_object.set_pulse_width_range(MIN_PULSE, MAX_PULSE)

def move_servo(servo_object, to_angle, from_angle=None, delay=0.035):
    """
    Moves a servo to a desired angle, with an optional smooth transition.
    Includes error handling for I2C communication failures.
    """
    if from_angle is not None:
        step = 1 if from_angle < to_angle else -1
        for angle in range(from_angle, to_angle + step, step):
            try:
                # This is the line that can fail due to hardware issues
                servo_object.angle = angle
            except OSError as e:
                # Catch the I/O error, print a warning, and continue running.
                print(f"!! WARNING: I2C communication error: {e}. Skipping step.")
                # 'continue' will skip this single angle and proceed with the loop.
                continue
            
            time.sleep(delay)
    else:
        try:
            # Also handle errors for direct (non-smooth) movements
            servo_object.angle = to_angle
        except OSError as e:
            print(f"!! WARNING: I2C communication error on direct move: {e}.")

    # We update the global state *after* the move is complete
    # Note: We'll find the servo_id to update the state dict
    for servo_id, obj in servos.items():
        if obj == servo_object:
            current_angles[servo_id] = to_angle
            break

def rest_pose():
    """Moves all servos to a predefined resting position."""
    print("Moving to rest pose...")
    # Use current_angles for smooth transitions to rest
    move_servo(servos[0], 12, from_angle=current_angles[0])
    move_servo(servos[1], 90, from_angle=current_angles[1])
    move_servo(servos[2], 130, from_angle=current_angles[2])
    move_servo(servos[3], 100, from_angle=current_angles[3])
    move_servo(servos[4], 90, from_angle=current_angles[4])
    print("In rest pose.")

# --- Main Server Function ---

def run_server():
    # Setup ZMQ context and sockets
    context = zmq.Context()
    
    # Socket for receiving commands
    command_socket = context.socket(zmq.REP)
    command_socket.bind(f"tcp://*:{COMMAND_PORT}")
    
    # Socket for publishing state
    publish_socket = context.socket(zmq.PUB)
    publish_socket.bind(f"tcp://*:{PUBLISH_PORT}")
    
    print(f"Servo server started...")
    print(f"Listening for commands on port {COMMAND_PORT}")
    print(f"Publishing state on port {PUBLISH_PORT}")

    # Apply calibration to all servos
    print("Calibrating servos...")
    for servo in servos.values():
        set_servo_calibration(servo)
    
    # Go to initial rest pose
    rest_pose()
    
    try:
        while True:
            # 1. Wait for a command
            message = command_socket.recv_string()
            print(f"Received command: '{message}'")
            
            response = "Acknowledged. No action taken."
            execute_move = False
            
            # 2. Parse the command
            try:
                parts = message.split()
                if len(parts) == 4 and parts[0] == 'servo' and parts[2] == 'angle':
                    servo_id = int(parts[1])
                    angle = int(parts[3])
                    
                    # 3. Validate the command
                    if servo_id not in servos:
                        response = f"Error: Invalid servo ID '{servo_id}'."
                    elif not (0 <= angle <= 180):
                        response = f"Error: Invalid angle '{angle}'. Must be 0-180."
                    else:
                        # Command is valid
                        response = f"OK: Moving servo {servo_id} to {angle}."
                        execute_move = True
                
                elif len(parts) == 1 and parts[0] == 'rest':
                    rest_pose()
                    response = "OK: Moved to rest pose."
                    execute_move = False
                
                else:
                    response = "Error: Invalid command format. Use 'servo X angle Y' or 'rest'."

            except (ValueError, IndexError):
                response = "Error: Invalid command. Could not parse."
            
            # 4. Execute the command (if valid)
            if execute_move:
                servo_obj = servos[servo_id]
                
                # This function now contains the error handling
                move_servo(servo_obj, angle)
                # Update the state tracker
                current_angles[servo_id] = angle
            # 5. Send the reply
            command_socket.send_string(response)
            
            # 6. Publish the new state (always, even on error)
            state_message = json.dumps(current_angles)
            publish_socket.send_string(f"{PUB_TOPIC} {state_message}")
            print(f"Published state: {state_message}")

    except KeyboardInterrupt:
        print("\nShutting down server...")
        rest_pose() # Go to rest on shutdown
    
    finally:
        # Clean up
        command_socket.close()
        publish_socket.close()
        context.term()
        print("Server shut down.")

# --- Start the server ---
if __name__ == "__main__":
    run_server()
