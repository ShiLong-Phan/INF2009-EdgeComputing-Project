# Edge Computing Project Plan

## Project Goal

Build an edge-assisted recyclable classifier with two stages:
1) Edge device gives fast local prediction and immediate feedback.
2) Edge server performs mandatory cloud verification (NanoGPT/Qwen API) and stores final result.

The first release should prioritize a stable baseline and data collection. Improvements should be added only after baseline metrics are collected.

## Confirmed Decisions

1) MQTT broker and edge server run on laptop (local network).
2) Cloud verification is mandatory for all events.
3) Image storage is optional and should support cleanup from dashboard command.
4) Target edge model accuracy is around 80 percent (initial baseline target).
5) Latency target of sub-100ms is aspirational for edge-only stage; full pipeline latency with cloud verification will likely be higher.

## Hardware

- Logitech C310 webcam
- Powerbank (5V output)
- Speaker
- mmWave sensor (HLK-LD2450)
- Raspberry Pi edge device
- Laptop for MQTT broker, server, dashboard, and local DB

## Deployment Modes

Edge device can be mounted in either mode:
1) Inside-bin mode: detect when trash is thrown in.
2) Outside-bin mode: user handwaves to trigger capture.

Both modes should reuse the same software pipeline and differ only in sensor threshold/tuning config.

## Baseline System Architecture

### Edge Device (Raspberry Pi)

1) Read mmWave events.
2) On trigger, capture image.
3) Run local classifier (MobileNetV2 prototype: bottle/can first).
4) Play affirmative sound for recyclable classes.
5) Create event payload with metadata and optional image path.
6) Publish payload to laptop via MQTTS.
7) If broker unreachable, save event in local outbox queue and retry later.

### Edge Server (Laptop)

1) Receive MQTT event package.
2) Validate schema and event id.
3) Run mandatory Gemini verification via API.
4) Compare edge prediction vs Gemini result.
5) Store event, predictions, and comparison in local DB.
6) Update dashboard with latest status and metrics.

## Data Contract (MVP)

Each event should include at least:

- event_id: unique UUID for deduplication
- device_id: edge device identifier
- timestamp_utc
- trigger_mode: inside_bin or outside_bin
- edge_model_version
- edge_pred_label
- edge_confidence
- image_ref: optional local filename/hash
- payload_version

Server should enforce idempotent upsert by event_id.

## Security and Transport

Use MQTTS (TLS) for transport security.

For MVP:
1) TLS in transit is mandatory.
2) Mutual TLS is preferred if feasible in time.
3) Payload encryption at application layer is optional and can be deferred.

Notes:
- MQTTS secures data in transit.
- If local disk encryption is needed later, treat it as a separate improvement track.

## Performance Targets

Define two latency metrics to avoid mixing local and cloud paths:

1) Edge reaction latency (trigger to local inference + sound): target less than 100ms aspirational, acceptable initial baseline less than 300ms.
2) End-to-end verified latency (trigger to Gemini-verified DB write): target less than 2-5s depending on network/API.

Additional baseline targets:
- Edge model accuracy: at least 80 percent on defined validation set.
- Queue durability: no data loss in a 10-minute laptop disconnect test.
- Duplicate handling: duplicate event publish results in one logical row.

## Checkpoint Plan (Small Milestones)

### CP0 - Environment and Reproducibility (DONE)

Scope:
- Pin Python versions and dependencies.
- Split requirements for edge and server.
- Verify camera + UART sensor setup.

Exit criteria:
- Fresh setup can run sensor test and camera capture without manual fixes.

### CP1 - Basic MQTTS Link (Pi -> Laptop) (DONE)

Scope:
- Start Mosquitto broker on laptop.
- Configure TLS certificates.
- Publish hello payload from Pi and consume on laptop.

Exit criteria:
- Stable publish/consume over MQTTS for at least 1000 test messages.

### CP2 - Event Schema and Reliability (IMPLEMENTED IN CODE)

Scope:
- Implement JSON event envelope.
- Add event_id and timestamp.
- Use QoS 1 and server dedup logic.

