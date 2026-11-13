"""Evaluation: admission policies, automatic fixes and rightsizing.

    kubefleet evaluate [--gate] [--out DIR]

Policies: 360 manifests per bank. The engine was written against `dev` forms; `heldout` is the same forms with new
seeds; `fresh` forms (CronJob and Job nesting, init and ephemeral containers, container overrides of pod security,
digests and registry ports, multi-document files, YAML anchors, JSON, comments) were written after it was frozen.

Rightsizing: settings chosen on 200 `dev` workloads (steady, daily, spiky); results on 300 `heldout` workloads of the
same shapes and 200 `held_back` workloads (memory leaks with weekly restarts, nightly batch bursts).
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from .fixes import AUTO, autofix
from .manifests import generate
from .policy import RULES, check, naive
from .rightsize import HELD_BACK_SHAPES, TUNING_SHAPES, current, judge, recommend, workload
from .rightsize import naive as naive_rs

POLICY_BANKS = {"dev": 1, "heldout": 2, "fresh": 3}
RS_BANKS: dict[str, tuple[range, tuple[str, ...]]] = {
    "dev": (range(1, 201), TUNING_SHAPES),
    "heldout": (range(1001, 1301), TUNING_SHAPES),
    "held_back": (range(2001, 2201), HELD_BACK_SHAPES),
}


def _prf(tp: int, fp: int, fn: int) -> dict[str, float]:
    p, r = tp / max(tp + fp, 1), tp / max(tp + fn, 1)
    return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(2 * p * r / max(p + r, 1e-9), 3)}


def policy_bank(form: str, n: int = 360) -> dict[str, Any]:
    manifests = generate(n, POLICY_BANKS[form], form)
    out: dict[str, Any] = {"manifests": n, "violations": sum(len(m.truth) for m in manifests)}
    for name in ("engine", "naive"):
        tp = fp = fn = 0
        per_rule: Counter[str] = Counter()
        missed: Counter[str] = Counter()
        for m in manifests:
            got = {v.rule for v in check(m.text)} if name == "engine" else naive(m.text)
            tp += len(got & m.truth)
            fp += len(got - m.truth)
            fn += len(m.truth - got)
            for r in got - m.truth:
                per_rule[r] += 1
            for r in m.truth - got:
                missed[r] += 1
        out[name] = {
            **_prf(tp, fp, fn),
            "false_alarms_by_rule": dict(per_rule.most_common(5)),
            "missed_by_rule": dict(missed.most_common(5)),
        }
    fixed = auto_total = introduced = left = 0
    for m in manifests:
        for doc in yaml.safe_load_all(m.text):
            if isinstance(doc, dict):
                res = autofix(doc)
                auto_total += len((res.fixed | res.remaining) & AUTO)
                fixed += len(res.fixed & AUTO)
                introduced += len(res.introduced)
                left += len(res.remaining)
    out["autofix"] = {"auto_fixable": auto_total, "fixed": fixed, "introduced": introduced, "left_for_people": left}
    return out


def rightsizing_bank(name: str) -> dict[str, Any]:
    seeds, shapes = RS_BANKS[name]
    ws = [workload(s, shapes) for s in seeds]
    cur = [judge(w, current(w)) for w in ws]
    cpu_now = sum(c["cpu_requested"] for c in cur)
    mem_now = sum(c["memory_requested"] for c in cur)
    out: dict[str, Any] = {"workloads": len(ws)}
    for label, fn in (("recommended", recommend), ("average_x1.2", naive_rs), ("current", current)):
        rows = [judge(w, fn(w)) for w in ws]
        out[label] = {
            "cpu_vs_current": round(sum(r["cpu_requested"] for r in rows) / cpu_now, 3),
            "memory_vs_current": round(sum(r["memory_requested"] for r in rows) / mem_now, 3),
            "time_above_cpu_request": round(float(np.mean([r["contention"] for r in rows])), 4),
            "time_throttled": round(float(np.mean([r["throttled"] for r in rows])), 4),
            "oom_killed_workloads": int(sum(r["oom"] for r in rows)),
            "oom_by_shape": {
                sh: int(sum(r["oom"] for w, r in zip(ws, rows, strict=True) if w.shape == sh))
                for sh in sorted({w.shape for w in ws})
            },
        }
    return out


GATE: dict[str, float] = {
    "heldout_policy_f1_min": 0.99,
    "fresh_policy_f1_min": 0.98,
    "autofix_introduced_max": 0,
    "heldout_rightsizing_time_above_request_max": 0.01,
    "held_back_oom_killed_max": 0,
    "heldout_cpu_vs_current_max": 0.6,
}


def run() -> dict[str, Any]:
    policies = {f: policy_bank(f) for f in POLICY_BANKS}
    sizing = {b: rightsizing_bank(b) for b in RS_BANKS}
    checks = {
        "heldout_policy_f1_min": policies["heldout"]["engine"]["f1"],
        "fresh_policy_f1_min": policies["fresh"]["engine"]["f1"],
        "autofix_introduced_max": sum(p["autofix"]["introduced"] for p in policies.values()),
        "heldout_rightsizing_time_above_request_max": sizing["heldout"]["recommended"]["time_above_cpu_request"],
        "held_back_oom_killed_max": sizing["held_back"]["recommended"]["oom_killed_workloads"],
        "heldout_cpu_vs_current_max": sizing["heldout"]["recommended"]["cpu_vs_current"],
    }
    passed = {k: (v <= GATE[k] if k.endswith("_max") else v >= GATE[k]) for k, v in checks.items()}
    return {
        "rules": {k: v[1] for k, v in RULES.items()},
        "policies": policies,
        "rightsizing": sizing,
        "checks": checks,
        "gate": GATE,
        "passed": passed,
        "ok": all(passed.values()),
    }


def save(result: dict[str, Any], out: Path) -> None:
    from .report import html_report, markdown

    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8", newline="\n")
    (out / "report.md").write_text(markdown(result), encoding="utf-8", newline="\n")
    (out / "report.html").write_text(html_report(result), encoding="utf-8", newline="\n")
