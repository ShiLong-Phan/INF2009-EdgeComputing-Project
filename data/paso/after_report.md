# PASO Analysis Report - after

## Summary
- Total events: 50
- Agreement rate (verify_status=ok): 78.0
- Verify status counts: {'ok': 50}

## Latency Metrics (ms)
| Metric | Count | Mean | Median | P95 |
|---|---:|---:|---:|---:|
| edge_reaction | 50 | 42.273 | 44.295 | 59.617 |
| broker_ingest | 50 | 862.664 | 285.441 | 2898.126 |
| image_arrival | 50 | 891.799 | 328.267 | 2911.534 |
| verification_done | 50 | 3937.793 | 3589.457 | 6228.729 |

## System Metrics (Pi (edge device))
| Metric | Count | Mean | Median | P95 |
|---|---:|---:|---:|---:|
| cpu_percent_total | 300 | 19.518 | 13.9 | 49.51 |
| mem_percent_total | 300 | 44.082 | 43.9 | 46.9 |
| power_proxy_score | 300 | 44.755 | 31.8 | 118.824 |
| proc_cpu_percent | 300 | 3.633 | 3.0 | 6.0 |
| proc_rss_mb | 300 | 134.779 | 135.016 | 135.016 |

## Bottleneck Findings
- Verification latency p95 is high (>2500ms): likely network/cloud bottleneck.

## Events By Device
- pi-edge-01: 50
