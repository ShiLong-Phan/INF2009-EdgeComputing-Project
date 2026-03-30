"""
Bottle or Can Identifier
Uses webcam to capture an image and Gemini 2.5 Flash (free tier) to classify it.

Requirements:
    pip install opencv-python google-genai pillow psutil

Setup:
    Set your API key either:
      - As an environment variable: GEMINI_API_KEY=your_key_here
      - Or paste it directly into API_KEY below (not recommended for sharing)
"""

import cv2
import PIL.Image
import os
import sys
import time
import threading
import subprocess
import psutil
from google import genai
from google.genai import types

# ── CONFIG ────────────────────────────────────────────────────────────────────
API_KEY = os.getenv("GEMINI_API_KEY", "YOUR_API_KEY_HERE")  # <-- Set your API key here if not using env var
MODEL   = "gemini-2.5-flash"   # Free tier: 15 req/min, 1M tokens/day
MONITOR_INTERVAL = 0.25        # seconds between resource samples
# ────────────────────────────────────────────────────────────────────────────────


# ── POWER READING (Raspberry Pi 5 only) ─────────────────────────────────────────
def _read_rpi5_power_watts() -> float | None:
    """
    Reads total board power from the RPi 5 PMIC via vcgencmd.
    Returns watts as a float, or None if unavailable (non-RPi host).

    vcgencmd pmic_read_adc returns lines like:
        VDD_CORE_A current(7)=2.57184000A
        VDD_CORE_V volt(15)=0.87731290V
    Total power = sum of (V * A) for every rail that has both readings.
    """
    import re
    try:
        result = subprocess.run(
            ["vcgencmd", "pmic_read_adc"],
            capture_output=True, text=True, timeout=1
        )
        currents = {}  # rail_base -> amps
        voltages = {}  # rail_base -> volts
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            m = re.match(r'(\S+)\s+current\(\d+\)=([\d.]+)A', line)
            if m and m.group(1).endswith('_A'):
                currents[m.group(1)[:-2]] = float(m.group(2))
                continue
            m = re.match(r'(\S+)\s+volt\(\d+\)=([\d.]+)V', line)
            if m and m.group(1).endswith('_V'):
                voltages[m.group(1)[:-2]] = float(m.group(2))

        total = sum(currents[r] * voltages[r] for r in currents if r in voltages)
        if total > 0:
            return total
    except Exception:
        pass
    return None


# ── SYSTEM MONITOR ───────────────────────────────────────────────────────────────
class SystemMonitor:
    """
    Background thread that samples CPU %, RAM (MB), and board power (W)
    at a fixed interval. Call start() / stop(), then read .stats().
    """

    def __init__(self, interval: float = MONITOR_INTERVAL):
        self.interval  = interval
        self._proc     = psutil.Process()
        self._samples: list[tuple] = []   # (cpu_pct, ram_mb, power_w | None)
        self._running  = False
        self._thread: threading.Thread | None = None
        # Warm up cpu_percent so the first real reading is accurate
        psutil.cpu_percent(interval=None)

    def start(self) -> None:
        self._samples  = []
        self._running  = True
        self._thread   = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join()

    def _run(self) -> None:
        while self._running:
            cpu   = psutil.cpu_percent(interval=None)          # system-wide %
            ram   = psutil.virtual_memory().used / (1024 ** 2) # MB
            power = _read_rpi5_power_watts()
            self._samples.append((cpu, ram, power))
            time.sleep(self.interval)

    def stats(self) -> dict:
        """Return avg/peak for CPU, RAM, and power (if available)."""
        if not self._samples:
            return {}
        cpu_vals   = [s[0] for s in self._samples]
        ram_vals   = [s[1] for s in self._samples]
        pwr_vals   = [s[2] for s in self._samples if s[2] is not None]
        out = {
            "cpu_avg":  sum(cpu_vals) / len(cpu_vals),
            "cpu_peak": max(cpu_vals),
            "ram_avg":  sum(ram_vals) / len(ram_vals),
            "ram_peak": max(ram_vals),
        }
        if pwr_vals:
            out["pwr_avg"]  = sum(pwr_vals) / len(pwr_vals)
            out["pwr_peak"] = max(pwr_vals)
        return out


