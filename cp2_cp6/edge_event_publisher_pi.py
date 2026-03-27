import argparse
import csv
import os
import shutil
import ssl
import struct
import subprocess
import time
import uuid
from datetime import datetime
from typing import Optional, Tuple
import json

import cv2
import numpy as np
import paho.mqtt.client as mqtt
import serial

from event_schema import SUPPORTED_PAYLOAD_VERSION, encode_payload, utc_now_iso
from pi_outbox import PiOutbox

FRAME_HEADER = bytes([0xAA, 0xFF, 0x03, 0x00])
FRAME_TAIL = bytes([0x55, 0xCC])
FRAME_SIZE = 30
MAX_TARGETS = 3

TRIGGER_PROFILES = {
    "inside_bin": {
        "min_abs_speed": 65,
        "max_distance_cm": None,
    },
    "outside_bin": {
        "min_abs_speed": 70,
        "max_distance_cm": None,
    },
}


class EdgePublisherApp:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.motion_state = False
        self.serial_buffer = bytearray()
        self.last_trigger_monotonic = 0.0
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

    @staticmethod
    def decode_signed(raw: int) -> Optional[int]:
        if raw == 0:
            return None
        magnitude = raw & 0x7FFF
        return -magnitude if (raw & 0x8000) else magnitude

    def parse_mmwave_frame(self, frame: bytes) -> Tuple[bool, Optional[float], Optional[int]]:
        if len(frame) != FRAME_SIZE or frame[:4] != FRAME_HEADER or frame[-2:] != FRAME_TAIL:
            return False, None, None

        cfg = TRIGGER_PROFILES[self.args.trigger_mode]
        min_abs_speed = self.args.min_speed_cm_s
        if min_abs_speed is None:
            min_abs_speed = cfg["min_abs_speed"]

        max_distance_cm = self.args.max_distance_cm
        if max_distance_cm is None:
            max_distance_cm = cfg["max_distance_cm"]

        for i in range(MAX_TARGETS):
            offset = 4 + i * 8
            x_raw, y_raw, spd_raw, _ = struct.unpack_from("<4H", frame, offset)

            x_val = self.decode_signed(x_raw)
            y_val = self.decode_signed(y_raw)
            spd_val = self.decode_signed(spd_raw)

            # Follow prototype behavior: skip empty slot; otherwise require real movement speed.
            if x_val is None and y_val is None and spd_val is None:
                continue

            has_target = (x_raw != 0) or (y_raw != 0)
            if not has_target or spd_val is None:
                continue

            distance_cm = abs(y_val) / 10.0 if y_val is not None else None
            speed = spd_val

            if abs(speed) < float(min_abs_speed):
                continue

            if distance_cm is not None and max_distance_cm is not None and distance_cm > float(max_distance_cm):
                continue

            print(
                f"[SENSOR] Fast motion detected speed={speed} cm/s distance_cm={distance_cm} threshold={min_abs_speed}"
            )
            if distance_cm is None:
                return True, None, speed

            if max_distance_cm is None or distance_cm <= float(max_distance_cm):
                return True, distance_cm, speed

        return False, None, None

    def read_mmwave_frame(self, ser: serial.Serial) -> Optional[bytes]:
        if ser.in_waiting:
            self.serial_buffer.extend(ser.read(ser.in_waiting))

        while len(self.serial_buffer) >= 4 and self.serial_buffer[:4] != FRAME_HEADER:
            self.serial_buffer.pop(0)

        if len(self.serial_buffer) >= FRAME_SIZE:
            frame = bytes(self.serial_buffer[:FRAME_SIZE])
            del self.serial_buffer[:FRAME_SIZE]
            return frame
        return None

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
            cmd = ["aplay"]
            if self.args.sound_device:
                cmd.extend(["-D", self.args.sound_device])
            cmd.append(self.args.sound_file)
            subprocess.run(cmd, check=False)
            return

        print("[EDGE] 'aplay' not found. Skipping sound playback.")

    def on_connect(self, client, _userdata, _flags, reason_code, _properties) -> None:
        if reason_code == 0:
            self.connected = True
            print("[EDGE] Connected to MQTT broker.")
            client.subscribe(f"{self.args.ping_request_topic_prefix.rstrip('/')}/{self.args.device_id}", qos=1)
        else:
            self.connected = False
            print(f"[EDGE] MQTT connection failed: reason_code={reason_code}")

    def on_disconnect(self, client, _userdata, _flags, reason_code, _properties) -> None:
        self.connected = False
        print(f"[EDGE] Disconnected from MQTT broker: reason_code={reason_code}")

    def on_message(self, client, _userdata, msg: mqtt.MQTTMessage) -> None:
        expected_topic = f"{self.args.ping_request_topic_prefix.rstrip('/')}/{self.args.device_id}"
        if msg.topic != expected_topic:
            return

        request_id = ""
        sent_utc = ""
        try:
            body = json.loads(msg.payload.decode("utf-8", errors="strict"))
            request_id = str(body.get("request_id") or "")
            sent_utc = str(body.get("timestamp_utc") or "")
        except Exception:
            pass

        pong_topic = f"{self.args.ping_response_topic_prefix.rstrip('/')}/{self.args.device_id}"
        response = {
            "device_id": self.args.device_id,
            "request_id": request_id,
            "request_timestamp_utc": sent_utc,
            "response_timestamp_utc": utc_now_iso(),
        }
        client.publish(pong_topic, payload=json.dumps(response), qos=1, retain=False)

    def build_event_payload(
        self,
        image_ref: str,
        label: str,
        confidence: float,
        distance_cm: Optional[float],
        speed_cm_s: Optional[int],
        edge_reaction_ms: float,
    ) -> dict:
        return {
            "event_id": str(uuid.uuid4()),
            "device_id": self.args.device_id,
            "timestamp_utc": utc_now_iso(),
            "trigger_mode": self.args.trigger_mode,
            "edge_model_version": self.args.edge_model_version,
            "edge_pred_label": label,
            "edge_confidence": round(confidence, 4),
            "image_ref": image_ref,
            "payload_version": SUPPORTED_PAYLOAD_VERSION,
            "mmwave_distance_cm": None if distance_cm is None else round(distance_cm, 1),
            "mmwave_speed_cm_s": speed_cm_s,
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

        if self.args.publish_duplicate:
            dup_result = client.publish(self.args.topic, payload=payload_text, qos=1, retain=False)
            self._wait_publish(dup_result)
            print(f"[EDGE] Published duplicate metadata event_id={event_id} mid={dup_result.mid}")

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

        if self.args.publish_duplicate:
            dup_result = client.publish(image_topic, payload=image_bytes, qos=1, retain=False)
            self._wait_publish(dup_result)
            print(f"[EDGE] Published duplicate image event_id={event_id} mid={dup_result.mid}")

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

        ser = serial.Serial(self.args.mmwave_port, self.args.mmwave_baud, timeout=0.1)
        cap = cv2.VideoCapture(self.args.camera_id)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.args.frame_width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.args.frame_height)

        if not cap.isOpened():
            raise RuntimeError("Could not open camera device")

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.on_connect = self.on_connect
        client.on_disconnect = self.on_disconnect
        client.on_message = self.on_message
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

        print("[EDGE] CP2-CP4 pipeline running. Press Ctrl+C to stop.")

        try:
            latest_camera_frame = None
            while True:
                ret, camera_frame = cap.read()
                if ret:
                    latest_camera_frame = camera_frame

                latest = None
                while True:
                    frame = self.read_mmwave_frame(ser)
                    if frame is None:
                        break
                    latest = frame

                if latest is None:
                    is_now_active = self.motion_state
                    distance_cm = None
                    speed_cm_s = None
                else:
                    is_now_active, distance_cm, speed_cm_s = self.parse_mmwave_frame(latest)

                now = time.monotonic()

                if is_now_active:
                    is_rising_edge = not self.motion_state

                    if is_rising_edge and (now - self.last_trigger_monotonic) >= self.args.debounce_sec:
                        trigger_started = time.perf_counter()
                        self.last_trigger_monotonic = now

                        if latest_camera_frame is None:
                            print("[EDGE] Camera frame capture failed. Skipping trigger.")
                            self.motion_state = True
                            continue

                        image = latest_camera_frame.copy()

                        file_name = f"trigger_{datetime.utcnow().strftime('%Y%m%d_%H%M%S_%f')}.jpg"
                        image_path = os.path.join(self.args.capture_dir, file_name)
                        cv2.imwrite(image_path, image)

                        label, confidence = self.run_inference(image)
                        if self.is_recyclable(label):
                            self.play_affirmative_sound()

                        edge_reaction_ms = (time.perf_counter() - trigger_started) * 1000.0

                        payload = self.build_event_payload(
                            image_ref=file_name,
                            label=label,
                            confidence=confidence,
                            distance_cm=distance_cm,
                            speed_cm_s=speed_cm_s,
                            edge_reaction_ms=edge_reaction_ms,
                        )
                        payload_text = encode_payload(payload)
                        self.outbox.enqueue(payload["event_id"], payload_text, image_path)
                        self.append_paso_event_row(payload)
                        print(
                            f"[EDGE] Queued event_id={payload['event_id']} label={payload['edge_pred_label']} conf={payload['edge_confidence']} edge_reaction_ms={payload['edge_reaction_ms']} pending={self.outbox.count_pending()}"
                        )

                    self.motion_state = True
                else:
                    self.motion_state = False
                    time.sleep(0.02)

                self.drain_outbox(client)
        except KeyboardInterrupt:
            print("\n[EDGE] Stopped by user.")
        finally:
            client.loop_stop()
            client.disconnect()
            cap.release()
            ser.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CP2-CP4 edge publisher (Pi)")
    parser.add_argument("--broker-host", required=True)
    parser.add_argument("--broker-port", type=int, default=8883)
    parser.add_argument("--topic", default="edge/events/v1")
    parser.add_argument("--image-topic-prefix", default="edge/images/v1")
    parser.add_argument("--device-id", default="pi-edge-01")
    parser.add_argument("--ping-request-topic-prefix", default="edge/ping/request")
    parser.add_argument("--ping-response-topic-prefix", default="edge/ping/response")
    parser.add_argument("--trigger-mode", choices=["inside_bin", "outside_bin"], default="inside_bin")

    parser.add_argument("--ca-cert", required=True)
    parser.add_argument("--client-cert", required=True)
    parser.add_argument("--client-key", required=True)
    parser.add_argument("--insecure", action="store_true")

    parser.add_argument("--mmwave-port", default="/dev/ttyAMA0")
    parser.add_argument("--mmwave-baud", type=int, default=256000)
    parser.add_argument("--camera-id", type=int, default=0)
    parser.add_argument("--frame-width", type=int, default=640)
    parser.add_argument("--frame-height", type=int, default=480)
    parser.add_argument("--debounce-sec", type=float, default=1.0)
    parser.add_argument(
        "--min-speed-cm-s",
        type=float,
        default=None,
        help="Absolute speed threshold for trigger. Defaults: inside_bin=65, outside_bin=70.",
    )
    parser.add_argument(
        "--max-distance-cm",
        type=float,
        default=None,
        help="Optional max distance gate. Leave unset to disable distance filtering.",
    )

    parser.add_argument("--model-path", default="mobilenet_v2_1.0_224.tflite")
    parser.add_argument("--label-path", default="labels.txt")
    parser.add_argument("--edge-model-version", default="mobilenetv2-baseline")
    parser.add_argument("--capture-dir", default="captures")

    parser.add_argument("--recyclable-keywords", default="bottle,can,plastic,aluminum,tin")
    parser.add_argument("--sound-file", default="")
    parser.add_argument(
        "--sound-device",
        default="",
        help="Optional ALSA device for aplay, e.g. plughw:3,0. Leave empty to use system default.",
    )

    parser.add_argument(
        "--publish-duplicate",
        action="store_true",
        help="Publish each event twice to validate server-side deduplication",
    )
    parser.add_argument("--outbox-db-path", default="data/pi_outbox.db")
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
    app = EdgePublisherApp(args)
    app.run()


if __name__ == "__main__":
    main()
