import argparse
import os
import shutil
import ssl
import struct
import subprocess
import time
import uuid
from datetime import datetime
from typing import Optional, Tuple

import cv2
import numpy as np
import paho.mqtt.client as mqtt
import serial

from event_schema import SUPPORTED_PAYLOAD_VERSION, encode_payload, utc_now_iso

FRAME_HEADER = bytes([0xAA, 0xFF, 0x03, 0x00])
FRAME_SIZE = 30
MAX_TARGETS = 3

TRIGGER_PROFILES = {
    "inside_bin": {
        "min_abs_speed": 3,
        "max_distance_cm": 140,
    },
    "outside_bin": {
        "min_abs_speed": 8,
        "max_distance_cm": 250,
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

        os.makedirs(self.args.capture_dir, exist_ok=True)

    @staticmethod
    def decode_signed(raw: int) -> Optional[int]:
        if raw == 0:
            return None
        magnitude = raw & 0x7FFF
        return -magnitude if (raw & 0x8000) else magnitude

    def parse_mmwave_frame(self, frame: bytes) -> Tuple[bool, Optional[float], Optional[int]]:
        if len(frame) != FRAME_SIZE or frame[:4] != FRAME_HEADER:
            return False, None, None

        cfg = TRIGGER_PROFILES[self.args.trigger_mode]

        for i in range(MAX_TARGETS):
            offset = 4 + i * 8
            x_raw, y_raw, spd_raw, _ = struct.unpack_from("<4H", frame, offset)

            x_val = self.decode_signed(x_raw)
            y_val = self.decode_signed(y_raw)
            spd_val = self.decode_signed(spd_raw)

            if x_val is None and y_val is None and spd_val is None:
                continue

            if y_val is None or spd_val is None:
                continue

            distance_cm = abs(y_val) / 10.0
            speed = spd_val

            if abs(speed) >= cfg["min_abs_speed"] and distance_cm <= cfg["max_distance_cm"]:
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
            subprocess.run(["aplay", self.args.sound_file], check=False)
            return

        print("[EDGE] 'aplay' not found. Skipping sound playback.")

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
        distance_cm: Optional[float],
        speed_cm_s: Optional[int],
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
        }

    def publish_event(self, client: mqtt.Client, payload: dict) -> None:
        payload_text = encode_payload(payload)
        result = client.publish(self.args.topic, payload=payload_text, qos=1, retain=False)
        print(
            f"[EDGE] Published event_id={payload['event_id']} mid={result.mid} label={payload['edge_pred_label']} conf={payload['edge_confidence']}"
        )

        if self.args.publish_duplicate:
            dup_result = client.publish(self.args.topic, payload=payload_text, qos=1, retain=False)
            print(f"[EDGE] Published duplicate for testing mid={dup_result.mid}")

    def run(self) -> None:
        self.try_load_model()

        ser = serial.Serial(self.args.mmwave_port, self.args.mmwave_baud, timeout=0.1)
        cap = cv2.VideoCapture(self.args.camera_id)
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

        client.connect(self.args.broker_host, self.args.broker_port, keepalive=60)
        client.loop_start()

        print("[EDGE] CP2-CP4 pipeline running. Press Ctrl+C to stop.")

        try:
            while True:
                latest = None
                while True:
                    frame = self.read_mmwave_frame(ser)
                    if frame is None:
                        break
                    latest = frame

                if latest is None:
                    time.sleep(0.02)
                    continue

                active, distance_cm, speed_cm_s = self.parse_mmwave_frame(latest)
                now = time.monotonic()

                if active and (now - self.last_trigger_monotonic) >= self.args.debounce_sec:
                    self.last_trigger_monotonic = now

                    ret, image = cap.read()
                    if not ret:
                        print("[EDGE] Camera frame capture failed. Skipping trigger.")
                        continue

                    file_name = f"trigger_{datetime.utcnow().strftime('%Y%m%d_%H%M%S_%f')}.jpg"
                    image_path = os.path.join(self.args.capture_dir, file_name)
                    cv2.imwrite(image_path, image)

                    label, confidence = self.run_inference(image)
                    if self.is_recyclable(label):
                        self.play_affirmative_sound()

                    payload = self.build_event_payload(
                        image_ref=file_name,
                        label=label,
                        confidence=confidence,
                        distance_cm=distance_cm,
                        speed_cm_s=speed_cm_s,
                    )
                    self.publish_event(client, payload)
                else:
                    time.sleep(0.02)
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
    parser.add_argument("--device-id", default="pi-edge-01")
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

    parser.add_argument("--model-path", default="mobilenet_v2_1.0_224.tflite")
    parser.add_argument("--label-path", default="labels.txt")
    parser.add_argument("--edge-model-version", default="mobilenetv2-baseline")
    parser.add_argument("--capture-dir", default="captures")

    parser.add_argument("--recyclable-keywords", default="bottle,can,plastic,aluminum,tin")
    parser.add_argument("--sound-file", default="")

    parser.add_argument(
        "--publish-duplicate",
        action="store_true",
        help="Publish each event twice to validate server-side deduplication",
    )

    return parser


def main() -> None:
    args = build_parser().parse_args()
    app = EdgePublisherApp(args)
    app.run()


if __name__ == "__main__":
    main()
