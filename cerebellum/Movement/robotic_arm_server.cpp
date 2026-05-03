#include <iostream>
#include <string>
#include <sstream>
#include <vector>
#include <map>
#include <chrono>
#include <thread>
#include <cmath>
#include <zmq.hpp>
#include <nlohmann/json.hpp>
#include <fcntl.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <linux/i2c-dev.h>

using json = nlohmann::json;

// --- Config ---
const std::string COMMAND_PORT = "5555";
const std::string PUBLISH_PORT = "5556";
const std::string PUB_TOPIC = "servo_state";

const int PCA9685_ADDRESS = 0x40; // Default address
const int MODE1 = 0x00;
const int PRESCALE = 0xFE;
const int LED0_ON_L = 0x06;

// Pulse width bounds matching Python
const int MIN_PULSE = 500;
const int MAX_PULSE = 2850;

// State management
std::map<int, int> current_angles = {
    {0, 12},   // servo_A
    {1, 90},   // servo_B
    {2, 130},  // servo_C
    {3, 100},  // servo_D
    {4, 90}    // servo_E
};

int i2c_file;

// I2C Helpers
void write_byte(int reg, int value) {
    unsigned char buf[2] = {(unsigned char)reg, (unsigned char)value};
    if (write(i2c_file, buf, 2) != 2) {
        std::cerr << "!! WARNING: I2C write failed for reg " << reg << std::endl;
    }
}

int read_byte(int reg) {
    unsigned char buf[1] = {(unsigned char)reg};
    if (write(i2c_file, buf, 1) != 1) {
        return -1;
    }
    unsigned char val;
    if (read(i2c_file, &val, 1) != 1) {
        return -1;
    }
    return val;
}

// PCA9685 initialization
void init_pca9685() {
    std::string i2c_filename = "/dev/i2c-1";
    i2c_file = open(i2c_filename.c_str(), O_RDWR);
    if (i2c_file < 0) {
        std::cerr << "Could not open I2C bus " << i2c_filename << ". Will continue in mock mode." << std::endl;
    } else {
        if (ioctl(i2c_file, I2C_SLAVE, PCA9685_ADDRESS) < 0) {
            std::cerr << "Failed to acquire bus access and/or talk to slave." << std::endl;
        } else {
            write_byte(MODE1, 0x00);

            // Set frequency to 50Hz
            float freq = 50.0;
            float prescaleval = 25000000.0;
            prescaleval /= 4096.0;
            prescaleval /= freq;
            prescaleval -= 1.0;
            int prescale = floor(prescaleval + 0.5);

            int oldmode = read_byte(MODE1);
            int newmode = (oldmode & 0x7F) | 0x10; // sleep
            write_byte(MODE1, newmode);
            write_byte(PRESCALE, prescale);
            write_byte(MODE1, oldmode);
            std::this_thread::sleep_for(std::chrono::milliseconds(5));
            write_byte(MODE1, oldmode | 0xa0);
        }
    }
}

// Convert angle (0-180) to PCA9685 tick value (0-4095)
int angle_to_tick(int angle) {
    // Pulse width in microseconds
    float pulse_width = MIN_PULSE + (float)angle / 180.0 * (MAX_PULSE - MIN_PULSE);
    // Period for 50Hz is 20ms = 20000us. Ticks = 4096 * (pulse_width / 20000)
    int tick = (int)(4096.0 * (pulse_width / 20000.0));
    return tick;
}

void set_pwm(int channel, int on, int off) {
    if (i2c_file >= 0) {
        write_byte(LED0_ON_L + 4 * channel, on & 0xFF);
        write_byte(LED0_ON_L + 4 * channel + 1, on >> 8);
        write_byte(LED0_ON_L + 4 * channel + 2, off & 0xFF);
        write_byte(LED0_ON_L + 4 * channel + 3, off >> 8);
    }
}

void move_servo(int servo_id, int to_angle, bool smooth = false) {
    if (current_angles.find(servo_id) == current_angles.end()) {
        std::cerr << "Unknown servo id " << servo_id << std::endl;
        return;
    }

    if (smooth) {
        int from_angle = current_angles[servo_id];
        int step = (from_angle < to_angle) ? 1 : -1;
        for (int angle = from_angle; angle != to_angle + step; angle += step) {
            set_pwm(servo_id, 0, angle_to_tick(angle));
            std::this_thread::sleep_for(std::chrono::milliseconds(35));
        }
    } else {
        set_pwm(servo_id, 0, angle_to_tick(to_angle));
    }

    current_angles[servo_id] = to_angle;
}

