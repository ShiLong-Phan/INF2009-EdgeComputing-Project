# PASO Analysis Report - baseline

## Summary
- Total events: 57
- Agreement rate (verify_status=ok): 5.263
- Verify status counts: {'ok': 57}

## Latency Metrics (ms)
| Metric | Count | Mean | Median | P95 |
|---|---:|---:|---:|---:|
| edge_reaction | 57 | 1158.824 | 1552.58 | 1572.684 |
| broker_ingest | 57 | 933.5 | 264.482 | 3991.39 |
| image_arrival | 57 | 1021.292 | 391.404 | 4003.303 |
| verification_done | 57 | 4388.177 | 3785.581 | 7807.713 |

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
- pi-edge-01: 57
