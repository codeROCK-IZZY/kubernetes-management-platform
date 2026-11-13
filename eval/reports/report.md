# Kubernetes management platform: evaluation

## Admission policies (precision / recall / F1)

| Manifests | Policy engine | Searching the YAML text | Auto-fixed (introduced) |
|---|---:|---:|---:|
| Forms the engine was written against | 1.000 / 1.000 / 1.000 | 0.635 / 0.844 / 0.725 | 339 of 339 (0) |
| Same forms, new manifests | 1.000 / 1.000 / 1.000 | 0.624 / 0.816 / 0.707 | 348 of 348 (0) |
| Forms written after freezing | 1.000 / 1.000 / 1.000 | 0.228 / 0.628 / 0.335 | 142 of 142 (0) |

## Rightsizing (judged on the two weeks after the recommendation)

| Workloads | Method | CPU vs today | Memory vs today | Time above CPU request | Throttled | OOM-killed |
|---|---|---:|---:|---:|---:|---:|
| Tuning workloads | Recommended (this platform) | 38.8% | 53.2% | 0.1% | 0.0% | 0 of 200 |
| Tuning workloads | Average x 1.2 | 25.4% | 50.2% | 21.9% | 0.1% | 0 of 200 |
| Tuning workloads | Today's requests | 100.0% | 100.0% | 0.0% | 0.0% | 0 of 200 |
| Held-out workloads | Recommended (this platform) | 40.4% | 52.9% | 0.1% | 0.0% | 0 of 300 |
| Held-out workloads | Average x 1.2 | 26.9% | 49.9% | 21.1% | 0.1% | 0 of 300 |
| Held-out workloads | Today's requests | 100.0% | 100.0% | 0.0% | 0.0% | 0 of 300 |
| Leaks and batch (held back) | Recommended (this platform) | 19.3% | 54.5% | 1.3% | 1.2% | 0 of 200 |
| Leaks and batch (held back) | Average x 1.2 | 9.4% | 39.3% | 18.2% | 1.8% | 92 of 200 |
| Leaks and batch (held back) | Today's requests | 100.0% | 100.0% | 0.0% | 0.0% | 0 of 200 |

OOM kills under average x 1.2 on the held-back shapes, by shape: batch 92, leak 0. The simulated leak grows too slowly to matter over two weeks (see the known limitations).

## Gate (regression limits, not targets)

- PASS heldout_policy_f1_min: 1.0 (limit 0.99)
- PASS fresh_policy_f1_min: 1.0 (limit 0.98)
- PASS autofix_introduced_max: 0 (limit 0)
- PASS heldout_rightsizing_time_above_request_max: 0.001 (limit 0.01)
- PASS held_back_oom_killed_max: 0 (limit 0)
- PASS heldout_cpu_vs_current_max: 0.404 (limit 0.6)
