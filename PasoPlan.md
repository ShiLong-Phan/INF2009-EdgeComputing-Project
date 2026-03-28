Profiling (Finding the Bottlenecks)
Profiling involves measuring the execution time of individual operations, memory access patterns, and power consumption. This helps pinpoint exactly which layers or functions are slowing things down or draining the battery.
Example: Running camera to poll for detect motion constantly for 1 minute vs using motion sensor only.

Analysing (Understanding the Why)
Once you have the profiling data, analyzing helps you understand system-level constraints. This means looking at whether your CPU or GPU is under-utilized or overloaded, identifying memory access delays, or spotting network bandwidth issues. (Note: I expect two constraints - intermittent network issues (phone hotspot) and battery life (powerbank). Don't assume though.)

Scheduling (Managing the Workload)
Scheduling involves dynamically assigning tasks to processing units based on real-time resource availability and priority. (I'm not sure what we could do for this part. We can skip this because our Raspberry Pi 5 is very much overpowered.) Gemini suggestion on this below:
In your trash sensor project: You can take a page from smart security camera designs: you use your motion sensor to dictate the schedule.Movement Detected: When a user approaches to throw something away, the system immediately wakes up and schedules the object classification model (recyclable vs. non-recyclable) as a high-priority task.No Movement: The heavy object detection model pauses entirely, freeing up the processor and saving significant power when nothing is happening.

Optimisation (Making it Faster and Lighter)
This final stage applies advanced techniques to accelerate computation and minimize power usage. This can involve hardware acceleration, adjusting the model's architecture, or refining the low-level code. We're mainly looking to reduce energy consumption.


As an aside, I think what my professor wants are measurements of important metrics: RAM, CPU usage, power usage etc., so for any plan, the important thing is measuring the start state and the end state, and observing changes.

One-day practical PASO checklist (before CP8/CP9, then after):

0) Keep Documentation.md command-free
- All executable commands are listed in this file only.

1) Baseline capture (no new optimizations yet) (done)

Laptop commands (PowerShell):
- Start system profiler for laptop receiver process:
  python cp2_cp6/paso_system_profile.py --output-csv data/paso/laptop_baseline_system.csv --duration-sec 300 --interval-sec 1 --label baseline --process-name server_event_receiver_laptop.py

Pi commands (bash):
- Start system profiler for Pi publisher process:
  python3 cp2_cp6/paso_system_profile.py --output-csv data/paso/pi_baseline_system.csv --duration-sec 300 --interval-sec 1 --label baseline --process-name edge_event_publisher_pi.py

- Run edge publisher with full PASO event CSV logging enabled:
  python3 cp2_cp6/edge_event_publisher_pi.py --broker-host DOMCOM2 --broker-port 8883 --topic edge/events/v1 --image-topic-prefix edge/images/v1 --device-id pi-edge-01 --trigger-mode inside_bin --ca-cert certs/ca.crt --client-cert certs/pi-client.crt --client-key certs/pi-client.key --model-path mobilenet_v2_1.0_224.tflite --label-path labels.txt --edge-model-version mobilenetv2-baseline --capture-dir captures --sound-file sounds/beep.wav --min-speed-cm-s 65 --outbox-db-path data/pi_outbox.db --retry-base-sec 2 --max-retry-backoff-sec 60 --max-image-bytes 400000 --frame-width 320 --frame-height 240 --paso-log-csv data/paso/pi_edge_events_baseline.csv

Laptop commands (PowerShell):
- Baseline analysis report:
  python cp2_cp6/paso_analyze_run.py --db-path data/edge_events.db --label baseline --system-csv data/paso/laptop_baseline_system.csv --pi-system-csv data/paso/pi_baseline_system.csv --output-md data/paso/baseline_report.md --output-json data/paso/baseline_report.json

2) Implement Optimizations:

2.1) Frame differencing — isolate object from background before inference

Goal: Instead of feeding the raw camera frame (which includes bin interior / wall /
surroundings) to the classifier, subtract a known background image so the model sees
only the recyclable item on a clean (black/neutral) canvas. This should improve
classification accuracy without meaningful latency cost (~2-5 ms of OpenCV ops).

