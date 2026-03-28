import argparse
import json
import os
import sqlite3
import ssl
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import paho.mqtt.client as mqtt
from flask import Flask, abort, jsonify, render_template, send_file

from event_schema import utc_now_iso


DEFAULT_DB_PATH = "data/edge_events.db"
DEFAULT_ONLINE_WINDOW_SEC = 180
DEFAULT_BROKER_PORT = 8883
DEFAULT_PING_REQUEST_TOPIC_PREFIX = "edge/ping/request"
DEFAULT_PING_RESPONSE_TOPIC_PREFIX = "edge/ping/response"
DEFAULT_PING_TIMEOUT_SEC = 3.0
GMT_PLUS_8 = timezone(timedelta(hours=8))


def _resolve_existing_image_path(image_path: str, db_path: str) -> Optional[str]:
    raw = (image_path or "").strip()
    if not raw:
        return None

    candidates: List[str] = []
    if os.path.isabs(raw):
        candidates.append(raw)
    else:
        db_abs = os.path.abspath(db_path)
        db_dir = os.path.dirname(db_abs)
        db_parent = os.path.dirname(db_dir) if db_dir else db_dir
        module_dir = os.path.dirname(os.path.abspath(__file__))

        # Try common roots so existing DB rows remain readable across launch locations.
        candidates.extend(
            [
                os.path.abspath(raw),
                os.path.abspath(os.path.join(db_dir, raw)),
                os.path.abspath(os.path.join(db_parent, raw)),
                os.path.abspath(os.path.join(module_dir, raw)),
                os.path.abspath(os.path.join(module_dir, "..", raw)),
            ]
        )

    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if os.path.exists(candidate):
            return candidate
    return None


def _normalize_label(label: Optional[str]) -> str:
    raw = (label or "").strip().lower()
    if not raw or raw == "unknown":
        return "UNKNOWN"
    if "bottle" in raw:
        return "BOTTLE"
    if "can" in raw:
        return "CAN"
    return "UNKNOWN"


def _is_recyclable(normalized_label: str) -> bool:
    return normalized_label in {"BOTTLE", "CAN"}


def _db_connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _load_events(db_path: str, device_id: Optional[str] = None) -> List[sqlite3.Row]:
    where = "WHERE device_id != 'unknown'"
    params: Tuple[object, ...] = ()

    if device_id:
        where += " AND device_id = ?"
        params = (device_id,)

    query = f"""
        SELECT
            event_id,
            device_id,
            timestamp_utc,
            edge_pred_label,
            edge_confidence,
            verify_status,
            verify_label,
            verify_error,
            image_path,
            receive_count
        FROM events
        {where}
        ORDER BY timestamp_utc DESC
    """

    with _db_connect(db_path) as conn:
        return conn.execute(query, params).fetchall()


def _build_summary(rows: List[sqlite3.Row]) -> Dict[str, object]:
    total = len(rows)
    verified_ok = sum(1 for r in rows if (r["verify_status"] or "") == "ok")
    verify_pending = sum(1 for r in rows if (r["verify_status"] or "") == "pending")
    verify_error = sum(1 for r in rows if (r["verify_status"] or "") == "error")
    verify_skipped = sum(1 for r in rows if (r["verify_status"] or "") == "skipped")

    # Agreement uses rows where cloud verification is available.
    agreement_den = 0
    agreement_num = 0
    for row in rows:
        if (row["verify_status"] or "") != "ok":
            continue
        verify_label = _normalize_label(row["verify_label"])
        if verify_label not in {"BOTTLE", "CAN", "UNKNOWN"}:
            continue
        agreement_den += 1
        if _normalize_label(row["edge_pred_label"]) == verify_label:
            agreement_num += 1

    agreement_rate = round((agreement_num / agreement_den) * 100.0, 2) if agreement_den else None

    return {
        "total": total,
        "verified_ok": verified_ok,
        "verify_pending": verify_pending,
        "verify_error": verify_error,
        "verify_skipped": verify_skipped,
        "agreement_rate": agreement_rate,
        "agreement_samples": agreement_den,
    }


def _scan_mix(rows: List[sqlite3.Row]) -> List[Dict[str, object]]:
    counts: Dict[str, int] = {"BOTTLE": 0, "CAN": 0, "MISMATCH": 0, "UNKNOWN": 0}
    for row in rows:
        edge_label = _normalize_label(row["edge_pred_label"])
        verify_status = (row["verify_status"] or "").strip().lower()
        cloud_label = _normalize_label(row["verify_label"])

        # For chart/list reporting, bottle/can must be cloud-confirmed; otherwise mark mismatch.
        if edge_label in {"BOTTLE", "CAN"}:
            if verify_status == "ok" and cloud_label == edge_label:
                counts[edge_label] += 1
            else:
                counts["MISMATCH"] += 1
            continue

        counts["UNKNOWN"] += 1

    ordered = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    result: List[Dict[str, object]] = []
    for label, count in ordered:
        if count <= 0:
            continue
        result.append(
            {
                "label": label,
                "count": count,
                "category": (
                    "Recyclable"
                    if _is_recyclable(label)
                    else "Mismatch"
                    if label == "MISMATCH"
                    else "Non-Recyclable"
                ),
            }
        )
    return result