def capture_image() -> tuple:
    """
    Opens the default webcam and waits for the user to capture a frame.

    Controls:
        SPACE  → capture and continue
        Q      → quit without capturing

    Returns:
        (frame, t_trigger, t_captured) where frame is a BGR numpy array
        and the timestamps are perf_counter values. Returns (None, None, None)
        if the user quit.
    """
    cap = cv2.VideoCapture(0)

    if not cap.isOpened():
        print("[ERROR] Could not open webcam. Check that it is connected and not in use.")
        return None, None, None

    print("Webcam open — aim at a bottle or can.")
    print("  SPACE = capture  |  Q = quit\n")

    captured = None
    t_trigger = None
    t_captured = None
    while True:
        ret, frame = cap.read()
        if not ret:
            print("[ERROR] Failed to read frame from webcam.")
            break

        cv2.imshow("Bottle / Can Identifier  —  SPACE to capture | Q to quit", frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord(" "):
            t_trigger = time.perf_counter()
            captured = frame.copy()
            t_captured = time.perf_counter()
            print("Image captured!")
            break
        elif key in (ord("q"), ord("Q"), 27):   # Q or Esc
            print("Cancelled — no image captured.")
            t_trigger = t_captured = None
            break

    cap.release()
    cv2.destroyAllWindows()
    return captured, t_trigger, t_captured


def identify_item(frame) -> tuple:
    """
    Sends the captured frame to Gemini and asks it to classify
    the item as BOTTLE, CAN, or UNKNOWN.

    Args:
        frame: BGR numpy array from OpenCV.

    Returns:
        (result, api_elapsed) where result is one of "BOTTLE", "CAN",
        "UNKNOWN", or "ERROR", and api_elapsed is seconds taken by the API call.
    """
    if API_KEY == "YOUR_API_KEY_HERE":
        print("[ERROR] No API key set. Add your key to GEMINI_API_KEY env var or edit API_KEY in this file.")
        return "ERROR", 0.0

    client = genai.Client(api_key=API_KEY)

    # OpenCV uses BGR — convert to RGB for Pillow / Gemini
    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    pil_image = PIL.Image.fromarray(rgb_frame)

    prompt = (
        "Look at this image carefully.\n"
        "Is the main object in the image a BOTTLE or a CAN?\n\n"
        "Definitions:\n"
        "  BOTTLE — any bottle made of glass or plastic (water bottle, soda bottle, etc.)\n"
        "  CAN    — any metal/aluminium can (beverage can, tin can, etc.)\n\n"
        "Reply with ONLY one word: BOTTLE, CAN, or UNKNOWN.\n"
        "Do not include any other text, punctuation, or explanation."
    )

    try:
        t_api_start = time.perf_counter()
        response = client.models.generate_content(
            model=MODEL,
            contents=[prompt, pil_image],
        )
        api_elapsed = time.perf_counter() - t_api_start
        raw = response.text.strip().upper()
    except Exception as exc:
        print(f"[ERROR] Gemini API call failed: {exc}")
        return "ERROR", 0.0

    # Be forgiving — model might add punctuation or extra words
    if "BOTTLE" in raw:
        return "BOTTLE", api_elapsed
    if "CAN" in raw:
        return "CAN", api_elapsed
    return "UNKNOWN", api_elapsed


def main() -> str | None:
    t_total_start = time.perf_counter()

    print("=" * 48)
    print("   Bottle / Can Identifier")
    print(f"   Model : {MODEL}")
    print("=" * 48 + "\n")

    # ── IDLE PHASE: monitor while waiting for user to trigger capture
    idle_monitor = SystemMonitor()
    idle_monitor.start()

    frame, t_trigger, t_captured = capture_image()

    idle_monitor.stop()
    idle_stats = idle_monitor.stats()

    if frame is None:
        return None

    # ── ACTIVE PHASE: monitor while sending image and waiting for response
    active_monitor = SystemMonitor()
    active_monitor.start()

    print("Sending image to Gemini for analysis…")
    result, api_elapsed = identify_item(frame)

    active_monitor.stop()
    active_stats = active_monitor.stats()

    t_total_end = time.perf_counter()

    # ── RESULTS
    print("\n" + "=" * 48)
    print(f"  RESULT : {result}")
    print("=" * 48)
    if t_trigger is not None and t_captured is not None:
        print(f"  Trigger → capture  : {(t_captured - t_trigger) * 1000:.2f} ms")
    print(f"  Capture → response : {api_elapsed:.3f} s")

    # ── RESOURCE METRICS
    print()
    print(f"  {'Metric':<28} {'Idle':>10} {'Active':>10}")
    print(f"  {'-'*28} {'-'*10} {'-'*10}")
    print(f"  {'CPU avg (%)' :<28} {idle_stats.get('cpu_avg',  0)  :>9.1f}% {active_stats.get('cpu_avg',  0)  :>9.1f}%")
    print(f"  {'CPU peak (%)':<28} {idle_stats.get('cpu_peak', 0)  :>9.1f}% {active_stats.get('cpu_peak', 0)  :>9.1f}%")
    print(f"  {'RAM avg (MB)' :<28} {idle_stats.get('ram_avg',  0)  :>9.1f}  {active_stats.get('ram_avg',  0)  :>9.1f} ")
    print(f"  {'RAM peak (MB)':<28} {idle_stats.get('ram_peak', 0)  :>9.1f}  {active_stats.get('ram_peak', 0)  :>9.1f} ")

    # Power rows — only shown if running on RPi 5 (vcgencmd available)
    if "pwr_avg" in idle_stats or "pwr_avg" in active_stats:
        idle_pwr_avg   = idle_stats.get("pwr_avg",  0)
        idle_pwr_peak  = idle_stats.get("pwr_peak", 0)
        act_pwr_avg    = active_stats.get("pwr_avg",  0)
        act_pwr_peak   = active_stats.get("pwr_peak", 0)
        delta          = act_pwr_avg - idle_pwr_avg
        print(f"  {'Power avg (W)' :<28} {idle_pwr_avg  :>9.3f}W {act_pwr_avg  :>9.3f}W")
        print(f"  {'Power peak (W)':<28} {idle_pwr_peak :>9.3f}W {act_pwr_peak :>9.3f}W")
        print(f"  {'Delta power (Active-Idle)':<28} {'':>10} {delta:>+9.3f}W")
    else:
        print(f"  {'Power':<28} {'N/A (RPi 5 only)':>22}")

    print("=" * 48)

    # Save the captured image next to this script for reference
    save_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_capture.jpg")
    cv2.imwrite(save_path, frame)
    print(f"\nCapture saved → {save_path}")

    return result


if __name__ == "__main__":
    outcome = main()
    # Exit code: 0 = bottle, 1 = can, 2 = unknown/error  (useful for scripting)
    exit_codes = {"BOTTLE": 0, "CAN": 1, "UNKNOWN": 2, "ERROR": 2, None: 2}
    sys.exit(exit_codes.get(outcome, 2))
