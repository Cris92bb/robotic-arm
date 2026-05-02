import time
import zmq
import json
import board
import adafruit_bno055

# --- Config ---
BNO_ADDRESS = 0x29
PUB_PORT = "5557"  # Separate port from movement server

def run_inner_ear():
    # Setup ZMQ
    context = zmq.Context()
    socket = context.socket(zmq.PUB)
    socket.bind(f"tcp://*:{PUB_PORT}")

    # Setup Sensor
    i2c = board.I2C()
    sensor = adafruit_bno055.BNO055_I2C(i2c, address=BNO_ADDRESS)

    print(f"Inner Ear broadcasting on port {PUB_PORT}...")

    while True:
        try:
            # Read orientation and calibration status
            euler = sensor.euler
            # sys, gyro, accel, mag (0-3 scale, 3 is best)
            cal_sys, cal_gyro, cal_accel, cal_mag = sensor.calibration_status

            if euler[0] is not None:
                payload = {
                    "orientation": {"h": euler[0], "r": euler[1], "p": euler[2]},
                    "calibration": {"s": cal_sys, "g": cal_gyro, "a": cal_accel, "m": cal_mag},
                    "timestamp": time.time()
                }
                socket.send_string(f"orientation {json.dumps(payload)}")
                print(f"Sent positions - h: {euler[0]:.2f}, r: {euler[1]:.2f}, p: {euler[2]:.2f}")
            
        except (OSError, RuntimeError) as e:
            # Instead of crashing, we just wait a tiny bit and try again
            print(f"Skipping frame due to I2C hiccup: {e}")
            time.sleep(0.01)
            continue
        except Exception as e:
            print(f"Sensor Read Error: {e}")
        
        time.sleep(0.05) # 20Hz is plenty for stabilization

if __name__ == "__main__":
    run_inner_ear()