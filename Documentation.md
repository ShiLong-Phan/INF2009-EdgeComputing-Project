# Edge Computing Project — Documentation

## 1. Project Goal

Build an edge-assisted recyclable classifier with two stages:
1) Edge device gives fast local prediction and immediate feedback.
2) Edge server performs mandatory cloud verification (NanoGPT/Qwen API) and stores final result.

The first release prioritises a stable baseline and data collection. Improvements are added only after baseline metrics are collected.

## 2. Hardware

- Logitech C310 webcam
- Powerbank (5V output)
- Speaker
- mmWave sensor (HLK-LD2450)
- Raspberry Pi edge device
- Laptop for MQTT broker, server, dashboard, and local DB

## 3. System Architecture

### Edge Device (Raspberry Pi)

1) Read mmWave sensor events.
2) On trigger, capture image and run frame differencing to isolate foreground object.
3) Run local TFLite classifier (waste_classifier_v1 — MobileNetV2 fine-tuned on drinking waste).
4) Play affirmative sound for recyclable classes (AluCan, PET).
5) Create event payload with metadata and image.
6) Publish payload to laptop via MQTTS.
7) If broker unreachable, save event in local SQLite outbox queue and retry with exponential backoff.

### Edge Server (Laptop)

1) Receive MQTT event metadata and image.
2) Validate schema and event_id (idempotent upsert).
3) Run cloud verification via NanoGPT/Qwen API.
4) Compare edge prediction vs cloud result.
5) Store event, predictions, and comparison in local SQLite DB.
6) Update CP7 Flask dashboard.

## 4. Deployment Modes

Edge device can be mounted in either mode:
- `inside_bin`: detect when trash is thrown in. Speed threshold default 65 cm/s.
- `outside_bin`: user hand-waves to trigger capture. Speed threshold default 70 cm/s.

Both modes use the same software pipeline (`edge_event_publisher_pi.py`), differing only in the speed gate. Use `--trigger-mode` and optionally `--min-speed-cm-s` to configure.

## 5. Data Contract

Each MQTT event envelope contains:

| Field | Description |
|---|---|
| `event_id` | UUID, used for server-side deduplication (idempotent upsert) |
| `device_id` | Edge device identifier (e.g. `pi-edge-01`) |
| `timestamp_utc` | ISO-8601 event time |
| `trigger_mode` | `inside_bin` or `outside_bin` |
| `edge_model_version` | Model identifier string (e.g. `waste-classifier-v1`) |
| `edge_pred_label` | Predicted class label |
| `edge_confidence` | Float 0–1 |
| `image_ref` | Optional filename/hash |
| `payload_version` | Schema version |

## 6. Security and Transport

- MQTTS (TLS 1.2+) on port 8883, mutual TLS required.
- All clients (laptop receiver, laptop dashboard, Pi publishers) present certificates signed by the local CA.
- CA private key (`certs/ca.key`) stays on laptop only — never copy to Pi.
- Payload-level encryption is out of scope.

## 7. Performance Targets

1) Edge reaction latency (trigger → local inference): target < 100 ms. Baseline measurement: mean 34 ms, p95 47 ms.
2) End-to-end verified latency (trigger → cloud-verified DB write): target < 5 s. Baseline measurement: mean 4537 ms, p95 8470 ms.
3) Bottleneck identified: network + NanoGPT API roundtrip (phone hotspot). Edge reaction itself is well under target.

---

## 8. Setup Guide

### 8.1 Naming Convention

Use these identities consistently to avoid TLS hostname mismatches:

| Role | Identity |
|---|---|
| Laptop hostname (broker) | `DOMCOM2` |
| Pi 1 device-id | `pi-edge-01` |
| Pi 2 device-id | `pi-edge-02` |
| Metadata MQTT topic | `edge/events/v1` |
| Image MQTT topic prefix | `edge/images/v1` |

### 8.2 Laptop Prerequisites

Install (Windows):
1. Python 3.10+
2. OpenSSL
3. Mosquitto broker

Ensure all three are on `PATH`:

```powershell
python --version
openssl version
mosquitto -h
```

### 8.3 Laptop Python Virtual Environment

```powershell
python -m venv .venv-laptop
.\.venv-laptop\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r cp2_cp6\requirements-laptop.txt
```

### 8.4 TLS Certificate Setup

Run from repo root. Replace `192.168.1.232` with your **current laptop IP** before generating `server.crt`.

