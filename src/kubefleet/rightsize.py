"""Rightsizing: CPU and memory requests from usage history, judged on the weeks that follow.

Each workload has four weeks of usage in 5-minute samples. Recommendations are made from the first two weeks and
judged on the next two:
- CPU request: a high percentile of past usage, with headroom; the limit is twice the request;
- memory: the peak of past usage, plus the growth trend over the coming two weeks, with headroom. Memory is set as
  request = limit, because a container over its memory limit is killed, not slowed.
The shortcut compared: average usage x 1.2 for both.

Workload shapes used while tuning: steady, daily cycle, spiky. Shapes held back for the test: a slow memory leak with
weekly restarts, and nightly batch bursts.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

STEP_MIN = 5
DAY = 24 * 60 // STEP_MIN
WEEKS = 4
TUNING_SHAPES = ("steady", "daily", "spiky")
HELD_BACK_SHAPES = ("leak", "batch")


@dataclass
class Workload:
    name: str
    shape: str
    cpu: np.ndarray  # cores
    memory: np.ndarray  # MiB
    current_cpu: float  # requested today
    current_memory: float


def workload(seed: int, shapes: tuple[str, ...]) -> Workload:
    g = np.random.default_rng(seed)
    shape = str(g.choice(shapes))
    n = WEEKS * 7 * DAY
    t = np.arange(n)
    day = (t % DAY) / DAY
    weekday = (t // DAY) % 7 < 5
    base = float(g.lognormal(-1.2, 0.6))  # cores
    cpu = np.full(n, base)
    if shape in ("daily", "spiky", "leak"):
        cpu = base * (0.6 + 0.8 * np.clip(np.sin(np.pi * (day - 0.3) / 0.6), 0, None)) * np.where(weekday, 1.0, 0.7)
    if shape == "spiky":
        for _ in range(int(g.integers(4, 10))):
            s = int(g.integers(0, n - 12))
            cpu[s : s + int(g.integers(2, 8))] *= float(g.uniform(2.0, 3.5))
    if shape == "batch":
        cpu = np.full(n, base * 0.2)
        nightly = (t % DAY >= 2 * 12) & (t % DAY < 2 * 12 + int(g.integers(6, 18)))
        cpu[nightly] = base * float(g.uniform(3, 6))
    cpu = np.maximum(cpu * (1 + g.normal(0, 0.08, n)), 0.005)
    mem_base = float(g.uniform(150, 900))
    memory = mem_base * (1 + 0.15 * cpu / max(float(cpu.max()), 1e-9)) * (1 + g.normal(0, 0.02, n))
    if shape == "leak":  # grows until the weekly restart, a little faster each week
        since = t % (7 * DAY)
        memory = memory + since * float(g.uniform(0.004, 0.012)) * (1 + 0.15 * (t // (7 * DAY)))
    if shape == "batch":
        memory[cpu > base] *= 1.6
    current_cpu = float(np.percentile(cpu[: 14 * DAY], 99) * g.uniform(1.5, 4.0))
    current_memory = float(memory[: 14 * DAY].max() * g.uniform(1.3, 3.0))
    return Workload(f"{shape}-{seed}", shape, cpu, memory, current_cpu, current_memory)


@dataclass(frozen=True)
class Settings:
    cpu_percentile: float = 95.0
    cpu_headroom: float = 1.2
    memory_headroom: float = 1.15


@dataclass(frozen=True)
class Recommendation:
    cpu_request: float
    cpu_limit: float
    memory: float  # request = limit


def recommend(w: Workload, s: Settings | None = None) -> Recommendation:
    s = s or Settings()
    train = slice(0, 14 * DAY)
    cpu = float(np.percentile(w.cpu[train], s.cpu_percentile)) * s.cpu_headroom
    mem = w.memory[train]
    daily_peaks = mem.reshape(14, DAY).max(axis=1)
    slope = float(np.polyfit(np.arange(14), daily_peaks, 1)[0])  # MiB per day
    growth = max(slope, 0.0) * 14
    memory = (float(mem.max()) + growth) * s.memory_headroom
    return Recommendation(cpu, 2 * cpu, memory)


def naive(w: Workload) -> Recommendation:
    cpu = float(w.cpu[: 14 * DAY].mean()) * 1.2
    memory = float(w.memory[: 14 * DAY].mean()) * 1.2
    return Recommendation(cpu, 2 * cpu, memory)


def current(w: Workload) -> Recommendation:
    return Recommendation(w.current_cpu, 2 * w.current_cpu, w.current_memory)


def judge(w: Workload, r: Recommendation) -> dict[str, float]:
    test = slice(14 * DAY, None)
    cpu, mem = w.cpu[test], w.memory[test]
    return {
        "cpu_requested": r.cpu_request,
        "memory_requested": r.memory,
        "contention": float((cpu > r.cpu_request).mean()),  # share of time above the request
        "throttled": float((cpu > r.cpu_limit).mean()),
        "oom": float((mem > r.memory).any()),
    }
