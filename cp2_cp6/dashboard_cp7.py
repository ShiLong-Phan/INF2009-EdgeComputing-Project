import argparse
import json
import os
import sqlite3
from typing import Dict, List, Optional, Tuple

from flask import Flask, abort, render_template, send_file


DEFAULT_DB_PATH = "data/edge_events.db"


def _normalize_label(label: Optional[str]) -> str:
    raw = (label or "").strip().lower()
    if not raw or raw == "unknown":
        return "UNKNOWN"
    if "bottle" in raw:
        return "BOTTLE"
    if "can" in raw:
        return "CAN"
    return "OTHER"


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
    counts: Dict[str, int] = {"BOTTLE": 0, "CAN": 0, "UNKNOWN": 0, "OTHER": 0}
    for row in rows:
        counts[_normalize_label(row["edge_pred_label"])] += 1

    ordered = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    result: List[Dict[str, object]] = []
    for label, count in ordered:
        if count <= 0:
            continue
        result.append(
            {
                "label": label,
                "count": count,
                "category": "Recyclable" if _is_recyclable(label) else "Non-Recyclable",
            }
        )
    return result


def _device_stats(rows: List[sqlite3.Row]) -> List[Dict[str, object]]:
    per_device: Dict[str, int] = {}
    for row in rows:
        device = row["device_id"]
        per_device[device] = per_device.get(device, 0) + 1

    ranked = sorted(per_device.items(), key=lambda x: x[1], reverse=True)
    return [{"device_id": d, "count": c} for d, c in ranked]


def create_app(db_path: str) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["DB_PATH"] = db_path

    @app.route("/")
    def dashboard_home():
        rows = _load_events(app.config["DB_PATH"])
        summary = _build_summary(rows)
        devices = _device_stats(rows)
        latest = rows[:25]
        return render_template(
            "dashboard_home.html",
            summary=summary,
            devices=devices,
            latest=latest,
        )

    @app.route("/device/<device_id>")
    def dashboard_device(device_id: str):
        rows = _load_events(app.config["DB_PATH"], device_id=device_id)
        if not rows:
            abort(404, description=f"No events found for device '{device_id}'")

        summary = _build_summary(rows)
        mix = _scan_mix(rows)
        latest = rows[:30]

        chart_labels = [m["label"] for m in mix]
        chart_counts = [m["count"] for m in mix]

        return render_template(
            "dashboard_device.html",
            device_id=device_id,
            summary=summary,
            mix=mix,
            latest=latest,
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

        image_path = row["image_path"]
        if not os.path.exists(image_path):
            abort(404, description="Image file missing on disk")

        return send_file(image_path)

    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CP7 multi-device Flask dashboard")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5050)
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app = create_app(args.db_path)
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
