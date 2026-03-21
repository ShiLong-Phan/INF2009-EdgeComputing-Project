# Edge Computing Project Plan

## Project Goal

Build an edge-assisted recyclable classifier with two stages:
1) Edge device gives fast local prediction and immediate feedback.
2) Edge server performs mandatory cloud verification (Gemini API) and stores final result.

The first release should prioritize a stable baseline and data collection. Improvements should be added only after baseline metrics are collected.

## Confirmed Decisions

1) MQTT broker and edge server run on laptop (local network).
2) Gemini verification is mandatory for all events.
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

### CP7 - Dashboard MVP

Scope:
- Show latest events, online/offline status, queue depth, agreement rate.
- Show counts by class and time window.

Exit criteria:
- Dashboard provides enough information for demo and debugging.

### CP8 - Edge Image Retention Controls (Optional but Plausible)

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

1) cp2_cp4/event_schema.py
- Shared schema encode/decode and payload validation.

2) cp2_cp4/edge_event_publisher_pi.py
- mmWave trigger profiles (inside_bin, outside_bin).
- Camera capture on trigger.
- Local inference and recyclable keyword check.
- Optional affirmative sound playback.
- MQTT publish with QoS 1, dual topics (event + image), and optional duplicate publish mode.
- CP5 integration with FIFO SQLite outbox, retries, and backoff.

3) cp2_cp4/pi_outbox.py
- Pi-side FIFO SQLite queue for event/image delivery tracking.

4) cp2_cp4/server_event_receiver_laptop.py
- TLS MQTT receiver.
- Schema validation.
- SQLite idempotent upsert keyed by event_id.
- Duplicate accounting through receive_count.
- Image topic ingestion and local image persistence on laptop.
- CP6 Gemini verification and verification status/result persistence.

5) cp2_cp4/gemini_verifier.py
- Gemini image classification helper adapted for laptop-side verification.

6) cp2_cp4/requirements-pi.txt
7) cp2_cp4/requirements-laptop.txt

Operational guide:

- Use CP2_TO_CP4_SETUP.md for setup commands, CP5/CP6 runtime commands, dedup test flow, image transport flow, and manual hardware/network steps required on laptop and Pi.
