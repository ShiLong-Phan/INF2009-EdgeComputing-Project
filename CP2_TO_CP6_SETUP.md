# CP2 to CP6 Setup and Runbook

This document covers the implementation that was added in this repository for:
- CP2 Event schema and reliability baseline (QoS1 + server dedup)
- CP3 Sensor trigger pipeline
- CP4 Capture + local inference baseline
- CP5 Offline FIFO outbox queue on Pi
- CP6 Laptop-side Gemini verification

## Added Files

- cp2_cp6/event_schema.py
- cp2_cp6/edge_event_publisher_pi.py
- cp2_cp6/pi_outbox.py
- cp2_cp6/server_event_receiver_laptop.py
- cp2_cp6/gemini_verifier.py
- cp2_cp6/requirements-pi.txt
- cp2_cp6/requirements-laptop.txt

## CP2 Coverage

Implemented:
1. Standard JSON event envelope with required fields:
   - event_id
   - device_id
   - timestamp_utc
   - trigger_mode
   - edge_model_version
   - edge_pred_label
   - edge_confidence
   - image_ref
   - payload_version
2. MQTT publish uses QoS1.
3. Laptop receiver validates schema before persisting.
4. SQLite idempotent upsert keyed by event_id.
5. Duplicate tracking via receive_count and last_seen_utc.

How to prove dedup quickly:
1. Start laptop receiver.
2. Run Pi edge publisher with --publish-duplicate.
3. Verify receiver logs show DUPLICATE and DB keeps one row with receive_count > 1.

## CP3 Coverage

Implemented in edge_event_publisher_pi.py:
1. mmWave serial frame parsing (HLK-LD2450 format).
2. Trigger profiles:
   - inside_bin
   - outside_bin
3. Fast-wave speed gates tuned to reduce random triggers:
   - inside_bin default minimum absolute speed: 65 cm/s
   - outside_bin default minimum absolute speed: 70 cm/s
4. Optional CLI tuning:
   - --min-speed-cm-s to override speed threshold
   - --max-distance-cm to add a distance limit when needed
5. Debounce guard via --debounce-sec.

## CP4 Coverage

Implemented in edge_event_publisher_pi.py:
1. Capture image on valid trigger.
2. Optional local inference with OpenCV DNN model.
3. Prediction fields attached to MQTT payload.
4. Affirmative sound playback for recyclable labels:
   - configurable keywords via --recyclable-keywords
   - sound file via --sound-file (uses aplay)

Notes:
1. If model or labels are missing, script runs in capture-only prediction mode (label=unknown, confidence=0.0).
2. This still preserves CP2 data contract and pipeline behavior.

## CP5 Coverage

Implemented in edge_event_publisher_pi.py + pi_outbox.py:
1. Pi outbox uses SQLite with FIFO ordering by autoincrement id.
2. Each queue row contains:
   - event payload JSON
   - image file path
   - event/image publish status
   - retry count and next retry timestamp
3. Publish flow is exactly-once-ish at application level:
   - publish event metadata (QoS1)
   - publish image bytes (QoS1) to image topic
   - remove row only after both publish operations succeed
4. Retries use exponential backoff:
   - delay = retry_base_sec * 2^(retry_count-1), capped by max_retry_backoff_sec

## CP6 Coverage

Implemented in server_event_receiver_laptop.py + gemini_verifier.py:
1. Laptop subscribes to two topic streams:
   - metadata topic: edge/events/v1
   - image topic prefix: edge/images/v1/#
2. Image transfer from Pi to laptop is binary MQTT payload (JPEG bytes).
3. Image bytes are stored as files on laptop under data/images/<event_id>.jpg.
4. SQLite stores image metadata/path/status (not raw image bytes).
5. Gemini verification runs on laptop when both metadata and image are present.
6. Verification writes:
   - verify_status
   - verify_label
   - verify_confidence (None for categorical response)
   - verify_error
   - verify_raw_text

## Laptop Commands (Receiver)

Run on your actual laptop after pulling this repo.

1. Create environment and install:

```powershell
python -m venv .venv-cp2-laptop
.\.venv-cp2-laptop\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r cp2_cp6\requirements-laptop.txt
```

Set Gemini API key in the same shell before running receiver:

```powershell
$env:GEMINI_API_KEY = "<YOUR_API_KEY>"
```

2. Run receiver:

```powershell
python cp2_cp6\server_event_receiver_laptop.py --broker-host DOMCOM2 --broker-port 8883 --topic edge/events/v1 --image-topic-prefix edge/images/v1 --ca-cert .\certs\ca.crt --client-cert .\certs\laptop-client.crt --client-key .\certs\laptop-client.key --db-path .\data\edge_events.db --image-store-dir .\data\images --gemini-model gemini-2.5-flash
```

## Pi Commands (Edge Runtime)

Run on your actual Pi after pulling this repo.

1. Create environment and install:

```bash
python3 -m venv .venv-cp2-pi
source .venv-cp2-pi/bin/activate
pip install --upgrade pip
pip install -r cp2_cp6/requirements-pi.txt
```

2. Run edge pipeline:

```bash
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
  --model-path mobilenet_v2_1.0_224.tflite \
  --label-path labels.txt \
  --edge-model-version mobilenetv2-baseline \
   --capture-dir captures \
   --sound-file /home/pi/sounds/beep.wav \
   --sound-device plughw:3,0 \
   --min-speed-cm-s 65 \
   --outbox-db-path data/pi_outbox.db \
   --retry-base-sec 2 \
   --max-retry-backoff-sec 60 \
   --max-image-bytes 400000
```

3. Optional dedup test:

```bash
python3 cp2_cp6/edge_event_publisher_pi.py \
  --broker-host DOMCOM2 \
   --image-topic-prefix edge/images/v1 \
  --ca-cert certs/ca.crt \
  --client-cert certs/pi-client.crt \
  --client-key certs/pi-client.key \
  --publish-duplicate
```

## Manual Configuration You Still Need To Do

Because this coding workspace is not your real laptop/Pi runtime, do these manually:

1. Laptop:
   - Keep Mosquitto TLS broker running on 8883 using your CP1 cert setup.
   - Ensure cert paths in command match your laptop file locations.
   - Keep firewall inbound TCP 8883 allowed.

2. Pi:
   - Ensure UART is enabled and mmWave is available at /dev/ttyAMA0 (or pass another --mmwave-port).
   - Ensure USB camera index is correct (--camera-id).
   - Place model and labels on Pi or run capture-only mode.
   - Ensure captures and outbox DB locations are writable (capture-dir and outbox-db-path).
   - For sound playback, install alsa-utils (aplay) and provide --sound-file.

3. Network/certs:
   - Ensure Pi resolves laptop hostname (DOMCOM2) correctly.
   - If hostname verification fails in changing demo networks, update mapping or regenerate server cert SAN as needed.

## Known Limits Of Current CP2-CP6 Implementation

1. CP7 dashboard is not included yet.
2. Receiver assumes image payload fits within MQTT broker/message limits.
3. Inference mapping to recyclable classes depends on your label file and keyword matching.