void rest_pose() {
    std::cout << "Moving to rest pose..." << std::endl;
    move_servo(0, 12, true);
    move_servo(1, 90, true);
    move_servo(2, 130, true);
    move_servo(3, 100, true);
    move_servo(4, 90, true);
    std::cout << "In rest pose." << std::endl;
}

void calibration_dance() {
    std::cout << "Starting Calibration Dance..." << std::endl;
    std::this_thread::sleep_for(std::chrono::seconds(2));
    move_servo(0, 45, false);
    std::this_thread::sleep_for(std::chrono::seconds(1));
    move_servo(0, 135, false);
    std::this_thread::sleep_for(std::chrono::seconds(1));
    move_servo(0, 90, false);

    move_servo(4, 0, false);
    std::this_thread::sleep_for(std::chrono::seconds(1));
    move_servo(4, 180, false);
    std::this_thread::sleep_for(std::chrono::seconds(1));
    move_servo(4, 90, false);
    std::cout << "Calibration Dance complete." << std::endl;
}

std::string process_command(const std::string& message) {
    std::istringstream iss(message);
    std::vector<std::string> parts;
    std::string part;
    while (iss >> part) {
        parts.push_back(part);
    }

    if (parts.empty()) return "Error: Empty command.";

    if (parts.size() == 4 && parts[0] == "servo" && parts[2] == "angle") {
        try {
            int servo_id = std::stoi(parts[1]);
            int angle = std::stoi(parts[3]);

            if (current_angles.find(servo_id) == current_angles.end()) {
                return "Error: Invalid servo ID.";
            }
            if (angle < 0 || angle > 180) {
                return "Error: Invalid angle. Must be 0-180.";
            }

            move_servo(servo_id, angle, false);
            return "OK: Moving servo " + std::to_string(servo_id) + " to " + std::to_string(angle) + ".";
        } catch (...) {
            return "Error: Could not parse integers.";
        }
    } else if (parts.size() == 1 && parts[0] == "rest") {
        rest_pose();
        return "OK: Moved to rest pose.";
    } else if (parts.size() == 1 && parts[0] == "calibrate") {
        calibration_dance();
        return "OK: Calibration Dance complete.";
    }

    return "Error: Invalid command format. Use 'servo X angle Y', 'rest', or 'calibrate'.";
}

int main() {
    init_pca9685();

    zmq::context_t context(1);
    zmq::socket_t command_socket(context, ZMQ_REP);
    command_socket.bind("tcp://*:" + COMMAND_PORT);

    zmq::socket_t publish_socket(context, ZMQ_PUB);
    publish_socket.bind("tcp://*:" + PUBLISH_PORT);

    std::cout << "Servo server started..." << std::endl;
    std::cout << "Listening for commands on port " << COMMAND_PORT << std::endl;
    std::cout << "Publishing state on port " << PUBLISH_PORT << std::endl;

    rest_pose();

    while (true) {
        zmq::message_t request;
        auto res = command_socket.recv(request, zmq::recv_flags::none);
        if (res) {
            std::string message(static_cast<char*>(request.data()), request.size());
            std::cout << "Received command: '" << message << "'" << std::endl;

            std::string response = process_command(message);

            zmq::message_t reply(response.size());
            memcpy(reply.data(), response.c_str(), response.size());
            command_socket.send(reply, zmq::send_flags::none);

            // Publish state
            json state_json;
            // The go script expects keys as strings
            for (const auto& pair : current_angles) {
                state_json[std::to_string(pair.first)] = pair.second;
            }

            std::string state_message = PUB_TOPIC + " " + state_json.dump();
            zmq::message_t pub_msg(state_message.size());
            memcpy(pub_msg.data(), state_message.c_str(), state_message.size());
            publish_socket.send(pub_msg, zmq::send_flags::none);

            std::cout << "Published state: " << state_json.dump() << std::endl;
        }
    }

    return 0;
}
