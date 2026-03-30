"""
HLK-LD2450 Motion Detector
--------------------------
Reads frames from the sensor and prints human-readable output:
  - Whether motion / a target is detected
  - X position (left/right of sensor centre, in cm)
  - Y distance (how far in front of the sensor, in cm)
  - Speed (cm/s, positive = moving away, negative = approaching)

Wiring (Pi 5):
  GPIO14 / Pin 8  → sensor RX
  GPIO15 / Pin 10 ← sensor TX   (/dev/ttyAMA0)
"""

import serial
import struct
import time
import os

PORT      = "/dev/ttyAMA0"
BAUD_RATE = 256000

FRAME_HEADER = bytes([0xAA, 0xFF, 0x03, 0x00])
FRAME_TAIL   = bytes([0x55, 0xCC])
FRAME_SIZE   = 30
MAX_TARGETS  = 3


def decode_signed(raw: int) -> int:
    """
    The LD2450 uses a custom sign encoding:
    bit-15 = sign flag (1 = negative), bits 14-0 = magnitude.
    A value of 0x0000 means 'no target'.
    """
    if raw == 0:
        return None          # empty target slot
    magnitude = raw & 0x7FFF
    return -magnitude if (raw & 0x8000) else magnitude


def parse_frame(frame: bytes) -> list[dict] | None:
    """Return a list of active target dicts, or None if the frame is invalid."""
    if len(frame) != FRAME_SIZE:
        return None
    if frame[:4] != FRAME_HEADER or frame[-2:] != FRAME_TAIL:
        return None

    targets = []
    for i in range(MAX_TARGETS):
        offset = 4 + i * 8
        x_raw, y_raw, spd_raw, res_raw = struct.unpack_from("<4H", frame, offset)

        x   = decode_signed(x_raw)
        y   = decode_signed(y_raw)
        spd = decode_signed(spd_raw)

        # Skip empty slots (all zeros)
        if x is None and y is None and spd is None:
            continue

        targets.append({
            "x_cm":   round(x / 10, 1) if x is not None else 0,
            "y_cm":   round(y / 10, 1) if y is not None else 0,
            "speed":  spd if spd is not None else 0,
            "res_mm": res_raw,
        })

    return targets


def read_frame(ser: serial.Serial, buf: bytearray) -> bytes | None:
    """Buffer incoming bytes and return a complete frame when one is ready."""
    if ser.in_waiting:
        buf.extend(ser.read(ser.in_waiting))

    # Discard bytes before the frame header
    while len(buf) >= 4 and buf[:4] != FRAME_HEADER:
        buf.pop(0)

    if len(buf) >= FRAME_SIZE:
        frame = bytes(buf[:FRAME_SIZE])
        del buf[:FRAME_SIZE]
        return frame
    return None


def direction_label(x_cm: float) -> str:
    if abs(x_cm) < 15:
        return "centre"
    return "left" if x_cm < 0 else "right"


def speed_label(speed: int) -> str:
    if abs(speed) < 5:
        return "stationary"
    return f"{'approaching' if speed > 0 else 'moving away'} at {abs(speed)} cm/s"


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    if not os.path.exists(PORT):
        print(f"[ERROR] {PORT} not found. Is UART enabled?")
        return

    try:
        ser = serial.Serial(PORT, BAUD_RATE, timeout=1)
    except serial.SerialException as e:
        print(f"[ERROR] Could not open {PORT}: {e}")
        return

    print("=" * 50)
    print("  HLK-LD2450 Motion Detector")
    print("  Press Ctrl+C to stop")
    print("=" * 50)

    buf          = bytearray()
    last_status  = None   # track changes so we don't spam identical lines

    try:
        while True:
            frame = read_frame(ser, buf)
            if frame is None:
                time.sleep(0.02)
                continue

            targets = parse_frame(frame)
            if targets is None:
                continue

            if targets:
                lines = []
                for idx, t in enumerate(targets, start=1):
                    side  = direction_label(t["x_cm"])
                    spd   = speed_label(t["speed"])
                    dist  = abs(t["y_cm"])
                    lines.append(
                        f"  Target {idx}: {dist:.0f} cm away, "
                        f"{side} of centre, {spd}"
                    )
                status = "\n".join(lines)
                header = f"[MOTION DETECTED – {len(targets)} target(s)]"
            else:
                status = "  No targets in range."
                header = "[NO MOTION]"

            # Only print when something changes
            full = f"{header}\n{status}"
            if full != last_status:
                print(f"\n{full}")
                last_status = full

    except KeyboardInterrupt:
        print("\n\nStopped.")
    finally:
        ser.close()


if __name__ == "__main__":
    main()
