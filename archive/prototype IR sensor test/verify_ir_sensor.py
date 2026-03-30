"""
HLK-LD2450 mmWave Sensor Connection Verification
-------------------------------------------------
Raspberry Pi wiring (bit-bang UART via pigpio):
  - GPIO17 (Pin 11) ← LD2450 TX  (Pi RX)
  - GPIO27 (Pin 13) → LD2450 RX  (Pi TX)
  - 5 V power + GND as required by the module

Prerequisite – pigpio daemon must be running:
    sudo pigpiod

UART settings (fixed by the sensor firmware):
  Baud: 256000 | 8N1
  Note: pigpio bit-bang serial is rated to ~250 kbaud; 256 kbaud is
  at the limit — keep wiring short and connections solid.

Binary frame format (30 bytes per frame):
  Header : AA FF 03 00                     (4 bytes)
  Data   : 3 × 8 bytes, one per target slot
             Bytes 0-1 : X coordinate (mm, signed – see note)
             Bytes 2-3 : Y coordinate (mm, signed – see note)
             Bytes 4-5 : Speed       (cm/s, signed – see note)
             Bytes 6-7 : Distance resolution (mm, unsigned)
  Tail   : 55 CC                           (2 bytes)

Sign encoding: bit-15 of the 16-bit word is the sign flag
  (0 = positive / right / moving away,  1 = negative / left / approaching).
  The remaining 15 bits hold the magnitude.
  A word of 0x0000 means "no target in this slot".
"""

import sys
import time
import struct

# ── dependency check ──────────────────────────────────────────────────────────
try:
    import pigpio
except ImportError:
    print("[ERROR] 'pigpio' is not installed.")
    print("        Install it with:  pip install pigpio")
    sys.exit(1)


# ── configuration ─────────────────────────────────────────────────────────────
GPIO_RX       = 17       # Pi GPIO17 (Pin 11) receives from sensor TX
GPIO_TX       = 27       # Pi GPIO27 (Pin 13) transmits to sensor RX
BAUD_RATE     = 256000   # fixed by LD2450 firmware
READ_DURATION = 10.0     # seconds to listen for frames

FRAME_HEADER  = bytes([0xAA, 0xFF, 0x03, 0x00])
FRAME_TAIL    = bytes([0x55, 0xCC])
FRAME_SIZE    = 30               # header(4) + 3×target(8) + tail(2)
MAX_TARGETS   = 3


# ── helpers ───────────────────────────────────────────────────────────────────
def check_pigpiod() -> bool:
    """Return True if the pigpio daemon is reachable, False otherwise."""
    try:
        pi = pigpio.pi()
        ok = pi.connected
        pi.stop()
        return ok
    except Exception:
        return False


def decode_signed(raw: int) -> int:
    """
    Convert an LD2450 signed 16-bit word to a Python int.
    Bit-15 is the sign flag; bits 14-0 are the magnitude.
    """
    if raw == 0:
        return 0
    magnitude = raw & 0x7FFF
    return -magnitude if (raw & 0x8000) else magnitude


def parse_frame(frame: bytes) -> list[dict] | None:
    """
    Parse a 30-byte LD2450 frame.
    Returns a list of up to 3 target dicts, or None if the frame is invalid.
    """
    if len(frame) != FRAME_SIZE:
        return None
    if frame[:4] != FRAME_HEADER or frame[-2:] != FRAME_TAIL:
        return None

    targets = []
    for i in range(MAX_TARGETS):
        offset = 4 + i * 8
        x_raw, y_raw, spd_raw, res_raw = struct.unpack_from("<4H", frame, offset)
        # Skip empty target slots
        if x_raw == 0 and y_raw == 0 and spd_raw == 0:
            continue
        targets.append({
            "x_mm":    decode_signed(x_raw),
            "y_mm":    decode_signed(y_raw),
            "speed":   decode_signed(spd_raw),
            "res_mm":  res_raw,
        })
    return targets


def read_frame(pi: pigpio.pi, buf: bytearray) -> bytes | None:
    """
    Feed incoming bytes into buf, find a complete frame, and return it.
    Discards leading bytes that don't start with the frame header.
    """
    count, data = pi.bb_serial_read(GPIO_RX)
    if count > 0:
        buf.extend(data[:count])

    # Discard bytes until we see the header
    while len(buf) >= 4 and buf[:4] != FRAME_HEADER:
        buf.pop(0)

    if len(buf) >= FRAME_SIZE:
        frame = bytes(buf[:FRAME_SIZE])
        del buf[:FRAME_SIZE]
        return frame
    return None