#### CA and Broker Server Cert

```powershell
New-Item -ItemType Directory -Force certs | Out-Null
Set-Location certs

# CA — keep ca.key on laptop only, never copy to Pi
openssl genrsa -out ca.key 4096
openssl req -x509 -new -nodes -key ca.key -sha256 -days 3650 -out ca.crt -subj "/CN=cp1-local-ca"

# Broker server cert — SAN must include laptop hostname AND current IP
openssl genrsa -out server.key 2048
openssl req -new -key server.key -out server.csr -subj "/CN=DOMCOM2"
Set-Content -Path server.ext -Value "subjectAltName=DNS:DOMCOM2,IP:192.168.1.232`nextendedKeyUsage=serverAuth"
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out server.crt -days 825 -sha256 -extfile server.ext

Set-Location ..
```

> **Laptop IP changed?** Only regenerate `server.crt/server.key/server.csr/server.ext`. CA and all client certs are IP-independent.

#### Laptop Receiver Client Cert

```powershell
Set-Location certs
openssl genrsa -out laptop-client.key 2048
openssl req -new -key laptop-client.key -out laptop-client.csr -subj "/CN=laptop-receiver"
Set-Content -Path laptop-client.ext -Value "extendedKeyUsage=clientAuth"
openssl x509 -req -in laptop-client.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out laptop-client.crt -days 825 -sha256 -extfile laptop-client.ext
Set-Location ..
```

#### Pi Client Certs (one per Pi)

Pi 1:

```powershell
Set-Location certs
openssl genrsa -out pi-edge-01.key 2048
openssl req -new -key pi-edge-01.key -out pi-edge-01.csr -subj "/CN=pi-edge-01"
Set-Content -Path pi-edge-01.ext -Value "extendedKeyUsage=clientAuth"
openssl x509 -req -in pi-edge-01.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out pi-edge-01.crt -days 825 -sha256 -extfile pi-edge-01.ext
Set-Location ..
```

Pi 2 (same pattern, substitute `pi-edge-02`):

```powershell
Set-Location certs
openssl genrsa -out pi-edge-02.key 2048
openssl req -new -key pi-edge-02.key -out pi-edge-02.csr -subj "/CN=pi-edge-02"
Set-Content -Path pi-edge-02.ext -Value "extendedKeyUsage=clientAuth"
openssl x509 -req -in pi-edge-02.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out pi-edge-02.crt -days 825 -sha256 -extfile pi-edge-02.ext
Set-Location ..
```

### 8.5 Windows Firewall Rule (once, admin PowerShell)

```powershell
netsh advfirewall firewall add rule name="MQTTS 8883" dir=in action=allow protocol=TCP localport=8883
```

### 8.6 Mosquitto Config

`mosquitto_tls.conf` in repo root (already committed):

```conf
listener 8883
cafile certs/ca.crt
certfile certs/server.crt
keyfile certs/server.key

require_certificate true
use_identity_as_username true
allow_anonymous false
```

### 8.7 Raspberry Pi Setup (repeat for each Pi)

#### Install system packages

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip netcat-openbsd
```

#### Copy project files

Copy the full repo to the Pi. Then copy and rename cert files:

- **Pi 1**: copy `certs/pi-edge-01.crt` → `certs/pi-client.crt`, `certs/pi-edge-01.key` → `certs/pi-client.key`, plus `certs/ca.crt`.
- **Pi 2**: same but with `pi-edge-02.*` files.

Also copy the model and labels:
- `waste_classifier/waste_classifier_v1.tflite`
- `waste_classifier/labels.txt`

```bash
chmod 600 certs/pi-client.key
```

#### Resolve laptop hostname

Add current laptop IP to `/etc/hosts` on the Pi. **Update this whenever the network changes (new hotspot/router):**

```bash
echo "192.168.1.232 DOMCOM2" | sudo tee -a /etc/hosts
getent hosts DOMCOM2
nc -vz DOMCOM2 8883
```

#### Python virtual environment

```bash
python3 -m venv .venv-pi
source .venv-pi/bin/activate
pip install --upgrade pip
pip install -r cp2_cp6/requirements-pi.txt
```

> `requirements-pi.txt` includes `ai-edge-litert` for TFLite inference (Python 3.13 compatible).

---

## 9. Running the System

See `Essential startup cmds.txt` for copy-paste ready one-liners.

