import argparse
import csv
import os
import time
from datetime import datetime, timezone
from typing import Optional

import psutil


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_float_file(path: str, scale: float = 1.0) -> Optional[float]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read().strip()
        if not raw:
            return None
        return float(raw) / scale
    except Exception:
        return None


def read_cpu_temp_c() -> Optional[float]:
    return read_float_file("/sys/class/thermal/thermal_zone0/temp", scale=1000.0)


def read_cpu_freq_mhz() -> Optional[float]:
    # Linux cpufreq commonly reports kHz.
    return read_float_file("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq", scale=1000.0)


def resolve_process(pid: Optional[int], process_name: str) -> Optional[psutil.Process]:
    if pid is not None:
        try:
            return psutil.Process(pid)
        except Exception:
            return None

    if not process_name:
        return None

    lowered = process_name.lower()
    for proc in psutil.process_iter(attrs=["name", "cmdline"]):
        try:
            name = (proc.info.get("name") or "").lower()
            cmdline = " ".join(proc.info.get("cmdline") or []).lower()
            if lowered in name or lowered in cmdline:
                return proc
        except Exception:
            continue

    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="PASO system profiling sampler (CPU/RAM/net/power proxy)")
    parser.add_argument("--output-csv", required=True, help="Path to output CSV")
    parser.add_argument("--duration-sec", type=float, default=300.0, help="Total capture time")
    parser.add_argument("--interval-sec", type=float, default=1.0, help="Sampling interval")
    parser.add_argument("--label", default="baseline", help="Run label (baseline/after)")
    parser.add_argument("--pid", type=int, default=None, help="Optional process PID to monitor")
    parser.add_argument(
        "--process-name",
        default="",
        help="Optional process-name/substring to monitor if PID is not provided",
    )
    args = parser.parse_args()

    output_csv = os.path.abspath(args.output_csv)
    os.makedirs(os.path.dirname(output_csv) or ".", exist_ok=True)

    proc = resolve_process(args.pid, args.process_name)
    if proc is not None:
        try:
            proc.cpu_percent(interval=None)
        except Exception:
            proc = None

    net0 = psutil.net_io_counters()
    t0 = time.time()

    header = [
        "timestamp_utc",
        "elapsed_sec",
        "label",
        "cpu_percent_total",
        "mem_percent_total",
        "mem_available_mb",
        "disk_percent_root",
        "net_sent_mb_since_start",
        "net_recv_mb_since_start",
        "battery_percent",
        "battery_plugged",
        "cpu_temp_c",
        "cpu_freq_mhz",
        "power_proxy_score",
        "proc_pid",
        "proc_cpu_percent",
        "proc_rss_mb",
        "proc_vms_mb",
    ]

    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)

        while True:
            now = time.time()
            elapsed = now - t0
            if elapsed > args.duration_sec:
                break

            cpu_pct = psutil.cpu_percent(interval=None)
            vm = psutil.virtual_memory()
            disk = psutil.disk_usage("/")
            net = psutil.net_io_counters()
            battery = psutil.sensors_battery()
            cpu_temp = read_cpu_temp_c()
            cpu_freq_mhz = read_cpu_freq_mhz()

            power_proxy = None
            if cpu_freq_mhz is not None:
                power_proxy = round((cpu_pct * cpu_freq_mhz) / 1000.0, 3)
            else:
                power_proxy = round(cpu_pct, 3)

            proc_pid = ""
            proc_cpu = ""
            proc_rss_mb = ""
            proc_vms_mb = ""

            if proc is not None:
                try:
                    p_mem = proc.memory_info()
                    proc_pid = proc.pid
                    proc_cpu = proc.cpu_percent(interval=None)
                    proc_rss_mb = round(p_mem.rss / (1024 * 1024), 3)
                    proc_vms_mb = round(p_mem.vms / (1024 * 1024), 3)
                except Exception:
                    proc = None

            writer.writerow(
                [
                    utc_now_iso(),
                    round(elapsed, 3),
                    args.label,
                    round(cpu_pct, 3),
                    round(vm.percent, 3),
                    round(vm.available / (1024 * 1024), 3),
                    round(disk.percent, 3),
                    round((net.bytes_sent - net0.bytes_sent) / (1024 * 1024), 6),
                    round((net.bytes_recv - net0.bytes_recv) / (1024 * 1024), 6),
                    "" if battery is None else battery.percent,
                    "" if battery is None else int(bool(battery.power_plugged)),
                    "" if cpu_temp is None else round(cpu_temp, 3),
                    "" if cpu_freq_mhz is None else round(cpu_freq_mhz, 3),
                    power_proxy,
                    proc_pid,
                    proc_cpu,
                    proc_rss_mb,
                    proc_vms_mb,
                ]
            )

            sleep_for = max(0.05, args.interval_sec)
            time.sleep(sleep_for)

    print(f"[PASO] Profiling capture written: {output_csv}")


if __name__ == "__main__":
    main()
