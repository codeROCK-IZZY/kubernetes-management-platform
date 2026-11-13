# Known limitations

- **No live cluster connection.** The platform reads manifests and usage series. Connecting it means listing
  objects through the Kubernetes API and reading usage from metrics-server or Prometheus for each cluster, with
  read-only credentials.
- **The simulated memory leak was too slow** (about 8 MiB a week) to test trend extrapolation in the evaluation; a
  unit test covers it. All out-of-memory kills in the evaluation came from batch bursts.
- **Batch bursts are throttled 1.2% of the time** under the recommended limits.
- **Custom resources with pod templates** (Argo Rollouts, Knative, KEDA ScaledJobs) are not unwrapped.
- **Policies are fixed in code**; there is no per-namespace exception mechanism yet (a real platform needs one, with
  an owner and an expiry).
- **The API removal table** covers the common built-in APIs from 1.16 to 1.32, not every alpha API.
- **Generated manifests and usage.** Real fleets have stranger YAML and noisier metrics.
- **Claude's patches are checked, not scored** (paid calls).
- **No authentication** on the dashboard.