### 9.1 Laptop — Start All Services

**Terminal 1 — MQTT Broker:**

```powershell
mosquitto -c mosquitto_tls.conf -v
```

**Set API key before starting receiver (CMD):**

```cmd
set NANOGPT_API_KEY=your_actual_key_here
```

**Terminal 2 — Event Receiver:**

```powershell
.\.venv-laptop\Scripts\Activate.ps1
python cp2_cp6\server_event_receiver_laptop.py --broker-host DOMCOM2 --broker-port 8883 --topic edge/events/v1 --image-topic-prefix edge/images/v1 --ca-cert .\certs\ca.crt --client-cert .\certs\laptop-client.crt --client-key .\certs\laptop-client.key --db-path .\data\edge_events.db --image-store-dir .\data\images --nanogpt-model qwen3.5-27b
```

**Terminal 3 — CP7 Dashboard:**

```powershell
.\.venv-laptop\Scripts\Activate.ps1
python cp2_cp6\dashboard_cp7.py --db-path .\data\edge_events.db --host 0.0.0.0 --port 5050
```

Open: `http://localhost:5050/`

### 9.2 Pi — Start Publisher

```bash
source .venv-pi/bin/activate
python3 cp2_cp6/edge_event_publisher_pi.py \
  --broker-host DOMCOM2 \
  --broker-port 8883 \
  --topic edge/events/v1 \
  --image-topic-prefix edge/images/v1 \
  --device-id pi-edge-01 \
  --trigger-mode inside_bin \
  --ca-cert certs/ca.crt \
  --client-cert certs/pi-client.crt \
  --client-key certs/pi-client.key \
  --model-path waste_classifier/waste_classifier_v1.tflite \
  --label-path waste_classifier/labels.txt \
  --edge-model-version waste-classifier-v1 \
  --capture-dir captures \
  --sound-file sounds/beep.wav \
  --min-speed-cm-s 65 \
  --outbox-db-path data/pi_outbox.db \
  --retry-base-sec 2 \
  --max-retry-backoff-sec 60 \
  --max-image-bytes 400000 \
  --recyclable-keywords AluCan,PET \
  --bg-threshold 30 \
  --bg-min-area-px 1500 \
  --bg-crop-pad-px 10 \
  --bg-blur-kernel 21 \
  --min-confidence 0.8
```

For Pi 2: change `--device-id pi-edge-02`.

---

## 10. Troubleshooting

### Pi cannot resolve laptop hostname

**Symptom:** `socket.gaierror: [Errno -2] Name or service not known`

**Fix:** Add mapping to `/etc/hosts` on Pi:
```bash
echo "192.168.1.232 DOMCOM2" | sudo tee -a /etc/hosts
```
Update the IP every time you change networks.

### TLS fails with IP address mismatch

**Symptom:** `ssl.SSLCertVerificationError: certificate verify failed: IP address mismatch`

**Fix:** Always connect using hostname (`DOMCOM2`), not the raw IP. The server certificate identity is hostname-based. If you must include an IP, regenerate `server.crt` with both DNS and IP in the SAN.

### Mosquitto reports "bad certificate"

**Symptom:** Mosquitto log shows `ssl/tls alert bad certificate` / `protocol error`

**Diagnosis (run on Pi):**

```bash
openssl x509 -in certs/pi-client.crt -noout -subject -issuer -dates
openssl verify -CAfile certs/ca.crt certs/pi-client.crt
# Cert and key modulus must match:
openssl x509 -in certs/pi-client.crt -noout -modulus | openssl md5
openssl rsa  -in certs/pi-client.key  -noout -modulus | openssl md5
# Compare CA fingerprint on laptop vs Pi:
openssl x509 -in certs/ca.crt -noout -fingerprint -sha256
```

Common causes: stale cert copied to Pi, cert/key mismatch, cert signed by wrong CA.

### Duplicate events

Server performs idempotent upsert keyed by `event_id`. Duplicate publishes result in one logical DB row with `receive_count` incremented. This is expected and by design.

---

## 11. Checkpoint Plan

### CP0 — Environment and Reproducibility (DONE)

Scope: pin Python versions and dependencies, split requirements for edge and server, verify camera + UART sensor setup.

### CP1 — Basic MQTTS Link (Pi → Laptop) (DONE)

Scope: Mosquitto broker on laptop, TLS certificates, publish test payload from Pi.

### CP2 — Event Schema and Reliability (DONE)