def _device_stats(rows: List[sqlite3.Row]) -> List[Dict[str, object]]:
    per_device: Dict[str, Dict[str, object]] = {}
    for row in rows:
        device = row["device_id"]
        stats = per_device.setdefault(
            device,
            {
                "count": 0,
                "last_seen_utc": None,
            },
        )
        stats["count"] = int(stats["count"]) + 1

        ts = row["timestamp_utc"]
        if ts and (stats["last_seen_utc"] is None or ts > stats["last_seen_utc"]):
            stats["last_seen_utc"] = ts

    ranked = sorted(
        per_device.items(),
        key=lambda x: int(x[1]["count"]),
        reverse=True,
    )
    return [
        {
            "device_id": d,
            "count": int(info["count"]),
            "last_seen_utc": info["last_seen_utc"],
        }
        for d, info in ranked
    ]


def _parse_utc_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        normalized = ts.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=GMT_PLUS_8)
        return parsed.astimezone(GMT_PLUS_8)
    except ValueError:
        return None


def _attach_presence(devices: List[Dict[str, object]], online_window_sec: int) -> List[Dict[str, object]]:
    now_utc = datetime.now(GMT_PLUS_8)
    result: List[Dict[str, object]] = []

    for device in devices:
        last_seen_raw = device.get("last_seen_utc")
        last_seen_dt = _parse_utc_iso(last_seen_raw if isinstance(last_seen_raw, str) else None)

        seconds_since_last_seen: Optional[int] = None
        status = "offline"
        if last_seen_dt is not None:
            delta_sec = max(0, int((now_utc - last_seen_dt).total_seconds()))
            seconds_since_last_seen = delta_sec
            if delta_sec <= int(online_window_sec):
                status = "online"

        row = dict(device)
        row["status"] = status
        row["seconds_since_last_seen"] = seconds_since_last_seen
        result.append(row)

    return result


def _presence_summary(devices: List[Dict[str, object]]) -> Dict[str, int]:
    online = sum(1 for d in devices if d.get("status") == "online")
    offline = sum(1 for d in devices if d.get("status") == "offline")
    return {"online": online, "offline": offline}


def _ping_device_over_mqtt(app: Flask, device_id: str) -> Tuple[bool, Dict[str, object]]:
    request_id = str(uuid.uuid4())
    request_topic = f"{app.config['PING_REQUEST_TOPIC_PREFIX'].rstrip('/')}/{device_id}"
    response_topic = f"{app.config['PING_RESPONSE_TOPIC_PREFIX'].rstrip('/')}/{device_id}"

    done = threading.Event()
    start_monotonic = time.monotonic()
    result: Dict[str, object] = {
        "ok": False,
        "device_id": device_id,
        "request_id": request_id,
        "timeout_sec": app.config["PING_TIMEOUT_SEC"],
    }

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

    def on_connect(_client, _userdata, _flags, reason_code, _properties) -> None:
        if reason_code != 0:
            result["error"] = f"connect_failed:{reason_code}"
            done.set()
            return
        _client.subscribe(response_topic, qos=1)

        payload = json.dumps({"request_id": request_id, "timestamp_utc": utc_now_iso()})
        _client.publish(request_topic, payload=payload, qos=1, retain=False)

    def on_message(_client, _userdata, msg: mqtt.MQTTMessage) -> None:
        try:
            body = json.loads(msg.payload.decode("utf-8", errors="strict"))
        except Exception:
            return

        if body.get("request_id") != request_id:
            return

        latency_ms = int((time.monotonic() - start_monotonic) * 1000)
        result.update(
            {
                "ok": True,
                "latency_ms": latency_ms,
                "response": body,
            }
        )
        done.set()

    client.on_connect = on_connect
    client.on_message = on_message

    try:
        ca_cert = (app.config.get("CA_CERT") or "").strip()
        client_cert = (app.config.get("CLIENT_CERT") or "").strip()
        client_key = (app.config.get("CLIENT_KEY") or "").strip()

        if ca_cert and client_cert and client_key:
            client.tls_set(
                ca_certs=ca_cert,
                certfile=client_cert,
                keyfile=client_key,
                cert_reqs=ssl.CERT_REQUIRED,
                tls_version=ssl.PROTOCOL_TLS_CLIENT,
            )
            if app.config.get("INSECURE"):
                client.tls_insecure_set(True)

        client.connect(app.config["BROKER_HOST"], int(app.config["BROKER_PORT"]), keepalive=30)
        client.loop_start()
        done.wait(timeout=float(app.config["PING_TIMEOUT_SEC"]))
    except Exception as ex:
        result["error"] = str(ex)
    finally:
        client.loop_stop()
        try:
            client.disconnect()
        except Exception:
            pass

    if not result.get("ok") and "error" not in result:
        result["error"] = "timeout"

    return bool(result.get("ok")), result


