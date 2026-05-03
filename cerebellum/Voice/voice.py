import zmq
import subprocess
import threading
import time
import os
import shutil
import json
import queue

# ==========================================
# CONFIGURATION
# ==========================================
BRIDGE_IP = "192.168.0.2"  # <--- CHANGE TO LAPTOP IP
BRIDGE_PORT = "5562"
VOICE_MODEL = "en_US-lessac-low.onnx"
VOICE_JSON = f"{VOICE_MODEL}.json"

# ==========================================
# RAM DISK OPTIMIZATION
# ==========================================
RAM_DISK = "/dev/shm"
RAM_MODEL_PATH = f"{RAM_DISK}/{VOICE_MODEL}"

print("[VOCAL CORDS] Transferring Neural Model to high-speed RAM...")
if not os.path.exists(RAM_MODEL_PATH):
    shutil.copy(VOICE_MODEL, RAM_DISK)
    shutil.copy(VOICE_JSON, RAM_DISK)

print("[VOCAL CORDS] Starting continuous neural engine...")
piper_cmd = [
    "./piper/piper",
    "-m", RAM_MODEL_PATH,
    "--json-input"
]

tts_pipeline = subprocess.Popen(
    piper_cmd,
    stdin=subprocess.PIPE,
    text=True
)
print("[VOCAL CORDS] Engine is warm and idling.")

# ==========================================
# THE DUAL-QUEUE SYSTEM
# ==========================================
thought_queue = queue.Queue() # Holds raw text strings
audio_queue = queue.Queue()   # Holds finished .wav file paths

def generator_worker():
    """THREAD 1: Generates audio files as fast as possible."""
    msg_counter = 0
    
    while True:
        text = thought_queue.get()
        msg_counter += 1
        out_wav = f"{RAM_DISK}/speech_{msg_counter}.wav"
        
        print(f"[GENERATOR] Processing: '{text}'")
        
        # 1. Tell Piper to generate the file
        payload = json.dumps({"text": text, "output_file": out_wav})
        tts_pipeline.stdin.write(payload + "\n")
        tts_pipeline.stdin.flush()
        
        # 2. Poll the RAM disk to know when the file is finished
        while not os.path.exists(out_wav):
            time.sleep(0.01)
            
        last_size = -1
        while True:
            current_size = os.path.getsize(out_wav)
            if current_size == last_size and current_size > 44:
                break
            last_size = current_size
            time.sleep(0.01)

        # 3. File is done! Throw it to the player thread and immediately grab the next text.
        print(f"[GENERATOR] File ready. Pushing to Player Queue.")
        audio_queue.put(out_wav)
        thought_queue.task_done()

def player_worker():
    """THREAD 2: Plays audio files back-to-back with zero gaps."""
    while True:
        out_wav = audio_queue.get()
        
        print(f"[PLAYER] Speaking audio block...")
        # This blocks, but it doesn't matter because the Generator thread is still running!
        aplay_cmd = f"aplay -q {out_wav} 2>/dev/null"
        subprocess.run(aplay_cmd, shell=True)
        
        try:
            os.remove(out_wav)
        except:
            pass
            
        audio_queue.task_done()

def main():
    # Start both independent engine threads
    threading.Thread(target=generator_worker, daemon=True).start()
    threading.Thread(target=player_worker, daemon=True).start()

    # Setup Network
    context = zmq.Context()
    socket = context.socket(zmq.SUB)
    
    connect_url = f"tcp://{BRIDGE_IP}:{BRIDGE_PORT}"
    print(f"Connecting to Brain Stem at {connect_url}...")
    
    while True:
        try:
            socket.connect(connect_url)
            socket.setsockopt_string(zmq.SUBSCRIBE, "") 
            break
        except Exception as e:
            print(f"Waiting for Brain... {e}")
            time.sleep(2)

    print("Vocal Cords Online. Listening for thoughts...")

    while True:
        try:
            message = socket.recv_string()
            thought_queue.put(message)
        except Exception as e:
            print(f"[ERROR] Network error: {e}")
            time.sleep(1)

if __name__ == "__main__":
    main()