Two variants, matching the two physical mounts for the demo:

--- Variant A: Static background (outside-bin, PRIORITY) ---

Scenario: Pi is mounted outside the bin facing a plain wall. User holds up an object,
waves to trigger mmWave, gets result, removes object. The wall never changes.

Implementation (modify edge_event_publisher_pi.py directly):
  a) New CLI flag: --bg-image <path>
     - On startup, if the file exists, load it as the reference background (BGR, same
       resolution as capture). If it does not exist yet, the first captured frame is
       saved to that path and used as the background.
  b) New CLI flag: --bg-capture-on-start (optional convenience)
     - If set, discard any existing bg-image file, capture a fresh frame on startup
       and save it. Useful when the Pi is re-mounted in a slightly different position.
  c) Dashboard "Capture Background" button (stretch — implement if time permits):
     - POST endpoint on the Pi or an MQTT command from the dashboard that tells the
       publisher to overwrite the bg-image file with the current camera frame.
  d) Preprocessing in the trigger path (between image capture and run_inference):
     1. Convert both background and trigger frame to grayscale.
     2. cv2.absdiff → threshold (binary, ~30-40) → dilate to fill gaps → mask.
     3. Find contours on the mask, take the bounding rect of the largest contour.
     4. Crop the trigger frame (colour, not grayscale) to that bounding rect.
     5. Resize crop to model input size (224×224) and feed to run_inference.
     6. If no significant foreground is detected (contour area < min threshold),
        skip inference and log "no object detected" — avoids wasting cycles.
  e) Save both the raw trigger frame AND the cropped/masked image to capture_dir
     so we can visually verify quality during the demo and in PASO analysis.
  f) PASO log: add a column "fg_area_px" (foreground pixel count) so we can
     correlate crop size with accuracy in the after-run report.

Testing checklist (before demo):
  - Run with --bg-image bg.jpg on laptop webcam (no mmWave needed) to verify
    crop quality visually.
  - Confirm edge_reaction_ms stays under ~50 ms with the extra preprocessing.
  - Confirm PASO CSV and outbox still work correctly.

--- Variant B: Dynamic background (inside-bin, separate file) ---

Scenario: Pi is mounted above the bin looking down. Each toss adds to the pile.
Background = whatever was in the bin before the latest toss.

Implementation (new file: edge_event_publisher_pi_dynamic_bg.py):
  a) On startup, capture the first frame as the initial background.
  b) On each trigger:
     1. Diff the new frame against the stored background (same steps d.1-d.6 above).
     2. Run inference on the cropped foreground.
     3. AFTER inference and publishing, update the stored background to the current
        frame (post-toss state of the bin). This means the next trigger will diff
        against the bin-with-all-previous-items, isolating only the newly added one.
  c) Edge case: if the diff produces no significant contour (e.g. the item landed
     in exactly the same spot as the background frame), fall back to full-frame
     inference and log a warning.
  d) Stretch: periodic background refresh (every N seconds with no trigger) to
     handle lighting drift.

Priority order:
  1. Variant A in edge_event_publisher_pi.py — get this working and measured first.
  2. Variant B in a new file — implement after Variant A is verified.
  3. Trained model swap (shelved) — plug in groupmate's model + labels when ready.

2.2) Trained model swap (DONE — converted from Edge_Model_Refinement.ipynb)
     waste_classifier_v1.keras → waste_classifier_v1.tflite (2.42 MB, quantized)
     Labels: AluCan, Glass, HDPEM, PET (4 classes, all recyclable drinking waste)
     Input: 224×224×3, same preprocessing as baseline MobileNetV2.

3) Post-change capture and analysis (same duration, same workload)

Laptop commands (PowerShell):
- Start laptop system profiler for after run:
  python cp2_cp6/paso_system_profile.py --output-csv data/paso/laptop_after_system.csv --duration-sec 600 --interval-sec 1 --label after --process-name server_event_receiver_laptop.py

