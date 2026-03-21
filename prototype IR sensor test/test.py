import serial
import time

PORT      = "/dev/ttyAMA0"  # GPIO14 (Pin 8) TX → sensor RX, GPIO15 (Pin 10) RX ← sensor TX
BAUD_RATE = 256000          # HLK-LD2450 fixed baud rate

try:
    ser = serial.Serial(PORT, BAUD_RATE, timeout=1)
except serial.SerialException as e:
    print(f"[ERROR] Could not open {PORT}: {e}")
    print("        Try: sudo usermod -aG dialout $USER  (then log out and back in)")
    exit(1)

print(f"Listening to HLK-LD2450 on {PORT} at {BAUD_RATE} baud...")
print("Press Ctrl+C to stop.\n")

try:
    while True:
        if ser.in_waiting:
            print(ser.read(ser.in_waiting).hex(' '))
        time.sleep(0.05)
except KeyboardInterrupt:
    print("\nStopped.")
finally:
    ser.close()