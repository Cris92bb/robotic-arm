import zmq
import time

# Setup ZMQ Publisher on port 5562
context = zmq.Context()
socket = context.socket(zmq.PUB)
socket.bind("tcp://*:5562")

print("Brain Stem starting up... waiting 2 seconds for clients to connect.")
time.sleep(2) # Give ZMQ time to handshake

text_to_say = "Hello! My visual cortex and vocal cords are now connected over the network."
print(f"Sending thought: {text_to_say}")

socket.send_string(text_to_say)
print("Thought sent!")