Pi commands (bash):
- Start Pi system profiler for after run:
  python3 cp2_cp6/paso_system_profile.py --output-csv data/paso/pi_after_system.csv --duration-sec 600 --interval-sec 1 --label after --process-name edge_event_publisher_pi.py

- Run edge publisher with after CSV logging enabled:
  python3 cp2_cp6/edge_event_publisher_pi.py --broker-host DOMCOM2 --broker-port 8883 --topic edge/events/v1 --image-topic-prefix edge/images/v1 --device-id pi-edge-01 --trigger-mode inside_bin --ca-cert certs/ca.crt --client-cert certs/pi-client.crt --client-key certs/pi-client.key --model-path waste_classifier/waste_classifier_v1.tflite --label-path waste_classifier/labels.txt --edge-model-version waste-classifier-v1 --capture-dir captures --sound-file sounds/beep.wav --min-speed-cm-s 65 --outbox-db-path data/pi_outbox.db --retry-base-sec 2 --max-retry-backoff-sec 60 --max-image-bytes 400000 --recyclable-keywords AluCan,Glass,HDPEM,PET --bg-threshold 30 --bg-min-area-px 1500 --bg-crop-pad-px 10 --paso-log-csv data/paso/pi_edge_events_after.csv --bg-blur-kernel 21 

Laptop commands (PowerShell):
- After-run analysis report:
  python cp2_cp6/paso_analyze_run.py --db-path data/edge_events.db --label after --system-csv data/paso/laptop_after_system.csv --pi-system-csv data/paso/pi_after_system.csv --output-md data/paso/after_report.md --output-json data/paso/after_report.json

4) Camera-motion prototype comparison (optional — shows mmWave-trigger vs always-on-camera cost) (done)

Pi commands (bash):
- Start Pi system profiler for polling run:
  python3 cp2_cp6/paso_system_profile.py --output-csv data/paso/pi_polling_system.csv --duration-sec 300 --interval-sec 1 --label polling --process-name edge_event_publisher_polling.py

- Run camera-motion publisher (no mmWave, camera detects motion via frame differencing):
  python3 cp2_cp6/edge_event_publisher_polling.py --broker-host DOMCOM2 --broker-port 8883 --topic edge/events/v1 --image-topic-prefix edge/images/v1 --device-id pi-edge-01 --ca-cert certs/ca.crt --client-cert certs/pi-client.crt --client-key certs/pi-client.key --model-path mobilenet_v2_1.0_224.tflite --label-path labels.txt --edge-model-version mobilenetv2-baseline --capture-dir captures --sound-file sounds/beep.wav --outbox-db-path data/pi_outbox_polling.db --retry-base-sec 2 --max-retry-backoff-sec 60 --max-image-bytes 400000 --debounce-sec 1.0 --motion-min-area-px 3000 --motion-threshold 25 --paso-log-csv data/paso/pi_edge_events_polling.csv

Laptop commands (PowerShell):
- Start laptop system profiler for polling run:
  python cp2_cp6/paso_system_profile.py --output-csv data/paso/laptop_polling_system.csv --duration-sec 300 --interval-sec 1 --label polling --process-name server_event_receiver_laptop.py

- Polling analysis report (filter to events from this run's CSV):
  python cp2_cp6/paso_analyze_run.py --db-path data/edge_events.db --label polling --system-csv data/paso/laptop_polling_system.csv --pi-system-csv data/paso/pi_polling_system.csv --event-csv data/paso/pi_edge_events_polling.csv --output-md data/paso/polling_report.md --output-json data/paso/polling_report.json

- Compare baseline (mmWave-triggered) vs polling (always-on camera):
  python cp2_cp6/paso_compare_runs.py --before-json data/paso/baseline_reportd.json --after-json data/paso/polling_report.json --output-md data/paso/comparison_polling_vs_baseline.md

5) Direct baseline vs after comparison (Laptop)
- python cp2_cp6/paso_compare_runs.py --before-json data/paso/baseline_report.json --after-json data/paso/after_report.json --output-md data/paso/comparison.md

Scheduling note for report:
If Scheduling is skipped, justify it with measured CPU/RAM/queue pressure evidence instead of stating Pi 5 is overpowered by assumption.

