"""
Prototype: mmWave Triggered Object Classification
-------------------------------------------------
Hardware:
  - HLK-LD2450 (UART)
  - Logitech C310 (USB Camera)
  - Raspberry Pi 5

Logic:
  1. Poll mmWave sensor.
  2. If target detected AND time > last_trigger + 1.0s (Debounce):
     a. Capture frame from Camera.
     b. Run MobileNet inference.
     c. Print Class + Confidence.
"""

import cv2
import serial
import time
import struct
import os
import numpy as np
from datetime import datetime

# --- Configuration ---
MMWAVE_PORT = "/dev/ttyAMA0"
MMWAVE_BAUD = 256000
CAMERA_ID   = 0           # Usually 0 for first USB cam
DEBOUNCE_SEC = 1.0        # Time between triggers

# MobileNet Model Paths (Update these to your actual file names)
# You can use TFLite or ONNX models with OpenCV's DNN module.
MODEL_PATH  = "mobilenet_v2_1.0_224.tflite"
LABEL_PATH  = "labels.txt"

# --- mmWave Helpers (Reused) ---
FRAME_HEADER = bytes([0xAA, 0xFF, 0x03, 0x00])
FRAME_TAIL   = bytes([0x55, 0xCC])
FRAME_SIZE   = 30

def decode_signed(raw: int) -> int:
    magnitude = raw & 0x7FFF
    return -magnitude if (raw & 0x8000) else magnitude

def parse_mmwave_frame(frame: bytes):
    """Simple parser to check if ANY target is valid and MOVING."""
    if len(frame) != FRAME_SIZE or frame[:4] != FRAME_HEADER:
        return False
    
    has_target = False
    # Check up to 3 targets
    for i in range(3):
        offset = 4 + i * 8
        x_raw, y_raw, spd_raw, _ = struct.unpack_from("<4H", frame, offset)
        
        # If coordinates are not 0, a target exists
        if x_raw != 0 or y_raw != 0:
            speed = decode_signed(spd_raw)
            # Only trigger if speed > 5 cm/s (filters out stationary noise)
            if abs(speed) >= 5:
                has_target = True
                print(f"[SENSOR] Moving target detected! Speed: {speed} cm/s")
                break
    return has_target

def read_mmwave_loop(ser, buf):
    """Reads serial until a full frame is found or buffer empty."""
    if ser.in_waiting:
        buf.extend(ser.read(ser.in_waiting))
    
    while len(buf) >= 4 and buf[:4] != FRAME_HEADER:
        buf.pop(0)
        
    if len(buf) >= FRAME_SIZE:
        frame = bytes(buf[:FRAME_SIZE])
        del buf[:FRAME_SIZE]
        return frame
    return None

# --- Main Application ---
def main():
    # 1. Setup mmWave
    try:
        ser = serial.Serial(MMWAVE_PORT, MMWAVE_BAUD, timeout=0.1)
        print(f"[Success] mmWave connected on {MMWAVE_PORT}")
    except Exception as e:
        print(f"[Error] mmWave init failed: {e}")
        return

    # 2. Setup Camera
    cap = cv2.VideoCapture(CAMERA_ID)
    if not cap.isOpened():
        print("[Error] Could not open video device.")
        return
    # Set lower resolution for speed/testing
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    print(f"[Success] Camera {CAMERA_ID} initialized.")

    # 3. Setup MobileNet (via OpenCV DNN)
    # Note: OpenCV DNN supports TFLite since 4.5+ (which Pi 5 usually has)
    net = None
    classes = []
    
    if os.path.exists(MODEL_PATH) and os.path.exists(LABEL_PATH):
        try:
            with open(LABEL_PATH, 'r') as f:
                classes = [line.strip() for line in f.readlines()]
            
            # Load TFLite model using OpenCV
            net = cv2.dnn.readNet(MODEL_PATH)
            print(f"[Success] Loaded MobileNet: {MODEL_PATH}")
        except Exception as e:
            print(f"[Warning] Failed to load model: {e}")
            print("Running in 'Capture Only' mode.")
    else:
        print("[Warning] Model files not found. Running in 'Capture Only' mode.")

    # 4. Setup Live Feed Window
    window_name = "Pi 5 - Live Vision & Sensor"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)

    buf = bytearray()
    motion_active = False

    # Create directory for saved images
    os.makedirs("captures", exist_ok=True)

    print("\nSystem Ready. LIVE FEED active. Press 'q' to quit.")

    try:
        while True:
            # --- A. Read Sensor ---
            # Process all pending serial data to get the latest status
            latest_mmwave_frame = None
            while True:
                f = read_mmwave_loop(ser, buf)
                if f:
                    latest_mmwave_frame = f
                else:
                    break
            
            # Update motion state if we got new data
            if latest_mmwave_frame:
                is_now_active = parse_mmwave_frame(latest_mmwave_frame)
            else:
                # Keep previous state if no new packet arrived this exact loop iteration
                # (or could set to False if you want strict timeout)
                is_now_active = motion_active

            # --- B. Capture Frame (Always) ---
            ret, img = cap.read()
            if not ret:
                print("Failed to grab camera frame")
                break

            display_img = img.copy()
            
            # --- C. Logic & Overlay ---
            if is_now_active:
                # Rising edge detection: If this is NEW motion, save the image
                if not motion_active:
                    filename = f"captures/trigger_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
                    cv2.imwrite(filename, img)
                    print(f"[EVENT] Motion Start -> Saved: {filename}")

                motion_active = True

                # Draw Red Status
                cv2.rectangle(display_img, (0, 0), (640, 50), (0, 0, 255), -1)
                cv2.putText(display_img, "MOTION DETECTED", (20, 35), 
                           cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)

                # Run Inference Live
                if net:
                    blob = cv2.dnn.blobFromImage(img, 1.0/127.5, (224, 224), (127.5, 127.5, 127.5), swapRB=True, crop=False)
                    net.setInput(blob)
                    preds = net.forward()
                    
                    idx = np.argmax(preds)
                    confidence = preds[0][idx]
                    label = classes[idx] if idx < len(classes) else f"ID {idx}"
                    
                    # Display Prediction
                    label_text = f"{label}: {confidence*100:.1f}%"
                    cv2.putText(display_img, label_text, (20, 450), 
                                cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
            else:
                motion_active = False
                # Draw Green Status
                cv2.rectangle(display_img, (0, 0), (640, 50), (0, 100, 0), -1)
                cv2.putText(display_img, "SCANNING AREA...", (20, 35), 
                           cv2.FONT_HERSHEY_SIMPLEX, 1, (200, 255, 200), 2)

            # --- D. Show Video ---
            cv2.imshow(window_name, display_img)
            
            # Exit on 'q'
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
            
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        ser.close()
        cap.release()
        cv2.destroyAllWindows()

if __name__ == "__main__":
    main()