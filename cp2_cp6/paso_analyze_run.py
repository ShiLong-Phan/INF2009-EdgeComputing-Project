import argparse
import csv
import json
import math
import os
import sqlite3
from datetime import datetime
from statistics import mean, median
from typing import Dict, List, Optional


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def normalize_label(value: Optional[str]) -> str:
    text = (value or "").strip().upper()
    if text == "BOTTLE":
        return "BOTTLE"
    if text == "CAN":
        return "CAN"
    return "UNKNOWN"


def percentile(values: List[float], p: float) -> Optional[float]:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    values = sorted(values)
    pos = (len(values) - 1) * p
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return values[lo]
    frac = pos - lo
    return values[lo] * (1 - frac) + values[hi] * frac


def summarize(values: List[float]) -> Dict[str, Optional[float]]:
    if not values:
        return {"count": 0, "mean": None, "median": None, "p95": None}
    return {
        "count": len(values),
        "mean": round(mean(values), 3),
        "median": round(median(values), 3),
        "p95": round(percentile(values, 0.95) or 0.0, 3),
    }


def parse_edge_reaction_ms(raw_payload: str) -> Optional[float]:
    if not raw_payload:
        return None
    try:
        payload = json.loads(raw_payload)
        value = payload.get("edge_reaction_ms")
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def analyze_db(db_path: str) -> Dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """
        SELECT
            event_id,
            device_id,
            timestamp_utc,
            first_seen_utc,
            image_receive_utc,
            verify_done_utc,
            verify_status,
            edge_pred_label,
            verify_label,
            raw_payload
        FROM events
        WHERE receive_count >= 1
        """
    ).fetchall()

    status_rows = conn.execute(
        "SELECT verify_status, COUNT(*) AS c FROM events GROUP BY verify_status"
    ).fetchall()

    device_rows = conn.execute(
        "SELECT device_id, COUNT(*) AS c FROM events GROUP BY device_id ORDER BY c DESC"
    ).fetchall()

    conn.close()

    ingest_ms: List[float] = []
    image_ms: List[float] = []
    verify_ms: List[float] = []
    edge_reaction_ms: List[float] = []
    agree_ok = 0
    agree_total = 0

    for row in rows:
        t_edge = parse_iso(row["timestamp_utc"])
        t_first = parse_iso(row["first_seen_utc"])
        t_image = parse_iso(row["image_receive_utc"])
        t_verify = parse_iso(row["verify_done_utc"])

        if t_edge and t_first:
            ingest_ms.append((t_first - t_edge).total_seconds() * 1000.0)
        if t_edge and t_image:
            image_ms.append((t_image - t_edge).total_seconds() * 1000.0)
        if t_edge and t_verify:
            verify_ms.append((t_verify - t_edge).total_seconds() * 1000.0)

        edge_reaction = parse_edge_reaction_ms(row["raw_payload"])
        if edge_reaction is not None:
            edge_reaction_ms.append(edge_reaction)

        if row["verify_status"] == "ok":
            agree_total += 1
            if normalize_label(row["edge_pred_label"]) == normalize_label(row["verify_label"]):
                agree_ok += 1

    agreement_rate = None
    if agree_total > 0:
        agreement_rate = round((agree_ok / agree_total) * 100.0, 3)

    return {
        "event_count": len(rows),
        "verification_status_counts": {r["verify_status"]: r["c"] for r in status_rows},
        "events_by_device": {r["device_id"]: r["c"] for r in device_rows},
        "agreement_rate_ok_percent": agreement_rate,
        "latency_ms": {
            "edge_reaction": summarize(edge_reaction_ms),
            "broker_ingest": summarize(ingest_ms),
            "image_arrival": summarize(image_ms),
            "verification_done": summarize(verify_ms),
        },
    }


def analyze_system_csv(csv_path: str) -> Dict:
    cpu: List[float] = []
    mem: List[float] = []
    power_proxy: List[float] = []
    proc_cpu: List[float] = []
    proc_rss: List[float] = []

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("cpu_percent_total"):
                cpu.append(float(row["cpu_percent_total"]))
            if row.get("mem_percent_total"):
                mem.append(float(row["mem_percent_total"]))
            if row.get("power_proxy_score"):
                power_proxy.append(float(row["power_proxy_score"]))
            if row.get("proc_cpu_percent"):
                proc_cpu.append(float(row["proc_cpu_percent"]))
            if row.get("proc_rss_mb"):
                proc_rss.append(float(row["proc_rss_mb"]))

    return {
        "cpu_percent_total": summarize(cpu),
        "mem_percent_total": summarize(mem),
        "power_proxy_score": summarize(power_proxy),
        "proc_cpu_percent": summarize(proc_cpu),
        "proc_rss_mb": summarize(proc_rss),
    }


