import os
import zmq
import time
import re
from faster_whisper import WhisperModel
import speech_recognition as sr

# ==========================================
# CONFIGURATION
# ==========================================
LAPTOP_IP = "192.168.1.114"  # <--- Verify your Laptop's IP
BRIDGE_ADDR = f"tcp://{LAPTOP_IP}:5558"
MODEL_SIZE = "tiny.en" 

# Whisper 'tiny' sometimes mishears words. 
# We give it a fuzzy list of acceptable wake words.
WAKE_WORDS = ["hey bot", "hey bought", "a bot", "hey but", "hey about"]

# ==========================================
# NETWORK & NEURAL SETUP
# ==========================================
print(f"[EAR] Connecting to Bridge at {BRIDGE_ADDR}...")
context = zmq.Context()
socket = context.socket(zmq.PUB)
socket.connect(BRIDGE_ADDR)

print("[EAR] Loading Neural STT Engine...")
model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")

def is_wake_word(text):
    """Cleans the text and checks if a wake word is inside it."""
    clean_text = re.sub(r'[^\w\s]', '', text.lower())
    for w in WAKE_WORDS:
        if w in clean_text:
            return True
    return False

def listen_loop():
    recognizer = sr.Recognizer()
    
    with sr.Microphone() as source:
        print("\n[EAR] Calibrating microphone for room noise...")
        recognizer.adjust_for_ambient_noise(source, duration=2)
        print("[EAR] System Online. Sleeping until you say 'Hey Bot'.")
        
        while True:
            try:
                # --------------------------------------------------
                # STATE 1: PASSIVE SLEEP (Listen for Wake Word)
                # --------------------------------------------------
                recognizer.pause_threshold = 0.5 # Fast turnaround
                audio = recognizer.listen(source, phrase_time_limit=3)
                
                temp_wav = "/dev/shm/wake_check.wav"
                with open(temp_wav, "wb") as f:
                    f.write(audio.get_wav_data())
                
                segments, _ = model.transcribe(temp_wav)
                passive_text = " ".join([s.text for s in segments]).strip()
                
                if passive_text and is_wake_word(passive_text):
                    # --------------------------------------------------
                    # STATE 2: ACTIVE FOCUS (Listen for Command)
                    # --------------------------------------------------
                    print("\n" + "="*40)
                    print("[EAR] *** WOKE UP! SPEAK YOUR COMMAND ***")
                    print("="*40 + "\n")
                    
                    # Set the 2-second pause threshold as requested
                    recognizer.pause_threshold = 2.0 
                    
                    # Wait for the user to speak their actual command
                    cmd_audio = recognizer.listen(source, timeout=5, phrase_time_limit=15)
                    print("[EAR] Command captured. Transcribing...")
                    
                    cmd_wav = "/dev/shm/command.wav"
                    with open(cmd_wav, "wb") as f:
                        f.write(cmd_audio.get_wav_data())
                        
                    cmd_segments, _ = model.transcribe(cmd_wav)
                    active_text = " ".join([s.text for s in cmd_segments]).strip()
                    
                    if active_text:
                        print(f"[HEARD]: {active_text}")
                        # Fire it to the laptop's Brain!
                        socket.send_string(f"User said: {active_text}")
                    
                    print("\n[EAR] Going back to sleep...")
                    
            except sr.WaitTimeoutError:
                # This happens if it wakes up but you don't say a command within 5 seconds
                print("[EAR] No command heard. Going back to sleep...")
            except Exception as e:
                pass # Ignore random audio spikes

if __name__ == "__main__":
    listen_loop()