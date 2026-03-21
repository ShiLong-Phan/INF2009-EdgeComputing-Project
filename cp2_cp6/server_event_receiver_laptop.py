import argparse
import os
import sqlite3
import ssl
from datetime import datetime, timezone
from typing import Optional

import paho.mqtt.client as mqtt

from event_schema import decode_payload, validate_event_payload
from nanogpt_verifier import verify_image


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
                verify_error TEXT,
                verify_raw_text TEXT,
                image_path TEXT,
                image_status TEXT NOT NULL DEFAULT 'pending',
                image_receive_utc TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp_utc)"
        )
        _ensure_column(conn, "events", "verify_raw_text", "TEXT")
        _ensure_column(conn, "events", "image_path", "TEXT")
        _ensure_column(conn, "events", "image_status", "TEXT NOT NULL DEFAULT 'pending'")
        _ensure_column(conn, "events", "image_receive_utc", "TEXT")
        conn.commit()


def _ensure_column(conn: sqlite3.Connection, table: str, col_name: str, col_type: str) -> None:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    cols = {row[1] for row in rows}
    if col_name not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")


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
                first_seen_utc, last_seen_utc, receive_count, image_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(event_id) DO UPDATE SET
                device_id = excluded.device_id,
                timestamp_utc = excluded.timestamp_utc,
                trigger_mode = excluded.trigger_mode,
                edge_model_version = excluded.edge_model_version,
                edge_pred_label = excluded.edge_pred_label,
                edge_confidence = excluded.edge_confidence,
                image_ref = excluded.image_ref,
                payload_version = excluded.payload_version,
                last_seen_utc = excluded.last_seen_utc,
                raw_payload = excluded.raw_payload,
                receive_count = events.receive_count + 1,
                image_path = COALESCE(events.image_path, excluded.image_path)
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
                payload.get("image_ref"),
            ),
        )

        cur.execute(
            "SELECT receive_count FROM events WHERE event_id = ?",
            (payload["event_id"],),
        )
        row = cur.fetchone()
        conn.commit()

    return bool(row and row[0] == 1)


def upsert_image(db_path: str, event_id: str, image_path: str) -> None:
    now_iso = utc_now_iso()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO events (
                event_id, device_id, timestamp_utc, trigger_mode,
                edge_model_version, edge_pred_label, edge_confidence,
                image_ref, payload_version, raw_payload,
                first_seen_utc, last_seen_utc, receive_count,
                image_path, image_status, image_receive_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'received', ?)
            ON CONFLICT(event_id) DO UPDATE SET
                image_path = excluded.image_path,
                image_status = 'received',
                image_receive_utc = excluded.image_receive_utc,
                last_seen_utc = excluded.last_seen_utc
            """,
            (
                event_id,
                "unknown",
                now_iso,
                "inside_bin",
                "unknown",
                "unknown",
                0.0,
                os.path.basename(image_path),
                "1.0",
                "{}",
                now_iso,
                now_iso,
                image_path,
                now_iso,
            ),
        )
        conn.commit()


def get_verification_candidate(db_path: str, event_id: str) -> Optional[sqlite3.Row]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT event_id, verify_status, image_path
            FROM events
            WHERE event_id = ?
            """,
            (event_id,),
        ).fetchone()
    return row


