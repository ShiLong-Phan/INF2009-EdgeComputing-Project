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

1) Baseline capture (no new optimizations yet)

Laptop commands (PowerShell):
- Start system profiler for laptop receiver process:
  python cp2_cp6/paso_system_profile.py --output-csv data/paso/laptop_baseline_system.csv --duration-sec 600 --interval-sec 1 --label baseline --process-name server_event_receiver_laptop.py

Pi commands (bash):
- Start system profiler for Pi publisher process:
  python3 cp2_cp6/paso_system_profile.py --output-csv data/paso/pi_baseline_system.csv --duration-sec 600 --interval-sec 1 --label baseline --process-name edge_event_publisher_pi.py

- Run edge publisher with full PASO event CSV logging enabled:
  python cp2_cp6/edge_event_publisher_pi.py --broker-host DOMCOM2 --broker-port 8883 --topic edge/events/v1 --image-topic-prefix edge/images/v1 --device-id pi-edge-01 --trigger-mode inside_bin --ca-cert certs/ca.crt --client-cert certs/pi-client.crt --client-key certs/pi-client.key --model-path mobilenet_v2_1.0_224.tflite --label-path labels.txt --edge-model-version mobilenetv2-baseline --capture-dir captures --sound-file /home/domaniac/Desktop/skool_projekt/INF2009-EdgeComputing-Project/soundsbeep.wav --sound-device plughw:3,0 --min-speed-cm-s 65 --outbox-db-path data/pi_outbox.db --retry-base-sec 2 --max-retry-backoff-sec 60 --max-image-bytes 400000 --paso-log-csv data/paso/pi_edge_events_baseline.csv

Laptop commands (PowerShell):
- Baseline analysis report:
  python cp2_cp6/paso_analyze_run.py --db-path data/edge_events.db --label baseline --system-csv data/paso/laptop_baseline_system.csv --output-md data/paso/baseline_report.md --output-json data/paso/baseline_report.json

2) Implement CP8/CP9 improvements.

3) Post-change capture and analysis (same duration, same workload)

Laptop commands (PowerShell):
- Start laptop system profiler for after run:
  python cp2_cp6/paso_system_profile.py --output-csv data/paso/laptop_after_system.csv --duration-sec 600 --interval-sec 1 --label after --process-name server_event_receiver_laptop.py

Pi commands (bash):
- Start Pi system profiler for after run:
  python3 cp2_cp6/paso_system_profile.py --output-csv data/paso/pi_after_system.csv --duration-sec 600 --interval-sec 1 --label after --process-name edge_event_publisher_pi.py

- Run edge publisher with after CSV logging enabled:
  python3 cp2_cp6/edge_event_publisher_pi.py --broker-host DOMCOM2 --broker-port 8883 --topic edge/events/v1 --image-topic-prefix edge/images/v1 --device-id pi-edge-01 --trigger-mode inside_bin --ca-cert certs/ca.crt --client-cert certs/pi-client.crt --client-key certs/pi-client.key --model-path mobilenet_v2_1.0_224.tflite --label-path labels.txt --edge-model-version mobilenetv2-baseline --capture-dir captures --sound-file /home/pi/sounds/beep.wav --sound-device plughw:3,0 --min-speed-cm-s 65 --outbox-db-path data/pi_outbox.db --retry-base-sec 2 --max-retry-backoff-sec 60 --max-image-bytes 400000 --paso-log-csv data/paso/pi_edge_events_after.csv

Laptop commands (PowerShell):
- After-run analysis report:
  python cp2_cp6/paso_analyze_run.py --db-path data/edge_events.db --label after --system-csv data/paso/laptop_after_system.csv --output-md data/paso/after_report.md --output-json data/paso/after_report.json

4) Direct baseline vs after comparison (Laptop)
- python cp2_cp6/paso_compare_runs.py --before-json data/paso/baseline_report.json --after-json data/paso/after_report.json --output-md data/paso/comparison.md

Scheduling note for report:
If Scheduling is skipped, justify it with measured CPU/RAM/queue pressure evidence instead of stating Pi 5 is overpowered by assumption.