# ── main verification logic ───────────────────────────────────────────────────
def verify_connection(rx_gpio: int, tx_gpio: int, baud: int, duration: float) -> None:
    """Open bit-bang serial on GPIO pins and attempt to decode LD2450 data frames."""

    print(f"\n{'='*58}")
    print("  HLK-LD2450 mmWave Sensor – Connection Verification")
    print(f"{'='*58}")
    print(f"  RX GPIO       : GPIO{rx_gpio} (Pin 11) ← sensor TX")
    print(f"  TX GPIO       : GPIO{tx_gpio} (Pin 13) → sensor RX")
    print(f"  Baud rate     : {baud}")
    print(f"  Listen for    : {duration}s")
    print(f"{'='*58}\n")

    # 1. Check pigpio daemon
    if not check_pigpiod():
        print("[FAIL] Cannot connect to pigpio daemon.")
        print("\n[HINT] Start the daemon with:")
        print("       sudo pigpiod")
        sys.exit(1)

    print("[OK]   pigpio daemon is reachable.")

    # 2. Open pigpio and configure bit-bang serial
    try:
        pi = pigpio.pi()
        pi.bb_serial_read_open(rx_gpio, baud, 8)
        pi.set_mode(tx_gpio, pigpio.OUTPUT)
        pi.write(tx_gpio, 1)   # idle-high for UART TX line
    except Exception as exc:
        print(f"[FAIL] Could not configure GPIO: {exc}")
        print("\n[HINT] Try running with sudo.")
        sys.exit(1)

    print(f"[OK]   Bit-bang serial opened on GPIO{rx_gpio} (RX) / GPIO{tx_gpio} (TX).")
    print(f"\n[INFO] Listening for LD2450 frames for {duration}s ...")
    print("       Walk in front of the sensor to generate target data.\n")

    # 3. Read and decode frames
    start        = time.monotonic()
    buf          = bytearray()
    frames_recv  = 0
    frames_valid = 0

    try:
        while time.monotonic() - start < duration:
            frame = read_frame(pi, buf)
            if frame is None:
                time.sleep(0.01)
                continue

            frames_recv += 1

            targets = parse_frame(frame)
            if targets is None:
                print(f"  [WARN] Bad frame #{frames_recv}: {frame.hex(' ')}")
                continue

            frames_valid += 1
            elapsed = time.monotonic() - start

            if targets:
                for idx, t in enumerate(targets, start=1):
                    print(
                        f"  [FRAME {frames_valid:>4}  {elapsed:5.1f}s]  "
                        f"Target {idx}: "
                        f"X={t['x_mm']:+5d} mm  "
                        f"Y={t['y_mm']:+5d} mm  "
                        f"Speed={t['speed']:+4d} cm/s  "
                        f"Res={t['res_mm']} mm"
                    )
            else:
                # Valid frame but no targets detected — still proves comms work
                print(f"  [FRAME {frames_valid:>4}  {elapsed:5.1f}s]  (no targets in range)")

    except KeyboardInterrupt:
        print("\n[INFO] Interrupted by user.")
    finally:
        pi.bb_serial_read_close(rx_gpio)
        pi.stop()

    # 4. Summary
    print(f"\n{'='*58}")
    if frames_valid > 0:
        print(f"[PASS] LD2450 is connected and communicating!")
        print(f"       Received {frames_valid} valid frame(s) out of {frames_recv} total.")
    elif frames_recv > 0:
        print(f"[WARN] Received {frames_recv} frame(s) but none passed validation.")
        print("       Raw bytes arrived – likely a framing / baud-rate mismatch.")
        print(f"\n[HINT] Double-check baud rate is {baud} and wiring TX↔RX is not swapped.")
    else:
        print("[WARN] No data received during the listening window.")
        print("       The GPIO was opened but no bytes arrived from the sensor.")
        print("\n[HINT] Possible causes:")
        print(f"       • Wiring issue — confirm GPIO{rx_gpio} (Pi RX) ← LD2450 TX")
        print(f"                                  GPIO{tx_gpio} (Pi TX) → LD2450 RX")
        print("       • Sensor not powered — verify 5 V supply and GND")
        print("       • pigpio bit-bang at 256 kbaud — tolerance is tight; check connections")
    print(f"{'='*58}\n")


# ── entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    verify_connection(
        rx_gpio=GPIO_RX,
        tx_gpio=GPIO_TX,
        baud=BAUD_RATE,
        duration=READ_DURATION,
    )