def mark_verify_result(
    db_path: str,
    event_id: str,
    status: str,
    label: Optional[str],
    confidence: Optional[float],
    error_text: Optional[str],
    raw_text: Optional[str],
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE events
            SET verify_status = ?,
                verify_label = ?,
                verify_confidence = ?,
                verify_error = ?,
                verify_raw_text = ?
            WHERE event_id = ?
            """,
            (
                status,
                label,
                confidence,
                error_text,
                raw_text,
                event_id,
            ),
        )
        conn.commit()


class ReceiverApp:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.args.db_path = os.path.abspath(self.args.db_path)
        self.args.image_store_dir = os.path.abspath(self.args.image_store_dir)
        os.makedirs(self.args.image_store_dir, exist_ok=True)

    def _is_image_topic(self, topic: str) -> bool:
        return topic.startswith(self.args.image_topic_prefix.rstrip("/") + "/")

    def _topic_event_id(self, topic: str) -> Optional[str]:
        prefix = self.args.image_topic_prefix.rstrip("/") + "/"
        if not topic.startswith(prefix):
            return None
        event_id = topic[len(prefix):].strip()
        return event_id or None

    def _try_verify(self, event_id: str) -> None:
        candidate = get_verification_candidate(self.args.db_path, event_id)
        if candidate is None:
            return

        # DEDUP: If it's already verified (ok) or skipped, don't call the API again.
        if candidate["verify_status"] in ("ok", "skipped"):
            print(f"[SRV] Skipping verification for event_id={event_id} (Already {candidate['verify_status']})")
            return

        image_path = candidate["image_path"]
        if not image_path or not os.path.exists(image_path):
            return

        if not self.args.nanogpt_api_key or self.args.nanogpt_api_key.strip() == "":
            mark_verify_result(
                self.args.db_path,
                event_id,
                "skipped",
                "UNVERIFIED",
                None,
                "No API key provided",
                "STUB_RESPONSE",
            )
            print(f"[SRV] Verification skipped for event_id={event_id} (No API key)")
            return

        try:
            label, confidence, raw_text = verify_image(
                api_key=self.args.nanogpt_api_key,
                image_path=image_path,
                model=self.args.nanogpt_model,
            )
            mark_verify_result(
                self.args.db_path,
                event_id,
                "ok",
                label,
                confidence,
                None,
                raw_text,
            )
            print(f"[SRV] Verify ok event_id={event_id} label={label}")
        except Exception as ex:
            import traceback
            error_details = f"{str(ex)}\n{traceback.format_exc()}"
            mark_verify_result(
                self.args.db_path,
                event_id,
                "error",
                None,
                None,
                str(ex),
                None,
            )
            print(f"[SRV] Verify error event_id={event_id} err={error_details}")

    def on_connect(self, client, _userdata, _flags, reason_code, _properties) -> None:
        if reason_code == 0:
            print("[SRV] Connected to broker.")
            client.subscribe(self.args.topic, qos=1)
            client.subscribe(self.args.image_topic_prefix.rstrip("/") + "/#", qos=1)
            print(f"[SRV] Subscribed topic={self.args.topic}")
            print(f"[SRV] Subscribed image_topic_prefix={self.args.image_topic_prefix}")
        else:
            print(f"[SRV] Connect failed: reason_code={reason_code}")

    def on_message(self, _client, _userdata, msg: mqtt.MQTTMessage) -> None:
        if self._is_image_topic(msg.topic):
            event_id = self._topic_event_id(msg.topic)
            if not event_id:
                print(f"[SRV] Invalid image topic: {msg.topic}")
                return

            image_path = os.path.join(self.args.image_store_dir, f"{event_id}.jpg")
            with open(image_path, "wb") as image_file:
                image_file.write(msg.payload)

            upsert_image(self.args.db_path, event_id, image_path)
            print(f"[SRV] IMAGE received event_id={event_id} bytes={len(msg.payload)}")
            self._try_verify(event_id)
            return

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
        self._try_verify(payload["event_id"])

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
    parser.add_argument("--image-topic-prefix", default="edge/images/v1")

    parser.add_argument("--ca-cert", required=True)
    parser.add_argument("--client-cert", required=True)
    parser.add_argument("--client-key", required=True)
    parser.add_argument("--insecure", action="store_true")

    parser.add_argument("--db-path", default="data/edge_events.db")
    parser.add_argument("--image-store-dir", default="data/images")
    parser.add_argument("--nanogpt-model", default="qwen3.5-27b-vision")
    parser.add_argument(
        "--nanogpt-api-key",
        default=os.getenv("NANOGPT_API_KEY", ""),
        help="NanoGPT API key. Defaults to NANOGPT_API_KEY env var.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    app = ReceiverApp(args)
    app.run()


if __name__ == "__main__":
    main()