Scope: JSON event envelope with `event_id` + `timestamp_utc`, QoS 1 publish, server dedup logic (idempotent SQLite upsert keyed by `event_id`).

### CP3 — Sensor Trigger Pipeline (DONE)

Scope: mmWave serial frame parsing (HLK-LD2450), trigger profiles (`inside_bin` / `outside_bin`), speed gate tuning, debounce guard.

CLI knobs: `--min-speed-cm-s`, `--max-distance-cm`, `--debounce-sec`.

### CP4 — Capture + Local Inference Baseline (DONE)

Scope: capture image on trigger, run local TFLite model, attach prediction to MQTT payload, play affirmative sound for recyclable labels.

Notes:
- If model or labels are missing, script runs in capture-only mode (`label=unknown`, `confidence=0.0`), preserving the CP2 data contract.
- Sound playback via `aplay` — requires `alsa-utils` on Pi.

### CP5 — Offline Outbox Queue (DONE)

Scope: SQLite FIFO outbox on Pi, publish both event metadata and image (QoS 1), retry with exponential backoff.

Retry formula: `delay = retry_base_sec × 2^(retry_count−1)`, capped by `--max-retry-backoff-sec`.

### CP6 — Cloud Verification (DONE)

Scope: laptop subscribes to metadata + image topics, stores image files, calls NanoGPT/Qwen API, stores `verify_status`, `verify_label`, `verify_confidence`, `verify_error`.

Current model: `qwen3.5-27b` via `--nanogpt-model`.

### CP7 — Dashboard MVP (DONE)

Scope: Flask + Jinja2 dashboard (`dashboard_cp7.py`).

Features:
- Landing page: global KPIs, latest events, device leaderboard.
- Per-device drilldown: scanned material mix, agreement rate, latest events table.
- Edge vs Cloud column: MATCH / MISMATCH / N/A.
- Device online/offline status (configurable window via `--online-window-sec`).
- Ping button: sends MQTT ping to Pi, shows round-trip latency.
- Reset Background button: sends MQTT command to Pi to capture a fresh background frame.

Label normalisation (`_normalize_label` in `dashboard_cp7.py`):
- `AluCan` → `CAN`
- `PET` → `BOTTLE`
- `Glass` → `UNKNOWN`
- `HDPEM` → `UNKNOWN`
- Everything else → `UNKNOWN`

### CP7.5 — PASO + Optimisations (DONE — between CP7 and CP8)

PASO = Profile, Analyse, Schedule, Optimise.

#### Profiling and baseline capture

System and event metrics collected over 300-second window using `mobilenetv2-baseline`:

- Edge reaction latency: mean 34 ms, median 33 ms, p95 47 ms.
- Broker ingest latency: mean 1070 ms, median 406 ms, p95 3984 ms.
- Verification latency: mean 4537 ms, p95 8470 ms.
- Pi process RSS: ~139 MB. CPU: mean 3.5%, p95 10%.
- **Bottleneck:** network + NanoGPT API (phone hotspot). Edge reaction was already under 50 ms.

Full data: `data/paso/baseline_report.md` and `baseline_report.json`.

#### Optimisation 1 — Static background frame differencing (Variant A)

Motivation: raw trigger frames include the bin interior/wall/hands, degrading accuracy on the custom waste model. Frame differencing passes only the foreground object to the classifier.

How it works (`edge_event_publisher_pi.py`):
1. On startup, the publisher captures one reference background frame and saves it to `captures/background.jpg`. If a saved background already exists it is loaded instead.
2. On each trigger, the raw frame is saved as `trigger_<ts>.jpg`.
3. Frame differencing pipeline:
   a. Both frames are converted to grayscale and Gaussian-blurred (kernel default 21) to suppress auto-exposure noise.
   b. `cv2.absdiff` → binary threshold → morphological close → dilate.
   c. Largest contour found. If its area exceeds `bg-min-area-px` (default 1500), its bounding rect is cropped with padding from the colour frame, saved as `processed_<ts>.jpg`, and fed to the classifier.
   d. If no significant contour is found, inference is skipped entirely — event logged as `unknown/0.0`, no beep. This guards against spurious sensor triggers.
4. The cropped image (not the raw frame) is published to the cloud.
5. PASO log gains a `fg_area_px` column.

