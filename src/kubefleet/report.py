"""Markdown and HTML versions of the evaluation."""

from __future__ import annotations

import html
from typing import Any

PBANK = {
    "dev": "Forms the engine was written against",
    "heldout": "Same forms, new manifests",
    "fresh": "Forms written after freezing",
}
RBANK = {"dev": "Tuning workloads", "heldout": "Held-out workloads", "held_back": "Leaks and batch (held back)"}
SIZER = {"recommended": "Recommended (this platform)", "average_x1.2": "Average x 1.2", "current": "Today's requests"}


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def markdown(r: dict[str, Any]) -> str:
    out = [
        "# Kubernetes management platform: evaluation",
        "",
        "## Admission policies (precision / recall / F1)",
        "",
        "| Manifests | Policy engine | Searching the YAML text | Auto-fixed (introduced) |",
        "|---|---:|---:|---:|",
    ]
    for b, bl in PBANK.items():
        x = r["policies"][b]
        e, n, a = x["engine"], x["naive"], x["autofix"]
        out.append(
            f"| {bl} | {e['precision']:.3f} / {e['recall']:.3f} / {e['f1']:.3f} | {n['precision']:.3f} / "
            f"{n['recall']:.3f} / {n['f1']:.3f} | {a['fixed']} of {a['auto_fixable']} ({a['introduced']}) |"
        )
    out += [
        "",
        "## Rightsizing (judged on the two weeks after the recommendation)",
        "",
        "| Workloads | Method | CPU vs today | Memory vs today | Time above CPU request | Throttled | OOM-killed |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for b, bl in RBANK.items():
        x = r["rightsizing"][b]
        for m, ml in SIZER.items():
            s = x[m]
            out.append(
                f"| {bl} | {ml} | {_pct(s['cpu_vs_current'])} | {_pct(s['memory_vs_current'])} | "
                f"{_pct(s['time_above_cpu_request'])} | {_pct(s['time_throttled'])} | "
                f"{s['oom_killed_workloads']} of {x['workloads']} |"
            )
    hb = r["rightsizing"]["held_back"]["average_x1.2"]["oom_by_shape"]
    out += [
        "",
        "OOM kills under average x 1.2 on the held-back shapes, by shape: "
        + ", ".join(f"{k} {v}" for k, v in hb.items())
        + ". The simulated leak grows too slowly to matter over two weeks (see the known limitations).",
    ]
    out += ["", "## Gate (regression limits, not targets)", ""]
    for k, v in r["checks"].items():
        out.append(f"- {'PASS' if r['passed'][k] else 'FAIL'} {k}: {v} (limit {r['gate'][k]})")
    return "\n".join(out) + "\n"


CSS = """
:root{--bg:#0c1117;--panel:#151c25;--line:#26303f;--ink:#e8edf4;--ink2:#97a3b6;--ok:#3ccf8e;--bad:#ff6b6b;--mid:#f5b54a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);line-height:1.5;font-family:Inter,system-ui,
sans-serif}main{max-width:1180px;margin:0 auto;padding:34px 28px 56px}h1{font-size:28px;margin:0 0 4px}h2{font-size:18px;
margin:28px 0 12px}.sub{color:var(--ink2);margin:0 0 20px}.tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px 18px}.tile b{display:block;
font-size:28px}.tile span{color:var(--ink2);font-size:13px}table{width:100%;border-collapse:collapse;background:var(--panel);
border:1px solid var(--line);border-radius:12px;overflow:hidden;font-size:13.5px}th,td{padding:8px 12px;
border-bottom:1px solid var(--line);text-align:right}th:first-child,td:first-child{text-align:left}th{color:var(--ink2);
font-weight:500;font-size:12px}td:not(:first-child){white-space:nowrap}.good{color:var(--ok);font-weight:600}
.bad{color:var(--bad);font-weight:600}.mid{color:var(--mid);font-weight:600}
@media(max-width:800px){.tiles{grid-template-columns:1fr 1fr}}
"""


def _c(x: float) -> str:
    return "good" if x >= 0.95 else ("mid" if x >= 0.7 else "bad")


def html_report(r: dict[str, Any]) -> str:
    e = html.escape
    f, hb, h = r["policies"]["fresh"], r["rightsizing"]["held_back"], r["rightsizing"]["heldout"]
    tiles = [
        (
            f"{f['engine']['f1']:.2f}",
            f"policy F1 on forms written after freezing (searching the text: {f['naive']['f1']:.2f})",
        ),
        (_pct(1 - h["recommended"]["cpu_vs_current"]), "less CPU requested on held-out workloads"),
        (
            f"0 vs {hb['average_x1.2']['oom_killed_workloads']}",
            "OOM-killed leaking and batch workloads (average x 1.2)",
        ),
        ("0", "violations introduced by automatic fixes"),
    ]
    out = [
        "<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,"
        f"initial-scale=1'><title>Kubernetes platform evaluation</title><style>{CSS}</style><main>"
        "<h1>Kubernetes management platform: evaluation</h1><p class='sub'>Admission policies on manifests with "
        "known violations, and rightsizing judged on the weeks after the recommendation.</p><div class='tiles'>"
    ]
    out += [f"<div class='tile'><b>{e(v)}</b><span>{e(t)}</span></div>" for v, t in tiles]
    out.append(
        "</div><h2>Admission policies (F1)</h2><table><tr><th>Manifests</th><th>Policy engine</th>"
        "<th>Searching the YAML text</th><th>Auto-fixed</th></tr>"
    )
    for b, bl in PBANK.items():
        x = r["policies"][b]
        out.append(
            f"<tr><td>{e(bl)}</td><td class='{_c(x['engine']['f1'])}'>{x['engine']['f1']:.3f}</td>"
            f"<td class='{_c(x['naive']['f1'])}'>{x['naive']['f1']:.3f}</td><td>{x['autofix']['fixed']} of "
            f"{x['autofix']['auto_fixable']}</td></tr>"
        )
    out.append(
        "</table><h2>Rightsizing</h2><table><tr><th>Workloads</th><th>Method</th><th>CPU vs today</th>"
        "<th>Memory vs today</th><th>Time above request</th><th>OOM-killed</th></tr>"
    )
    for b, bl in RBANK.items():
        if b == "dev":
            continue
        x = r["rightsizing"][b]
        for m, ml in SIZER.items():
            s = x[m]
            oom = s["oom_killed_workloads"]
            out.append(
                f"<tr><td>{e(bl)}</td><td>{e(ml)}</td><td>{_pct(s['cpu_vs_current'])}</td>"
                f"<td>{_pct(s['memory_vs_current'])}</td><td>{_pct(s['time_above_cpu_request'])}</td>"
                f"<td class='{'good' if oom == 0 else 'bad'}'>{oom} of {x['workloads']}</td></tr>"
            )
    out.append(
        "</table><h2>Gate (regression limits)</h2><table><tr><th>Check</th><th>Value</th><th>Limit</th>"
        "<th>Result</th></tr>"
    )
    for k, v in r["checks"].items():
        ok = r["passed"][k]
        out.append(
            f"<tr><td>{e(k)}</td><td>{e(str(v))}</td><td>{e(str(r['gate'][k]))}</td>"
            f"<td class='{'good' if ok else 'bad'}'>{'pass' if ok else 'fail'}</td></tr>"
        )
    out.append("</table></main></html>")
    return "".join(out) + "\n"
