# PASO Analysis Report - baseline_filtered

## Summary
- Total events: 33
- Agreement rate (verify_status=ok): 63.636
- Verify status counts: {'ok': 33}

## Latency Metrics (ms)
| Metric | Count | Mean | Median | P95 |
|---|---:|---:|---:|---:|
| edge_reaction | 33 | 1143.512 | 1552.27 | 1578.676 |
| broker_ingest | 33 | 1243.013 | 269.958 | 4906.219 |
| image_arrival | 33 | 1332.92 | 410.504 | 4918.355 |
| verification_done | 33 | 4580.287 | 3877.013 | 8054.327 |

## System Metrics
| Metric | Count | Mean | Median | P95 |
|---|---:|---:|---:|---:|
| cpu_percent_total | 298 | 3.743 | 2.65 | 10.0 |
| mem_percent_total | 298 | 61.014 | 60.9 | 61.5 |
| power_proxy_score | 298 | 3.743 | 2.65 | 10.0 |
| proc_cpu_percent | 298 | 0.0 | 0.0 | 0.0 |
| proc_rss_mb | 298 | 5.045 | 5.043 | 5.059 |

## Bottleneck Findings
- Verification latency p95 is high (>2500ms): likely network/cloud bottleneck.
- Edge reaction p95 is above 300ms: consider capture/inference optimizations.

## Events By Device
- pi-edge-01: 33
