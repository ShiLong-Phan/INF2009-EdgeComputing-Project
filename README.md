# INF2009-EdgeComputing-Project

Edge-assisted recyclable classifier using a Raspberry Pi, an mmWave motion sensor, and a laptop acting as MQTT broker, cloud verifier, and dashboard.

The Pi detects motion via the HLK-LD2450 mmWave sensor, captures an image, runs a local TFLite waste classifier, and publishes the result over MQTTS. The laptop receives events, runs cloud verification via Qwen-27b , and serves a Flask dashboard with per-device analytics.

See [Documentation.md](Documentation.md) for full architecture, setup, and troubleshooting.  
See [PasoPlan.md](PasoPlan.md) for PASO profiling commands and measurement results.

---

## Requirements

### Computer (Windows)

- Python 3.10+
- [Mosquitto MQTT broker](https://mosquitto.org/download/)
- OpenSSL (available on PATH)

```powershell
python -m venv .venv-laptop
.\.venv-laptop\Scripts\Activate.ps1
pip install -r cp2_cp6\requirements-laptop.txt
```

Key packages: `paho-mqtt`, `Flask`, `psutil`, `google-genai`, `Pillow`, `requests`

### Raspberry Pi

- Python 3.11+ (tested on 3.13)
- `sudo apt install -y python3-venv python3-pip netcat-openbsd alsa-utils`

```bash
python3 -m venv .venv-pi
source .venv-pi/bin/activate
pip install -r cp2_cp6/requirements-pi.txt
pip install ai-edge-litert   # TFLite runtime for Python 3.13
```

Key packages: `paho-mqtt`, `pyserial`, `opencv-python`, `numpy`, `psutil`, `ai-edge-litert`

---

## Quick Start

1. Generate TLS certificates (see [Documentation.md §8.4](Documentation.md)).
2. Start Mosquitto broker: `mosquitto -c mosquitto_tls.conf -v`
3. Set API key: `set NANOGPT_API_KEY=your_key_here`. Skippable, but agreement rate will tank as cloud API is not called.
4. Start event receiver and Flask dashboard on laptop.
5. Start Pi publisher.

See `Essential startup cmds.txt` for ready-to-run commands.

---

## Repository Structure

```
cp1_mqtt/               Basic MQTTS test scripts (CP1)
cp2_cp6/                Main application code
  edge_event_publisher_pi.py    Pi publisher (mmWave trigger, inference, MQTT)
  server_event_receiver_laptop.py   Laptop receiver (cloud verify, SQLite)
  dashboard_cp7.py              Flask dashboard
  paso_analyze_run.py           PASO per-run analysis
  paso_compare_runs.py          PASO baseline vs after comparison
  paso_system_profile.py        CPU/RAM profiler
certs/                  TLS certificates (not committed — generate locally)
data/
  edge_events.db        SQLite event store (laptop)
  images/               Received event images
  paso/                 PASO CSV logs and reports
waste_classifier/
  waste_classifier_v1.tflite    Fine-tuned MobileNetV2 (4 classes, 2.42 MB)
  labels.txt                    Class labels (AluCan, Glass, HDPEM, PET)
sounds/                 Beep audio file for Pi
```

---

## Model

`waste_classifier_v1.tflite` — MobileNetV2 fine-tuned on the [Drinking Waste Classification](https://www.kaggle.com/datasets/arkadiyhacks/drinking-waste-classification) dataset (Kaggle).

- 4 classes: AluCan, Glass, HDPEM, PET
- Target recyclables (trigger beep): **AluCan**, **PET**
- Input: 224×224 RGB, scaled to [−1, 1]
- Training notebook: `Edge_Model_Refinement.ipynb`