Exit criteria:
- Intentional duplicate messages create one logical DB record.

### CP3 - Sensor Trigger Pipeline (IMPLEMENTED IN CODE)

Scope:
- Convert mmWave detection into event creation.
- Tune thresholds for inside-bin and outside-bin profiles.

Exit criteria:
- Trigger behavior is predictable in both mounting modes.

### CP4 - Capture + Local Inference Baseline (IMPLEMENTED IN CODE)

Scope:
- Capture image on motion trigger.
- Run local model and include prediction in payload.
- Play affirmative sound for recyclable class.

Exit criteria:
- End-to-end local edge loop works repeatedly without crash.

### CP5 - Offline Outbox Queue (IMPLEMENTED IN CODE)

Scope:
- Persist unsent events locally.
- Retry with exponential backoff when laptop is offline.

Exit criteria:
- No data loss after forced disconnection and reconnect test.

### CP6 - Mandatory Gemini Verification (IMPLEMENTED IN CODE)

Scope:
- Server receives event, calls Gemini API for verification.
- Store edge vs cloud comparison fields.

Exit criteria:
- Every received event has a verification result or explicit error status.

### CP7 - Dashboard MVP (IMPLEMENTED IN CODE)

Scope:
- Show latest events, online/offline status, queue depth, agreement rate.
- Show counts by class and time window.

Exit criteria:
- Dashboard provides enough information for demo and debugging.

### CP7.5 - PASO + Optimisations (IMPLEMENTED — done between CP7 and CP8)

PASO stands for Profile, Analyse, Schedule, Optimise. This was carried out as a
structured measurement-and-improvement cycle before progressing to CP8.

#### Profiling and baseline capture (done)

System and event metrics were collected over a 300-second window while running the
full pipeline (mmWave trigger → local inference → MQTT publish → cloud verification)
using mobilenetv2-baseline. Key results:

- Edge reaction latency (trigger to inference): mean 34 ms, median 33 ms, p95 47 ms.
- Broker ingest latency (edge to server): mean 1070 ms, median 406 ms, p95 3984 ms.
- Verification latency (trigger to cloud result): mean 4537 ms, p95 8470 ms.
- Pi process RSS: ~139 MB. Pi process CPU: mean 3.5%, p95 10%.
- Identified bottleneck: network and cloud API (phone hotspot + NanoGPT roundtrip).
  Edge reaction itself was already under 50 ms.

Full baseline data is in data/paso/baseline_report.md and baseline_report.json.

#### Optimisation 1 — Static background frame differencing (Variant A)

Motivation: the raw trigger frame fed to the classifier includes the surrounding
environment (wall, bin, hands), which degrades accuracy on the custom waste model.
Frame differencing removes the static background so the model receives only the
foreground object.

How it works (edge_event_publisher_pi.py):
1. On startup the publisher captures one reference background frame from the camera
   and saves it to captures/background.jpg. If a saved background already exists it
   is loaded instead. Nothing else happens until a trigger fires.
2. On each mmWave trigger, the trigger frame is first saved as trigger_<ts>.jpg (raw).
3. Frame differencing pipeline:
   a. Both background and trigger frames are converted to grayscale and Gaussian-blurred
      (kernel default 21) to suppress per-pixel noise from camera auto-exposure shifts.
   b. cv2.absdiff produces an absolute-difference image.
   c. Binary threshold (default 30) → morphological close → dilate yields a foreground mask.
   d. The largest contour is found. If its area exceeds bg-min-area-px (default 1500),
      its bounding rect is cropped with padding from the colour frame and saved as
      processed_<ts>.jpg.
   e. If no significant contour is found the full raw frame is used as fallback.
4. The processed (cropped) image is what is fed to the classifier and published to the
   cloud, not the raw frame.
5. PASO log gains a fg_area_px column so crop size can be correlated with accuracy.

