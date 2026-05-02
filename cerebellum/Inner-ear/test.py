import time
import board
import adafruit_bno055

# Initialize I2C
i2c = board.I2C()

# IMPORTANT: Using 0x29 because that's where your sensor lives!
sensor = adafruit_bno055.BNO055_I2C(i2c, address=0x29)

print("Inner Ear Online. Move the robot to see orientation...")

try:
    while True:
        # Euler angles: (Heading, Roll, Pitch)
        # Heading = Compass direction
        # Roll = Side-to-side tilt
        # Pitch = Forward/Backward tilt
        heading, roll, pitch = sensor.euler
        
        print(f"H: {heading:5.1f} | R: {roll:5.1f} | P: {pitch:5.1f}")
        time.sleep(0.2)
except KeyboardInterrupt:
    print("\nShutting down.")