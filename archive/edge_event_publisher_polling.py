"""Camera-motion edge publisher (prototype comparison baseline).

Replaces the mmWave sensor with camera-based motion detection.
The camera runs continuously; when frame-differencing detects movement
above --motion-min-area-px pixels, inference fires and an event is
published — same debounce and PASO logging as edge_event_publisher_pi.py.

This models the original prototype design where the camera handles both
motion sensing and image capture, with no dedicated hardware sensor.
"""

import argparse
import csv
import os
import re
import shutil
import ssl
import subprocess
import time
import uuid
from datetime import datetime
from typing import Optional, Tuple
import json

import cv2
import numpy as np
import paho.mqtt.client as mqtt

from event_schema import SUPPORTED_PAYLOAD_VERSION, encode_payload, utc_now_iso
from pi_outbox import PiOutbox


class PollingPublisherApp:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self._resolve_sound_file_path()
        self.net = None
        self.labels = []
        self.connected = False
        self.outbox = PiOutbox(self.args.outbox_db_path)
        self.paso_log_csv = os.path.abspath(self.args.paso_log_csv) if self.args.paso_log_csv else ""

        os.makedirs(self.args.capture_dir, exist_ok=True)
        if self.paso_log_csv:
            os.makedirs(os.path.dirname(self.paso_log_csv) or ".", exist_ok=True)
            if not os.path.exists(self.paso_log_csv):
                with open(self.paso_log_csv, "w", newline="", encoding="utf-8") as csv_file:
                    writer = csv.writer(csv_file)
                    writer.writerow(
                        [
                            "timestamp_utc",
                            "event_id",
                            "device_id",
                            "trigger_mode",
                            "edge_reaction_ms",
                            "edge_pred_label",
                            "edge_confidence",
                            "outbox_pending",
                        ]
                    )

    def _resolve_sound_file_path(self) -> None:
        if not self.args.sound_file:
            return

        requested = os.path.expanduser(self.args.sound_file)
        if os.path.exists(requested):
            self.args.sound_file = os.path.abspath(requested)
            return

        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        fallback = os.path.join(repo_root, "sounds", os.path.basename(requested))
        if os.path.exists(fallback):
            print(f"[EDGE] Using fallback sound file: {fallback}")
            self.args.sound_file = fallback

    def try_load_model(self) -> None:
        if not os.path.exists(self.args.model_path) or not os.path.exists(self.args.label_path):
            print("[EDGE] Model or labels not found. Running capture-only prediction mode.")
            return

        with open(self.args.label_path, "r", encoding="utf-8") as label_file:
            self.labels = [line.strip() for line in label_file if line.strip()]

        self.net = cv2.dnn.readNet(self.args.model_path)
        print(f"[EDGE] Loaded model: {self.args.model_path}")

    def run_inference(self, frame: np.ndarray) -> Tuple[str, float]:
        if self.net is None:
            return "unknown", 0.0

        blob = cv2.dnn.blobFromImage(
            frame,
            scalefactor=1.0 / 127.5,
            size=(224, 224),
            mean=(127.5, 127.5, 127.5),
            swapRB=True,
            crop=False,
        )
        self.net.setInput(blob)
        pred = self.net.forward()
        pred = np.asarray(pred)

        top_idx = int(np.argmax(pred))
        confidence = float(pred.reshape(-1)[top_idx])
        label = self.labels[top_idx] if top_idx < len(self.labels) else f"class_{top_idx}"

        return label, max(0.0, min(1.0, confidence))

    def is_recyclable(self, label: str) -> bool:
        lowered = label.lower()
        keywords = [k.strip().lower() for k in self.args.recyclable_keywords.split(",") if k.strip()]
        return any(keyword in lowered for keyword in keywords)

    def play_affirmative_sound(self) -> None:
        if not self.args.sound_file:
            return

        if not os.path.exists(self.args.sound_file):
            print(f"[EDGE] Sound file not found: {self.args.sound_file}")
            return

        if shutil.which("aplay"):
            candidates = []
            if self.args.sound_device:
                candidates.append(self.args.sound_device)

            usb_device = self._detect_usb_sound_device()
            if usb_device and usb_device not in candidates:
                candidates.append(usb_device)

            candidates.append("")

            tried = set()
            for device in candidates:
                if device in tried:
                    continue
                tried.add(device)

                cmd = ["aplay"]
                if device:
                    cmd.extend(["-D", device])
                cmd.append(self.args.sound_file)

                result = subprocess.run(cmd, check=False, capture_output=True, text=True)
                if result.returncode == 0:
                    if device and device != self.args.sound_device:
                        print(f"[EDGE] Sound playback succeeded using device: {device}")
                    return

                stderr = (result.stderr or "").strip()
                last_line = stderr.splitlines()[-1] if stderr else "unknown aplay error"
                print(f"[EDGE] Sound playback failed on device '{device or 'default'}': {last_line}")

            return

        print("[EDGE] 'aplay' not found. Skipping sound playback.")

    def _detect_usb_sound_device(self) -> str:
        try:
            result = subprocess.run(["aplay", "-l"], check=False, capture_output=True, text=True)
            if result.returncode != 0:
                return ""

            for line in (result.stdout or "").splitlines():
                if "usb" not in line.lower():
                    continue
                match = re.search(r"card\s+(\d+):", line)
                if match:
                    return f"plughw:{match.group(1)},0"
        except Exception:
            return ""

        return ""

    def on_connect(self, client, _userdata, _flags, reason_code, _properties) -> None:
        if reason_code == 0:
            self.connected = True
            print("[EDGE] Connected to MQTT broker.")
        else:
            self.connected = False
            print(f"[EDGE] MQTT connection failed: reason_code={reason_code}")

    def on_disconnect(self, client, _userdata, _flags, reason_code, _properties) -> None:
        self.connected = False
        print(f"[EDGE] Disconnected from MQTT broker: reason_code={reason_code}")

    def build_event_payload(
        self,
        image_ref: str,
        label: str,
        confidence: float,
        edge_reaction_ms: float,
    ) -> dict:
        return {
            "event_id": str(uuid.uuid4()),
            "device_id": self.args.device_id,
            "timestamp_utc": utc_now_iso(),
            "trigger_mode": "camera_motion",
            "edge_model_version": self.args.edge_model_version,
            "edge_pred_label": label,
            "edge_confidence": round(confidence, 4),
            "image_ref": image_ref,
            "payload_version": SUPPORTED_PAYLOAD_VERSION,
            "mmwave_distance_cm": None,
            "mmwave_speed_cm_s": None,
            "edge_reaction_ms": round(edge_reaction_ms, 2),
        }

    def append_paso_event_row(self, payload: dict) -> None:
        if not self.paso_log_csv:
            return

        with open(self.paso_log_csv, "a", newline="", encoding="utf-8") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(
                [
                    utc_now_iso(),
                    payload.get("event_id", ""),
                    payload.get("device_id", ""),
                    payload.get("trigger_mode", ""),
                    payload.get("edge_reaction_ms", ""),
                    payload.get("edge_pred_label", ""),
                    payload.get("edge_confidence", ""),
                    self.outbox.count_pending(),
                ]
            )

    def _wait_publish(self, info) -> None:
        info.wait_for_publish(timeout=self.args.publish_timeout_sec)
        if not info.is_published():
            raise RuntimeError("publish timed out before PUBACK")

    def _publish_event_payload_text(self, client: mqtt.Client, event_id: str, payload_text: str) -> None:
        result = client.publish(self.args.topic, payload=payload_text, qos=1, retain=False)
        self._wait_publish(result)
        print(f"[EDGE] Published event metadata event_id={event_id} mid={result.mid}")

    def _publish_event_image(self, client: mqtt.Client, event_id: str, image_path: str) -> None:
        if not os.path.exists(image_path):
            raise RuntimeError(f"queued image not found: {image_path}")

        with open(image_path, "rb") as image_file:
            image_bytes = image_file.read()

        if len(image_bytes) > self.args.max_image_bytes:
            raise RuntimeError(
                f"image too large ({len(image_bytes)} bytes), max allowed is {self.args.max_image_bytes}"
            )

        image_topic = f"{self.args.image_topic_prefix}/{event_id}"
        result = client.publish(image_topic, payload=image_bytes, qos=1, retain=False)
        self._wait_publish(result)
        print(f"[EDGE] Published event image event_id={event_id} bytes={len(image_bytes)} mid={result.mid}")

    def drain_outbox(self, client: mqtt.Client) -> None:
        if not self.connected:
            return

        item = self.outbox.peek_ready()
        if item is None:
            return

        try:
            if not item.event_published:
                self._publish_event_payload_text(client, item.event_id, item.event_payload)
                self.outbox.mark_event_published(item.id)

            if not item.image_published:
                self._publish_event_image(client, item.event_id, item.image_path)
                self.outbox.mark_image_published(item.id)

            self.outbox.complete(item.id)
            if self.args.delete_image_after_send and os.path.exists(item.image_path):
                os.remove(item.image_path)
            print(f"[EDGE] Outbox delivered event_id={item.event_id} pending={self.outbox.count_pending()}")
        except Exception as ex:
            retry_count = item.retry_count + 1
            delay_sec = min(self.args.max_retry_backoff_sec, self.args.retry_base_sec * (2 ** max(0, retry_count - 1)))
            next_retry_ts = time.time() + float(delay_sec)
            self.outbox.defer_retry(item.id, retry_count, next_retry_ts, str(ex))
            print(
                f"[EDGE] Outbox retry scheduled event_id={item.event_id} retry={retry_count} delay_sec={delay_sec} error={ex}"
            )

    def run(self) -> None:
        self.try_load_model()

        cap = cv2.VideoCapture(self.args.camera_id)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.args.frame_width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.args.frame_height)

        if not cap.isOpened():
            raise RuntimeError("Could not open camera device")

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.on_connect = self.on_connect
        client.on_disconnect = self.on_disconnect
        client.tls_set(
            ca_certs=self.args.ca_cert,
            certfile=self.args.client_cert,
            keyfile=self.args.client_key,
            cert_reqs=ssl.CERT_REQUIRED,
            tls_version=ssl.PROTOCOL_TLS_CLIENT,
        )
        if self.args.insecure:
            client.tls_insecure_set(True)

        try:
            client.connect(self.args.broker_host, self.args.broker_port, keepalive=60)
            client.loop_start()
            print(f"[EDGE] Connected to broker: {self.args.broker_host}")
        except Exception as e:
            print(f"[EDGE] Initial connection failed ({e}). Entering offline mode.")

        print("[EDGE] Camera-motion pipeline running. Press Ctrl+C to stop.")

        blur_k = self.args.motion_blur_ksize
        if blur_k % 2 == 0:
            blur_k += 1  # kernel size must be odd
        prev_gray = None
        last_trigger_monotonic = 0.0

        try:
            while True:
                ret, frame = cap.read()
                if not ret or frame is None:
                    time.sleep(0.02)
                    self.drain_outbox(client)
                    continue

                gray = cv2.GaussianBlur(
                    cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (blur_k, blur_k), 0
                )

                if prev_gray is None:
                    prev_gray = gray
                    continue

                diff = cv2.absdiff(prev_gray, gray)
                prev_gray = gray  # rolling: always compare to previous frame

                _, thresh = cv2.threshold(
                    diff, self.args.motion_threshold, 255, cv2.THRESH_BINARY
                )
                contours, _ = cv2.findContours(
                    thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
                )
                motion_detected = any(
                    cv2.contourArea(c) >= self.args.motion_min_area_px for c in contours
                )

                now = time.monotonic()
                if motion_detected and (now - last_trigger_monotonic) >= self.args.debounce_sec:
                    trigger_started = time.perf_counter()
                    last_trigger_monotonic = now

                    image = frame.copy()
                    label, confidence = self.run_inference(image)
                    edge_reaction_ms = (time.perf_counter() - trigger_started) * 1000.0

                    if self.is_recyclable(label):
                        self.play_affirmative_sound()

                    file_name = f"motion_{datetime.utcnow().strftime('%Y%m%d_%H%M%S_%f')}.jpg"
                    image_path = os.path.join(self.args.capture_dir, file_name)
                    cv2.imwrite(image_path, image)

                    payload = self.build_event_payload(
                        image_ref=file_name,
                        label=label,
                        confidence=confidence,
                        edge_reaction_ms=edge_reaction_ms,
                    )
                    payload_text = encode_payload(payload)
                    self.outbox.enqueue(payload["event_id"], payload_text, image_path)
                    self.append_paso_event_row(payload)
                    print(
                        f"[EDGE] Queued event_id={payload['event_id']} label={payload['edge_pred_label']} conf={payload['edge_confidence']} edge_reaction_ms={payload['edge_reaction_ms']} pending={self.outbox.count_pending()}"
                    )
                else:
                    time.sleep(0.02)

                self.drain_outbox(client)

        except KeyboardInterrupt:
            print("\n[EDGE] Stopped by user.")
        finally:
            client.loop_stop()
            client.disconnect()
            cap.release()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Polling-based edge publisher (prototype comparison)")
    parser.add_argument("--broker-host", required=True)
    parser.add_argument("--broker-port", type=int, default=8883)
    parser.add_argument("--topic", default="edge/events/v1")
    parser.add_argument("--image-topic-prefix", default="edge/images/v1")
    parser.add_argument("--device-id", default="pi-edge-01")

    parser.add_argument("--ca-cert", required=True)
    parser.add_argument("--client-cert", required=True)
    parser.add_argument("--client-key", required=True)
    parser.add_argument("--insecure", action="store_true")

    parser.add_argument("--camera-id", type=int, default=0)
    parser.add_argument("--frame-width", type=int, default=640)
    parser.add_argument("--frame-height", type=int, default=480)
    parser.add_argument("--debounce-sec", type=float, default=1.0)
    parser.add_argument(
        "--motion-min-area-px",
        type=float,
        default=3000.0,
        help="Minimum contour area (pixels) in the thresholded diff image to count as motion.",
    )
    parser.add_argument(
        "--motion-threshold",
        type=int,
        default=25,
        help="Pixel intensity change threshold for the frame-diff binarisation (0-255).",
    )
    parser.add_argument(
        "--motion-blur-ksize",
        type=int,
        default=21,
        help="Gaussian blur kernel size applied before differencing (must be odd).",
    )

    parser.add_argument("--model-path", default="mobilenet_v2_1.0_224.tflite")
    parser.add_argument("--label-path", default="labels.txt")
    parser.add_argument("--edge-model-version", default="mobilenetv2-baseline")
    parser.add_argument("--capture-dir", default="captures")

    parser.add_argument("--recyclable-keywords", default="bottle,can,plastic,aluminum,tin")
    parser.add_argument("--sound-file", default="")
    parser.add_argument("--sound-device", default="")

    parser.add_argument("--outbox-db-path", default="data/pi_outbox_polling.db")
    parser.add_argument("--retry-base-sec", type=float, default=2.0)
    parser.add_argument("--max-retry-backoff-sec", type=float, default=60.0)
    parser.add_argument("--publish-timeout-sec", type=float, default=5.0)
    parser.add_argument("--max-image-bytes", type=int, default=400000)
    parser.add_argument("--delete-image-after-send", action="store_true")
    parser.add_argument(
        "--paso-log-csv",
        default="",
        help="Optional CSV file to append per-event PASO profiling rows (edge reaction timing).",
    )

    return parser


def main() -> None:
    args = build_parser().parse_args()
    app = PollingPublisherApp(args)
    app.run()


if __name__ == "__main__":
    main()