New CLI flags on edge_event_publisher_pi.py:
- --bg-threshold (default 30): binary diff threshold.
- --bg-min-area-px (default 1500): minimum foreground contour area to accept.
- --bg-crop-pad-px (default 10): padding around the crop bounding rect.
- --bg-blur-kernel (default 21): Gaussian blur kernel size before diffing (0 to disable).
- --min-confidence (default 0.8): confidence gate — if the top class score is below this
  threshold the prediction is overridden to "unknown" and no beep is triggered. Suppresses
  false positives when the sensor fires but no clear object is present.

False-positive handling: if frame differencing finds no contour above bg-min-area-px,
inference is skipped entirely. The event is logged as unknown/0.0 and the loop continues
without beeping. This is the primary guard against spurious triggers (e.g. hand waving
near the sensor without holding an item).

Dashboard (dashboard_cp7.py) gains a POST /api/reset-bg/<device_id> endpoint that
publishes an MQTT command to edge/bg-reset/request/<device_id>. The publisher
subscribes to this topic and resets the background from the next available camera
frame. A "Reset Background" button was added to the device detail page in the
dashboard for this purpose.

#### Optimisation 2 — Custom-trained waste classifier model

Motivation: the original mobilenet_v2_1.0_224.tflite (ImageNet classes) was poor
at distinguishing cans specifically. Trained a MobileNetV2 fine-tuned
on the Kaggle "Drinking Waste Classification" dataset (arkadiyhacks) using
Edge_Model_Refinement.ipynb.

Model details:
- Base: MobileNetV2 1.00/224, ImageNet weights, backbone frozen.
- Head: GlobalAveragePooling2D → Dropout(0.3) → Dense(4, softmax).
- 4 classes: AluCan, Glass, HDPEM, PET.
- Training: 20 epochs, data augmentation (flip, rotation, brightness), ReduceLROnPlateau.
- Saved as waste_classifier/waste_classifier_v1.keras (Keras 3, 9.2 MB).

Conversion: the .keras file was converted to TFLite with default quantization using
TFLiteConverter.from_keras_model(), producing waste_classifier/waste_classifier_v1.tflite
(2.42 MB). Labels file is waste_classifier/labels.txt (one class per line, alphabetical).

Inference runtime: switched from cv2.dnn.readNet() (which couldn't handle the augmentation
layer baked into the model) to the TFLite interpreter. Import priority is
ai_edge_litert → tflite_runtime → tensorflow.lite. Preprocessing is done manually:
resize to 224×224 → BGR→RGB → scale to [-1, 1] → expand batch dimension.

Label normalisation in dashboard_cp7.py (_normalize_label) and paso_analyze_run.py
(normalize_label) map the 4 model classes to dashboard categories:
- AluCan → CAN (contains "can" substring, matched automatically).
- PET → BOTTLE (bottle-shaped recyclable; target for beep).
- Glass → UNKNOWN (not a campaign-target recyclable in this deployment).
- HDPEM → UNKNOWN (same rationale as Glass).

> **[OUTDATED — superseded]** Previously Glass and HDPEM were mapped to BOTTLE.
> Changed because they are not recyclables targeted by this bin campaign and should
> not count as verified recyclable detections or trigger agreement in analysis.

Only AluCan and PET trigger the affirmative beep (--recyclable-keywords AluCan,PET).
Glass and HDPEM are logged as UNKNOWN and do not beep.

#### Post-optimisation measurement

After-run commands (capturing 300-second window, same workload) are in PasoPlan.md
section 3. The Pi publisher command uses --model-path waste_classifier/waste_classifier_v1.tflite,
--edge-model-version waste-classifier-v1, and the new background differencing flags.
Results will be compared against the baseline using paso_compare_runs.py.

### CP8 - ML/AI features
Scope:
Build an analytics page and dataset export flow that answers: "What are users trying to recycle, and where are they uncertain?"

Core analytics deliverables:
1) Global spread of scanned materials:
	- Count and percentage by normalized class (BOTTLE, CAN, UNKNOWN, OTHER).
	- UNKNOWN and OTHER are treated as non-recyclable/uncertain campaign targets.
2) Per-device behavior profile:
	- Top scanned labels per device.
	- Unknown-rate per device = unknown_or_other_scans / total_scans.