def build_findings(report: Dict) -> List[str]:
    findings: List[str] = []
    lat = report.get("db", {}).get("latency_ms", {})

    verify_p95 = lat.get("verification_done", {}).get("p95")
    if verify_p95 is not None and verify_p95 > 2500:
        findings.append("Verification latency p95 is high (>2500ms): likely network/cloud bottleneck.")

    edge_p95 = lat.get("edge_reaction", {}).get("p95")
    if edge_p95 is not None and edge_p95 > 300:
        findings.append("Edge reaction p95 is above 300ms: consider capture/inference optimizations.")

    sys_cpu_mean = report.get("system", {}).get("cpu_percent_total", {}).get("mean")
    if sys_cpu_mean is not None and sys_cpu_mean > 70:
        findings.append("Average CPU usage is high (>70%): prioritize CPU-bound optimization steps.")

    sys_mem_mean = report.get("system", {}).get("mem_percent_total", {}).get("mean")
    if sys_mem_mean is not None and sys_mem_mean > 75:
        findings.append("Average memory usage is high (>75%): inspect image buffering and object lifetimes.")

    if not findings:
        findings.append("No immediate critical bottleneck threshold was exceeded in this run.")

    return findings


def write_markdown(path: str, label: str, report: Dict) -> None:
    db = report.get("db", {})
    sys = report.get("system", {})
    lat = db.get("latency_ms", {})

    lines = []
    lines.append(f"# PASO Analysis Report - {label}")
    lines.append("")
    lines.append("## Summary")
    lines.append(f"- Total events: {db.get('event_count', 0)}")
    lines.append(f"- Agreement rate (verify_status=ok): {db.get('agreement_rate_ok_percent')}")
    lines.append(f"- Verify status counts: {db.get('verification_status_counts', {})}")
    lines.append("")

    lines.append("## Latency Metrics (ms)")
    lines.append("| Metric | Count | Mean | Median | P95 |")
    lines.append("|---|---:|---:|---:|---:|")
    for key in ["edge_reaction", "broker_ingest", "image_arrival", "verification_done"]:
        s = lat.get(key, {})
        lines.append(
            f"| {key} | {s.get('count', 0)} | {s.get('mean')} | {s.get('median')} | {s.get('p95')} |"
        )
    lines.append("")

    lines.append("## System Metrics")
    lines.append("| Metric | Count | Mean | Median | P95 |")
    lines.append("|---|---:|---:|---:|---:|")
    for key in ["cpu_percent_total", "mem_percent_total", "power_proxy_score", "proc_cpu_percent", "proc_rss_mb"]:
        s = sys.get(key, {})
        lines.append(
            f"| {key} | {s.get('count', 0)} | {s.get('mean')} | {s.get('median')} | {s.get('p95')} |"
        )
    lines.append("")

    lines.append("## Bottleneck Findings")
    for item in report.get("findings", []):
        lines.append(f"- {item}")
    lines.append("")

    lines.append("## Events By Device")
    for device_id, count in db.get("events_by_device", {}).items():
        lines.append(f"- {device_id}: {count}")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze one PASO run from SQLite + optional system CSV")
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--label", default="baseline")
    parser.add_argument("--system-csv", default="")
    parser.add_argument("--output-md", required=True)
    parser.add_argument("--output-json", default="")
    args = parser.parse_args()

    report = {
        "label": args.label,
        "db": analyze_db(args.db_path),
        "system": analyze_system_csv(args.system_csv) if args.system_csv else {},
    }
    report["findings"] = build_findings(report)

    output_md = os.path.abspath(args.output_md)
    os.makedirs(os.path.dirname(output_md) or ".", exist_ok=True)
    write_markdown(output_md, args.label, report)

    if args.output_json:
        output_json = os.path.abspath(args.output_json)
        os.makedirs(os.path.dirname(output_json) or ".", exist_ok=True)
        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

    print(f"[PASO] Analysis report written: {output_md}")


if __name__ == "__main__":
    main()
