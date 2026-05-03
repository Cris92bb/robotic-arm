import zmq
import pyaudio
import webrtcvad
import numpy as np
from openwakeword.model import Model
from faster_whisper import WhisperModel
import time
import collections
import os

# ZMQ Setup
VISION_PORT = "5563"
context = zmq.Context()
# brain.py binds to SUB, so we connect as PUB
socket = context.socket(zmq.PUB)
socket.connect(f"tcp://127.0.0.1:{VISION_PORT}")

# PyAudio Setup
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000
CHUNK = 320 # 20ms of audio for VAD at 16000Hz (16000 * 0.02 = 320 samples)

# VAD Setup
vad = webrtcvad.Vad(3) # Aggressiveness level 3 (most aggressive)

# OpenWakeword Setup
# Let's use the pre-trained models which are loaded by default if we don't pass anything.
wakeword_model = Model()

# Faster Whisper Setup
whisper_model = WhisperModel("tiny.en", device="cpu", compute_type="int8")

print("Ear is starting up...")

def process_audio():
    audio = pyaudio.PyAudio()
    stream = audio.open(format=FORMAT,
                        channels=CHANNELS,
                        rate=RATE,
                        input=True,
                        frames_per_buffer=CHUNK)

    print("Listening for wake word...")

    recording = False
    recorded_chunks = []
    silence_counter = 0
    SILENCE_LIMIT = 50 # roughly 1 second of silence to stop recording

    while True:
        try:
            pcm_data = stream.read(CHUNK, exception_on_overflow=False)

            is_speech = vad.is_speech(pcm_data, RATE)

            if not recording:
                # Need to feed np array to wakeword model, 16-bit PCM
                np_data = np.frombuffer(pcm_data, dtype=np.int16)
                wakeword_model.predict(np_data)

                # Check if any wakeword score is above threshold
                wakeword_detected = False
                for model_name, score in wakeword_model.prediction_buffer.items():
                    if score[-1] > 0.5: # Threshold
                        wakeword_detected = True
                        break

                if wakeword_detected:
                    print("Wake word detected! Recording...")
                    recording = True
                    recorded_chunks = []
                    silence_counter = 0
                    # clear wakeword predictions
                    for k in wakeword_model.prediction_buffer:
                        wakeword_model.prediction_buffer[k][-1] = 0.0

            else:
                recorded_chunks.append(pcm_data)

                if not is_speech:
                    silence_counter += 1
                else:
                    silence_counter = 0

                if silence_counter > SILENCE_LIMIT:
                    print("Silence detected. Stopping recording and transcribing...")
                    recording = False

                    # Transcribe
                    audio_data = np.frombuffer(b''.join(recorded_chunks), np.int16).astype(np.float32) / 32768.0
                    segments, info = whisper_model.transcribe(audio_data, beam_size=5)

                    text = " ".join([segment.text for segment in segments]).strip()
                    print(f"Transcribed: {text}")

                    if text:
                        # Publish to ZMQ
                        message = f"[AUDIO] User said: {text}"
                        print(f"Publishing: {message}")
                        socket.send_string(message)

                    print("Listening for wake word...")

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error: {e}")
            break

    stream.stop_stream()
    stream.close()
    audio.terminate()

if __name__ == '__main__':
    process_audio()