3) Verification quality analytics:
	- Agreement rate by device and globally.
	- Mismatch distribution (edge says bottle, cloud says can, etc.).
4) Time-window insights:
	- Daily and hourly scan volume trends.
	- Daily unknown-rate trend to detect confusion periods.
5) Campaign recommendation view (rule-based for MVP):
	- If unknown-rate for a device/time-window exceeds threshold, flag campaign focus.
	- Example recommendation: "Increase bottle-vs-can signage near Device pi-edge-02."

Suggested CP8 dashboard pages:
1) Data page (global): spread charts + latest 7-day trends.
2) Device analytics page: per-device material mix and unknown-rate trend.
3) Campaign insights page: ranked recommended interventions.

Practical ML progression (post-MVP):
1) Baseline forecasting with simple time-series/linear trend on daily counts.
2) Confidence calibration of edge model using cloud-confirmed labels.
3) Drift watch: detect sudden class distribution changes by device.

Exit criteria:
1) Dashboard includes a dedicated "Data" analytics page.
2) Export CSV endpoint exists for report generation.
3) At least one actionable campaign recommendation is generated from real data.

### CP9 - Edge Image Retention Controls (Optional but Plausible)

Scope:
- Add dashboard command to request edge cleanup.
- Send command over MQTT control topic.
- Edge acknowledges and deletes old images by policy.

Exit criteria:
- Operator can trigger image cleanup from dashboard and see success/failure response.

## Improvements Backlog (After Baseline)

1) ROI cropping before inference (detect object region, crop majority background) to reduce inference time.
2) Better dataset collection + retraining loop.
3) Rich device health telemetry (disk usage, last heartbeat, packet rate).
4) Cloud DB migration after local DB baseline stabilizes.
5) Human review workflow for selected low-confidence events.

## Non-Goals for Initial Release

1) Bin fullness detection.
2) Full battery telemetry.
3) Complex model optimization before collecting baseline metrics.

## Open Questions to Confirm with Supervisors

1) Official latency KPI to grade against (edge-only vs end-to-end).
2) Required evaluation dataset size for claiming 80 percent accuracy.
3) How many events must be collected for baseline report.
4) Whether Gemini can be treated as reference label in assessment reports.

## Implementation Artifacts (CP2-CP6)

Implemented files:

1) cp2_cp6/event_schema.py
- Shared schema encode/decode and payload validation.

2) cp2_cp6/edge_event_publisher_pi.py
- mmWave trigger profiles (inside_bin, outside_bin).
- Camera capture on trigger.
- Local inference and recyclable keyword check.
- Optional affirmative sound playback.
- MQTT publish with QoS 1, dual topics (event + image), and optional duplicate publish mode.
- CP5 integration with FIFO SQLite outbox, retries, and backoff.

3) cp2_cp6/pi_outbox.py
- Pi-side FIFO SQLite queue for event/image delivery tracking.

4) cp2_cp6/server_event_receiver_laptop.py
- TLS MQTT receiver.
- Schema validation.
- SQLite idempotent upsert keyed by event_id.
- Duplicate accounting through receive_count.
- Image topic ingestion and local image persistence on laptop.
- CP6 cloud verification and verification status/result persistence.

5) cp2_cp6/nanogpt_verifier.py
- NanoGPT/Qwen image classification helper adapted for laptop-side verification.

6) cp2_cp6/requirements-pi.txt
7) cp2_cp6/requirements-laptop.txt

Operational guide:

- Use CP2_TO_CP4_SETUP.md for setup commands, CP5/CP6 runtime commands, dedup test flow, image transport flow, and manual hardware/network steps required on laptop and Pi.
- Use CP7_DEMO_2PI_RUNBOOK.md for end-to-end demo commands (broker, receiver, dashboard, and two Pi clients).

## Reporting and Analytics Deliverables

For supervisor-facing demos and reports:
1) Include a "Data" page showing spread of recyclables/non-recyclables over a selectable time window.
2) Include per-device unknown-rate and mismatch-rate to identify confusion hotspots.
3) Export CSV snapshots from the local DB for campaign planning evidence.
