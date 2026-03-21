import argparse
import os
import sqlite3
import ssl
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

from event_schema import decode_payload, validate_event_payload


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_db(db_path: str) -> None:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                timestamp_utc TEXT NOT NULL,
                trigger_mode TEXT NOT NULL,
                edge_model_version TEXT NOT NULL,
                edge_pred_label TEXT NOT NULL,
                edge_confidence REAL NOT NULL,
                image_ref TEXT,
                payload_version TEXT NOT NULL,
                raw_payload TEXT NOT NULL,
                first_seen_utc TEXT NOT NULL,
                last_seen_utc TEXT NOT NULL,
                receive_count INTEGER NOT NULL DEFAULT 1,
                verify_status TEXT NOT NULL DEFAULT 'pending',
                verify_label TEXT,
                verify_confidence REAL,
                verify_error TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp_utc)"
        )
        conn.commit()


def upsert_event(db_path: str, payload_text: str, payload: dict) -> bool:
    now_iso = utc_now_iso()

    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO events (
                event_id, device_id, timestamp_utc, trigger_mode,
                edge_model_version, edge_pred_label, edge_confidence,
                image_ref, payload_version, raw_payload,
                first_seen_utc, last_seen_utc, receive_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(event_id) DO UPDATE SET
                last_seen_utc = excluded.last_seen_utc,
                raw_payload = excluded.raw_payload,
                receive_count = events.receive_count + 1
            """,
            (
                payload["event_id"],
                payload["device_id"],
                payload["timestamp_utc"],
                payload["trigger_mode"],
                payload["edge_model_version"],
                payload["edge_pred_label"],
                float(payload["edge_confidence"]),
                payload.get("image_ref"),
                payload["payload_version"],
                payload_text,
                now_iso,
                now_iso,
            ),
        )

        cur.execute(
            "SELECT receive_count FROM events WHERE event_id = ?",
            (payload["event_id"],),
        )
        row = cur.fetchone()
        conn.commit()

    return bool(row and row[0] == 1)


class ReceiverApp:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args

    def on_connect(self, client, _userdata, _flags, reason_code, _properties) -> None:
        if reason_code == 0:
            print("[SRV] Connected to broker.")
            client.subscribe(self.args.topic, qos=1)
            print(f"[SRV] Subscribed topic={self.args.topic}")
        else:
            print(f"[SRV] Connect failed: reason_code={reason_code}")

    def on_message(self, _client, _userdata, msg: mqtt.MQTTMessage) -> None:
        payload_text = msg.payload.decode("utf-8", errors="strict")

        try:
            payload = decode_payload(payload_text)
        except Exception as ex:
            print(f"[SRV] Invalid JSON payload dropped: {ex}")
            return

        ok, reason = validate_event_payload(payload)
        if not ok:
            print(f"[SRV] Schema validation failed event dropped: {reason}")
            return

        is_new = upsert_event(self.args.db_path, payload_text, payload)
        status = "NEW" if is_new else "DUPLICATE"

        print(
            f"[SRV] {status} event_id={payload['event_id']} device={payload['device_id']} label={payload['edge_pred_label']} conf={payload['edge_confidence']}"
        )

    def run(self) -> None:
        ensure_db(self.args.db_path)

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.on_connect = self.on_connect
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

        print(f"[SRV] Connecting {self.args.broker_host}:{self.args.broker_port}")
        client.connect(self.args.broker_host, self.args.broker_port, keepalive=60)
        client.loop_forever()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CP2 receiver + dedup server")
    parser.add_argument("--broker-host", required=True)
    parser.add_argument("--broker-port", type=int, default=8883)
    parser.add_argument("--topic", default="edge/events/v1")

    parser.add_argument("--ca-cert", required=True)
    parser.add_argument("--client-cert", required=True)
    parser.add_argument("--client-key", required=True)
    parser.add_argument("--insecure", action="store_true")

    parser.add_argument("--db-path", default="data/edge_events.db")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    app = ReceiverApp(args)
    app.run()


if __name__ == "__main__":
    main()
