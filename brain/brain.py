import zmq
import time
import sys
from google import genai
from google.genai import types

# ==========================================
# CONFIGURATION & SECURITY
# ==========================================
API_FILE = "api_key.txt"
VISION_PORT = "5563" # Port to listen to the Go Bridge
VOICE_PORT = "5562"  # Port to send speech to the Pi

# Safely load the API key
try:
    with open(API_FILE, "r") as f:
        GEMINI_API_KEY = f.read().strip()
    if not GEMINI_API_KEY:
        raise ValueError("The API key file is empty.")
except Exception as e:
    print(f"\n[FATAL ERROR] Could not load API key: {e}")
    sys.exit(1)

# ==========================================
# NEURAL NETWORK SETUP (ZMQ)
# ==========================================
context = zmq.Context()

# 1. OUTGOING: Talk to the Pi's Vocal Cords
voice_socket = context.socket(zmq.PUB)
voice_socket.bind(f"tcp://*:{VOICE_PORT}")

# 2. INCOMING: Listen to the Go Bridge (Visual Cortex)
vision_socket = context.socket(zmq.SUB)
vision_socket.bind(f"tcp://*:{VISION_PORT}")
vision_socket.setsockopt_string(zmq.SUBSCRIBE, "") 

client = genai.Client(api_key=GEMINI_API_KEY)

print("=======================================")
print("[SYSTEM] Frontal Lobe Online.")
print(f"[SYSTEM] Listening for Go Bridge on port {VISION_PORT}...")
print(f"[SYSTEM] Publishing to Pi Vocal Cords on port {VOICE_PORT}...")
print("=======================================\n")
time.sleep(2) # Give ZMQ time to handshake

# ==========================================
# THE THOUGHT GENERATOR
# ==========================================
def generate_and_speak(prompt):
    print(f"\n[BRAIN] Generating thought for: '{prompt}'")
    
    sys_instruct = (
        "You are a robotic arm equipped with a camera. You have just looked at a human. "
        "Keep your response strictly under 2 sentences. Be friendly, slightly robotic, "
        "and do not use any markdown formatting or lists."
    )
    
    try:
        response = client.models.generate_content_stream(
            model="gemini-3.1-flash-lite-preview",
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=sys_instruct,
                temperature=0.6, 
            )
        )
        
        sentence_buffer = ""
        
        for chunk in response:
            if chunk.text:
                for char in chunk.text:
                    sentence_buffer += char
                    
                    # CHUNKING: Send the sentence the instant we hit punctuation
                    if char in ['.', '!', '?']:
                        clean_sentence = sentence_buffer.strip()
                        if clean_sentence:
                            print(f"[BRAIN -> PI]: {clean_sentence}")
                            voice_socket.send_string(clean_sentence)
                            sentence_buffer = "" 
                            
        # Flush any leftover words
        if sentence_buffer.strip():
            print(f"[BRAIN -> PI]: {sentence_buffer.strip()}")
            voice_socket.send_string(sentence_buffer.strip())
            
    except Exception as e:
        print(f"[BRAIN ERROR] LLM Pipeline crashed: {e}")

# ==========================================
# THE AUTONOMOUS LOOP (Always Listening)
# ==========================================
def main():
    last_spoken_time = 0
    cooldown_seconds = 15 # Wait 15 seconds before talking about the same person again
    
    while True:
        try:
            # Wait for a trigger from the Go bridge
            visual_event = vision_socket.recv_string()
            print(f"\n[VISUAL CORTEX DETECTED]: {visual_event}")
            
            current_time = time.time()
            if current_time - last_spoken_time > cooldown_seconds:
                # Tell Gemini what the Go script saw
                prompt = f"System log: {visual_event}. Acknowledge them."
                generate_and_speak(prompt)
                last_spoken_time = current_time
            else:
                print(f"[BRAIN] Ignoring event (Speech Cooldown Active. Wait {int(cooldown_seconds - (current_time - last_spoken_time))}s)")
                
        except KeyboardInterrupt:
            print("\nShutting down brain...")
            break
        except Exception as e:
            print(f"[ERROR] Network error: {e}")
            time.sleep(1)

if __name__ == "__main__":
    main()