New CLI flags:
- `--bg-threshold` (default 30): binary diff threshold.
- `--bg-min-area-px` (default 1500): minimum contour area to accept as foreground.
- `--bg-crop-pad-px` (default 10): padding around crop bounding rect.
- `--bg-blur-kernel` (default 21): Gaussian kernel size (0 to disable).
- `--min-confidence` (default 0.8): confidence gate — if top class score < threshold, label is overridden to `unknown` and no beep fires.

Dashboard gains `POST /api/reset-bg/<device_id>` which publishes an MQTT command to `edge/bg-reset/request/<device_id>`. The Pi publisher subscribes to this topic and re-captures the background on the next available frame.

#### Optimisation 2 — Custom-trained waste classifier model

Motivation: the original `mobilenet_v2_1.0_224.tflite` (ImageNet classes) was poor at distinguishing cans. A MobileNetV2 was fine-tuned on the Kaggle "Drinking Waste Classification" dataset (arkadiyhacks) via `Edge_Model_Refinement.ipynb`.

Model details:
- Base: MobileNetV2 1.00/224, ImageNet weights, backbone frozen.
- Head: GlobalAveragePooling2D → Dropout(0.3) → Dense(4, softmax).
- 4 classes: AluCan, Glass, HDPEM, PET.
- Training: 20 epochs, data augmentation (flip, rotation, brightness), ReduceLROnPlateau.
- Saved as `waste_classifier/waste_classifier_v1.keras` (9.2 MB).

Conversion: `TFLiteConverter.from_keras_model()` with default optimisation → `waste_classifier/waste_classifier_v1.tflite` (2.42 MB). Labels: `waste_classifier/labels.txt` (alphabetical, one per line).

Inference runtime: switched from `cv2.dnn.readNet()` (incompatible with the data augmentation layer baked into the model) to the TFLite interpreter. Import priority: `ai_edge_litert` → `tflite_runtime` → `tensorflow.lite`. Preprocessing: resize to 224×224 → BGR→RGB → scale to [−1, 1] → expand batch dimension.

Label mapping (both `_normalize_label` in `dashboard_cp7.py` and `normalize_label` in `paso_analyze_run.py`):
- `AluCan` → `CAN`
- `PET` → `BOTTLE`
- `Glass` → `UNKNOWN` (not a targeted recyclable in this deployment)
- `HDPEM` → `UNKNOWN` (same rationale)

Only `AluCan` and `PET` trigger the affirmative beep (`--recyclable-keywords AluCan,PET`).

#### Post-optimisation measurement

After-run commands are in `PasoPlan.md` section 3. Results compared against baseline via `paso_compare_runs.py`. After-run data: `data/paso/after_report.md` and `after_report.json`. Comparison: `data/paso/comparison.md`.

### CP8 — ML/AI Features (Planned)

Scope: analytics page and dataset export flow answering "What are users trying to recycle, and where are they uncertain?"

Core analytics deliverables:
1. Global material spread: count and % by normalised class (BOTTLE, CAN, UNKNOWN).
2. Per-device profile: top labels, unknown-rate per device.
3. Verification quality: agreement rate by device, mismatch distribution.
4. Time-window insights: daily/hourly scan volume trends, daily unknown-rate trend.
5. Campaign recommendation view (rule-based MVP): flag devices where unknown-rate exceeds threshold.

Planned dashboard pages: Data (global), Device analytics, Campaign insights.

Post-MVP ML progression:
1. Baseline forecasting with simple time-series/linear trend on daily counts.
2. Confidence calibration of edge model using cloud-confirmed labels.
3. Drift watch: detect sudden class distribution changes by device.

Exit criteria:
1. Dashboard includes a dedicated "Data" analytics page.
2. Export CSV endpoint exists.
3. At least one actionable campaign recommendation generated from real data.

### CP9 — Edge Image Retention Controls (Optional)

Scope: dashboard command → MQTT control topic → Pi deletes old images by policy, acknowledges.

Exit criteria: operator can trigger image cleanup from dashboard and see success/failure response.

## 12. Improvements Backlog

1. Better dataset collection + retraining loop.
2. Rich device health telemetry (disk usage, last heartbeat, packet rate).
3. Cloud DB migration after local DB baseline stabilises.
4. Human review workflow for selected low-confidence events.
5. Drift detection: alert when class distribution shifts significantly.

## 13. Non-Goals for Initial Release

1. Any form of payload decryption or key rotation.
2. Multi-broker federation.
3. Over-the-air model update.
