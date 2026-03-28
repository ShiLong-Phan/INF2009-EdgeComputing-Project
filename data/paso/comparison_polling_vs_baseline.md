# PASO Comparison (Baseline vs After)

| Metric | Baseline | After | Delta % | Direction |
|---|---:|---:|---:|---|
| Edge reaction p95 (ms) | 46.824 | 52.779 | 12.718 | lower is better |
| Broker ingest p95 (ms) | 3983.772 | n/a | n/a | lower is better |
| Verification done p95 (ms) | 8469.835 | n/a | n/a | lower is better |
| Pi CPU mean (%) | 15.017 | 19.797 | 31.831 | lower is better |
| Pi memory mean (%) | 40.008 | 38.818 | -2.974 | lower is better |
| Pi power proxy mean | 33.829 | 44.267 | 30.855 | lower is better |
| Agreement rate (%) | 64.000 | n/a | n/a | higher is better |

## Notes
- Negative delta is good for latency/resource metrics.
- Positive delta is good for agreement rate.
