import argparse
import json
import os
from typing import Any, Dict, Optional


def get_metric(report: Dict[str, Any], path: str) -> Optional[float]:
    cur: Any = report
    for key in path.split("."):
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    if cur is None:
        return None
    return float(cur)


def pct_delta(before: Optional[float], after: Optional[float]) -> Optional[float]:
    if before is None or after is None:
        return None
    if before == 0:
        return None
    return ((after - before) / before) * 100.0


def fmt(value: Optional[float], digits: int = 3) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare baseline vs after PASO analysis JSON reports")
    parser.add_argument("--before-json", required=True)
    parser.add_argument("--after-json", required=True)
    parser.add_argument("--output-md", required=True)
    args = parser.parse_args()

    with open(args.before_json, "r", encoding="utf-8") as f:
        before = json.load(f)
    with open(args.after_json, "r", encoding="utf-8") as f:
        after = json.load(f)

    # For CPU/RAM/power use Pi system metrics when available — that's what
    # actually changes between trigger approaches. Fall back to system (laptop)
    # if no Pi CSV was provided for a run.
    def sys_path(report: Dict, key: str) -> str:
        if report.get("system_pi"):
            return f"system_pi.{key}"
        return f"system.{key}"

    metrics = [
        ("Edge reaction p95 (ms)", "db.latency_ms.edge_reaction.p95", "lower"),
        ("Broker ingest p95 (ms)", "db.latency_ms.broker_ingest.p95", "lower"),
        ("Verification done p95 (ms)", "db.latency_ms.verification_done.p95", "lower"),
        ("Pi CPU mean (%)", sys_path(before, "cpu_percent_total.mean"), "lower"),
        ("Pi memory mean (%)", sys_path(before, "mem_percent_total.mean"), "lower"),
        ("Pi power proxy mean", sys_path(before, "power_proxy_score.mean"), "lower"),
        ("Agreement rate (%)", "db.agreement_rate_ok_percent", "higher"),
    ]

    lines = []
    lines.append("# PASO Comparison (Baseline vs After)")
    lines.append("")
    lines.append("| Metric | Baseline | After | Delta % | Direction |")
    lines.append("|---|---:|---:|---:|---|")

    for title, path, direction in metrics:
        b = get_metric(before, path)
        a = get_metric(after, path)
        d = pct_delta(b, a)
        lines.append(
            f"| {title} | {fmt(b)} | {fmt(a)} | {fmt(d)} | {direction} is better |"
        )

    lines.append("")
    lines.append("## Notes")
    lines.append("- Negative delta is good for latency/resource metrics.")
    lines.append("- Positive delta is good for agreement rate.")

    output_md = os.path.abspath(args.output_md)
    os.makedirs(os.path.dirname(output_md) or ".", exist_ok=True)
    with open(output_md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"[PASO] Comparison written: {output_md}")


if __name__ == "__main__":
    main()
