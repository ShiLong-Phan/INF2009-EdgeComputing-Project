# PASO Comparison (Baseline vs After)

| Metric | Baseline | After | Delta % | Direction |
|---|---:|---:|---:|---|
| Edge reaction p95 (ms) | 46.824 | 59.617 | 27.321 | lower is better |
| Broker ingest p95 (ms) | 3983.772 | 2898.126 | -27.252 | lower is better |
| Verification done p95 (ms) | 8469.835 | 6228.729 | -26.460 | lower is better |
| Pi CPU mean (%) | 15.017 | 19.518 | 29.973 | lower is better |
| Pi memory mean (%) | 40.008 | 44.082 | 10.183 | lower is better |
| Pi power proxy mean | 33.829 | 44.755 | 32.298 | lower is better |
| Agreement rate (%) | 64.000 | 78.000 | 21.875 | higher is better |

## Notes
- Negative delta is good for latency/resource metrics.
- Positive delta is good for agreement rate.
