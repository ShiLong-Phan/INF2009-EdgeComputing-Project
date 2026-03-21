# CP2 to CP4 Setup and Runbook

This document covers the implementation that was added in this repository for:
- CP2 Event schema and reliability baseline (QoS1 + server dedup)
- CP3 Sensor trigger pipeline
- CP4 Capture + local inference baseline
- Note: I will be using same .venv-laptop from CP1

## Added Files

- cp2_cp4/event_schema.py
- cp2_cp4/edge_event_publisher_pi.py
- cp2_cp4/server_event_receiver_laptop.py
- cp2_cp4/requirements-pi.txt
- cp2_cp4/requirements-laptop.txt

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

## Laptop Commands (Receiver)

Run on your actual laptop after pulling this repo.

1. Create environment and install:

```powershell
python -m venv .venv-cp2-laptop
.\.venv-cp2-laptop\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r cp2_cp4\requirements-laptop.txt
```

2. Run receiver:

```powershell
python cp2_cp4\server_event_receiver_laptop.py --broker-host DOMCOM2 --broker-port 8883 --topic edge/events/v1 --ca-cert .\certs\ca.crt --client-cert .\certs\laptop-client.crt --client-key .\certs\laptop-client.key --db-path .\data\edge_events.db
```

## Pi Commands (Edge Runtime)

Run on your actual Pi after pulling this repo.

1. Create environment and install:

```bash
python3 -m venv .venv-cp2-pi
source .venv-cp2-pi/bin/activate
pip install --upgrade pip
pip install -r cp2_cp4/requirements-pi.txt
```

2. Run edge pipeline:

```bash
python3 cp2_cp4/edge_event_publisher_pi.py \
  --broker-host DOMCOM2 \
  --broker-port 8883 \
  --topic edge/events/v1 \
  --device-id pi-edge-01 \
  --trigger-mode inside_bin \
  --ca-cert certs/ca.crt \
  --client-cert certs/pi-client.crt \
  --client-key certs/pi-client.key \
  --model-path mobilenet_v2_1.0_224.tflite \
  --label-path labels.txt \
  --edge-model-version mobilenetv2-baseline \
   --capture-dir captures \
   --sound-file /home/domaniac/Desktop/skool_projekt/INF2009-EdgeComputing-Project/sounds/beep.wav \
   --sound-device plughw:3,0 \
   --min-speed-cm-s 65
```

3. Optional dedup test:

```bash
python3 cp2_cp4/edge_event_publisher_pi.py \
  --broker-host DOMCOM2 \
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
   - For sound playback, install alsa-utils (aplay) and provide --sound-file.

3. Network/certs:
   - Ensure Pi resolves laptop hostname (DOMCOM2) correctly.
   - If hostname verification fails in changing demo networks, update mapping or regenerate server cert SAN as needed.

## Known Limits Of Current CP2-CP4 Implementation

1. CP5 outbox persistence is not included yet.
2. CP6 cloud verification and CP7 dashboard are not included yet.
3. Inference mapping to recyclable classes depends on your label file and keyword matching.
