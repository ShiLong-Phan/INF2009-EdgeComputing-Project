# PASO Analysis Report - baseline

## Summary
- Total events: 25
- Agreement rate (verify_status=ok): 64.0
- Verify status counts: {'ok': 25}

## Latency Metrics (ms)
| Metric | Count | Mean | Median | P95 |
|---|---:|---:|---:|---:|
| edge_reaction | 25 | 34.395 | 32.69 | 46.824 |
| broker_ingest | 25 | 1069.943 | 405.906 | 3983.772 |
| image_arrival | 25 | 1099.826 | 439.44 | 3995.381 |
| verification_done | 25 | 4536.528 | 3629.429 | 8469.835 |

## System Metrics (Pi (edge device))
| Metric | Count | Mean | Median | P95 |
|---|---:|---:|---:|---:|
| cpu_percent_total | 300 | 15.017 | 7.1 | 51.015 |
| mem_percent_total | 300 | 40.008 | 40.7 | 41.6 |
| power_proxy_score | 300 | 33.829 | 12.16 | 122.436 |
| proc_cpu_percent | 300 | 3.53 | 3.0 | 10.0 |
| proc_rss_mb | 300 | 139.287 | 140.625 | 142.469 |

## Bottleneck Findings
- Verification latency p95 is high (>2500ms): likely network/cloud bottleneck.

## Events By Device
- pi-edge-01: 25
