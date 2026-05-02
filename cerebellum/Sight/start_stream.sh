#!/bin/bash

# ==========================================
# Optic Nerve Configuration
# ==========================================
TARGET_IP="192.168.1.114"  # <--- UPDATE THIS TO YOUR LAPTOP IP
PORT="5000"
WIDTH="640"
HEIGHT="480"
FPS="30"

# ==========================================

echo "=========================================="
echo " Starting Foveated Optic Stream..."
echo " Target: UDP://$TARGET_IP:$PORT"
echo " Resolution: ${WIDTH}x${HEIGHT} @ ${FPS}fps"
echo " Press Ctrl+C to stop the stream."
echo "=========================================="

# Execute the hardware encoder command
rpicam-vid -t 0 --width $WIDTH --height $HEIGHT --framerate $FPS --codec h264  --inline --bitrate 1500000 --intra 15 --nopreview --flush -o udp://$TARGET_IP:$PORT