def _edge_cloud_match_status(row: sqlite3.Row) -> str:
    verify_status = (row["verify_status"] or "").strip().lower()
    if verify_status != "ok":
        return "N/A"

    edge_label = _normalize_label(row["edge_pred_label"])
    cloud_label = _normalize_label(row["verify_label"])
    return "MATCH" if edge_label == cloud_label else "MISMATCH"


def _prepare_latest_rows(rows: List[sqlite3.Row], limit: int) -> List[Dict[str, object]]:
    latest: List[Dict[str, object]] = []
    for row in rows[:limit]:
        row_dict = dict(row)
        row_dict["match_status"] = _edge_cloud_match_status(row)
        latest.append(row_dict)
    return latest


def create_app(db_path: str) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["DB_PATH"] = os.path.abspath(db_path)
    app.config["ONLINE_WINDOW_SEC"] = DEFAULT_ONLINE_WINDOW_SEC

    @app.route("/")
    def dashboard_home():
        rows = _load_events(app.config["DB_PATH"])
        summary = _build_summary(rows)
        devices = _attach_presence(_device_stats(rows), int(app.config["ONLINE_WINDOW_SEC"]))
        presence = _presence_summary(devices)
        latest = _prepare_latest_rows(rows, limit=25)
        return render_template(
            "dashboard_home.html",
            summary=summary,
            devices=devices,
            presence=presence,
            online_window_sec=app.config["ONLINE_WINDOW_SEC"],
            latest=latest,
        )

    @app.post("/api/ping/<device_id>")
    def api_ping(device_id: str):
        ok, payload = _ping_device_over_mqtt(app, device_id)
        if ok:
            return jsonify(payload), 200
        if payload.get("error") == "timeout":
            return jsonify(payload), 504
        return jsonify(payload), 500

    @app.route("/device/<device_id>")
    def dashboard_device(device_id: str):
        rows = _load_events(app.config["DB_PATH"], device_id=device_id)
        if not rows:
            abort(404, description=f"No events found for device '{device_id}'")

        summary = _build_summary(rows)
        mix = _scan_mix(rows)
        latest = _prepare_latest_rows(rows, limit=30)
        device_presence = _attach_presence(
            [{"device_id": device_id, "count": len(rows), "last_seen_utc": rows[0]["timestamp_utc"]}],
            int(app.config["ONLINE_WINDOW_SEC"]),
        )[0]

        chart_labels = [m["label"] for m in mix]
        chart_counts = [m["count"] for m in mix]

        return render_template(
            "dashboard_device.html",
            device_id=device_id,
            summary=summary,
            mix=mix,
            latest=latest,
            device_presence=device_presence,
            online_window_sec=app.config["ONLINE_WINDOW_SEC"],
            chart_labels=json.dumps(chart_labels),
            chart_counts=json.dumps(chart_counts),
        )

    @app.route("/image/<event_id>")
    def view_image(event_id: str):
        with _db_connect(app.config["DB_PATH"]) as conn:
            row = conn.execute(
                "SELECT image_path FROM events WHERE event_id = ?",
                (event_id,),
            ).fetchone()

        if row is None or not row["image_path"]:
            abort(404, description="Image not found")

        image_path = _resolve_existing_image_path(row["image_path"], app.config["DB_PATH"])
        if image_path is None:
            abort(404, description="Image file missing on disk")

        return send_file(image_path)

    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CP7 multi-device Flask dashboard")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--broker-host", default="DOMCOM2")
    parser.add_argument("--broker-port", type=int, default=DEFAULT_BROKER_PORT)
    parser.add_argument("--ca-cert", default="certs/ca.crt")
    parser.add_argument("--client-cert", default="certs/laptop-client.crt")
    parser.add_argument("--client-key", default="certs/laptop-client.key")
    parser.add_argument("--insecure", action="store_true")
    parser.add_argument("--ping-request-topic-prefix", default=DEFAULT_PING_REQUEST_TOPIC_PREFIX)
    parser.add_argument("--ping-response-topic-prefix", default=DEFAULT_PING_RESPONSE_TOPIC_PREFIX)
    parser.add_argument("--ping-timeout-sec", type=float, default=DEFAULT_PING_TIMEOUT_SEC)
    parser.add_argument("--online-window-sec", type=int, default=DEFAULT_ONLINE_WINDOW_SEC)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5050)
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app = create_app(args.db_path)
    app.config["ONLINE_WINDOW_SEC"] = max(10, int(args.online_window_sec))
    app.config["BROKER_HOST"] = args.broker_host
    app.config["BROKER_PORT"] = int(args.broker_port)
    app.config["CA_CERT"] = os.path.abspath(args.ca_cert) if args.ca_cert else ""
    app.config["CLIENT_CERT"] = os.path.abspath(args.client_cert) if args.client_cert else ""
    app.config["CLIENT_KEY"] = os.path.abspath(args.client_key) if args.client_key else ""
    app.config["INSECURE"] = bool(args.insecure)
    app.config["PING_REQUEST_TOPIC_PREFIX"] = args.ping_request_topic_prefix
    app.config["PING_RESPONSE_TOPIC_PREFIX"] = args.ping_response_topic_prefix
    app.config["PING_TIMEOUT_SEC"] = max(0.5, float(args.ping_timeout_sec))
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
