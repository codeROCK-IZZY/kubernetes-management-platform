# 3. Rightsizing is judged on the weeks after the recommendation

## Context

A rightsizing recommendation looks good on the data it was made from by construction. What matters is the weeks
after: does the workload get CPU when it needs it, and is it ever killed for memory?

## Decision

Recommendations use two weeks of history and are judged on the following two:

- **CPU:** the 95th percentile of usage x 1.2 as the request, with the limit at twice the request.
- **Memory:** the peak of usage, plus the trend in daily peaks extrapolated two weeks, x 1.15, with request = limit,
  because a container over its memory limit is killed rather than slowed.

The percentile and the two headrooms were chosen by a grid on 200 tuning workloads (steady, daily, spiky), trading
savings against time above the CPU request, then frozen. Held-out workloads and the held-back shapes (nightly batch,
slow leak) were run once.

## What the held-back run showed, including a simulator mistake

"Average x 1.2" got 92 of 200 held-back workloads killed for memory, and the recommendation none. All 92 were batch
workloads, whose memory jumps during the nightly run. The leak shape grew only about 8 MiB a week on a few hundred
MiB, so it never threatened either method. That is a mistake in the simulator, found after the held-back run. It was
not corrected afterwards, which would have meant reshaping the test to fit the result. Trend extrapolation is shown
instead by a unit test with a clearly leaking container.

## Consequences

- Batch workloads are throttled 1.2% of the time under the recommendation: bursts beyond twice the 95th percentile.
  A per-workload choice (burstable QoS, or a higher limit for batch) would remove that.
- Recommendations assume the next two weeks look like the last two. A launch or a seasonal peak needs a manual
  override.
