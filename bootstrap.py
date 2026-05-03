import socket
import json
import os
import re
import subprocess

def get_laptop_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # doesn't even have to be reachable
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP

def main():
    print("Running bootstrap process...")

    # 1. Update/read config
    config_path = "config.json"
    if not os.path.exists(config_path):
        print("Config file not found. Creating default config.json")
        config = {"laptop_ip": "", "raspberry_pi_ip": "192.168.1.101"}
        with open(config_path, "w") as f:
            json.dump(config, f, indent=4)
    else:
        with open(config_path, "r") as f:
            config = json.load(f)

    current_ip = get_laptop_ip()
    print(f"Detected Laptop IP: {current_ip}")
    config["laptop_ip"] = current_ip

    with open(config_path, "w") as f:
        json.dump(config, f, indent=4)

    print(f"Loaded Raspberry Pi IP: {config['raspberry_pi_ip']}")

    # 2. Update IPs in scripts
    # Update voice.py
    voice_path = "cerebellum/Voice/voice.py"
    if os.path.exists(voice_path):
        with open(voice_path, "r") as f:
            content = f.read()
        content = re.sub(r'BRIDGE_IP\s*=\s*".*"', f'BRIDGE_IP = "{current_ip}"', content)
        with open(voice_path, "w") as f:
            f.write(content)
        print(f"Updated {voice_path} with new Laptop IP.")

    # Update main.go with the Raspberry Pi IP (we can pass it as a flag or update it in a run script)
    # The bridge takes a flag --pi-ip. Since the user wants this to be easy, let's create a script to run the bridge.
    run_bridge_script = "brain/Bridge/run_bridge.sh"
    with open(run_bridge_script, "w") as f:
        f.write(f"#!/bin/bash\n")
        f.write(f"cd src && ./bridge_bin --pi-ip {config['raspberry_pi_ip']}\n")
    os.chmod(run_bridge_script, 0o755)
    print(f"Created {run_bridge_script} to run the Go Bridge with the Pi IP.")

    # 3. Build C++ Movement Server (Reminder)
    print("NOTE: The C++ movement server (cerebellum/Movement/robotic_arm_server.cpp) must be compiled natively on the Raspberry Pi or using a cross-compiler. Use this command on the Pi:")
    print("      g++ -std=c++11 -o robotic_arm_server_cpp robotic_arm_server.cpp -lzmq")

    # 4. Build Go Bridge
    bridge_dir = "brain/Bridge/src"
    if os.path.exists(bridge_dir):
        print(f"Building Go Bridge in {bridge_dir}...")
        subprocess.run(["go", "build", "-o", "bridge_bin", "main.go"], cwd=bridge_dir)

    # 5. Build React Dashboard (if exists)
    dashboard_dir = "brain/dashboard"
    if os.path.exists(dashboard_dir):
        print(f"Building React Dashboard in {dashboard_dir}...")

        # Write .env BEFORE building so Vite can bake in the IP
        env_path = os.path.join(dashboard_dir, ".env")
        with open(env_path, "w") as f:
            f.write(f"VITE_BRIDGE_IP={current_ip}\n")
            f.write(f"VITE_PI_IP={config['raspberry_pi_ip']}\n")
        print(f"Updated {env_path}")

        subprocess.run(["npm", "install"], cwd=dashboard_dir)
        subprocess.run(["npm", "run", "build"], cwd=dashboard_dir)

    print("Bootstrap complete. You can now deploy 'cerebellum' to the Pi, and run the Brain on your laptop.")

if __name__ == "__main__":
    main()
