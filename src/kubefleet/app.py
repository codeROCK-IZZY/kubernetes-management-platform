"""The fleet dashboard and API: clusters, policy violations with verified fixes, rightsizing, upgrade blockers."""

from __future__ import annotations

import html
from collections import Counter
from typing import Any

import numpy as np
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .fleet import Cluster, Item, savings
from .policy import RULES, check
from .rightsize import DAY
from .upgrade import path

e = html.escape

CSS = """
:root{--bg:#0c1117;--panel:#151c25;--line:#26303f;--ink:#e8edf4;--ink2:#97a3b6;--hi:#ff6b6b;--md:#f5b54a;--lo:#7aa2ff;
--ok:#3ccf8e;--acc:#7aa2ff}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.5 Inter,system-ui,sans-serif}a{color:inherit}header{padding:14px 26px;border-bottom:1px solid var(--line);
display:flex;gap:18px;align-items:baseline}header b{font-size:17px}header nav a{color:var(--ink2);text-decoration:none;
margin-right:14px}main{padding:20px 26px 48px;max-width:1280px;margin:0 auto}.tiles{display:grid;
grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:18px}.tile{background:var(--panel);border:1px solid var(--line);
border-radius:12px;padding:14px 16px}.tile b{display:block;font-size:26px}.tile span{color:var(--ink2);font-size:12.5px}
table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:12px;
overflow:hidden;margin-bottom:16px}th,td{padding:7px 12px;border-bottom:1px solid var(--line);text-align:left;
vertical-align:top}th{color:var(--ink2);font-weight:500;font-size:12px}.muted{color:var(--ink2)}.b{display:inline-block;
padding:1px 8px;border-radius:99px;font-size:11.5px;font-weight:600;margin:1px 3px 1px 0}.high{background:#ff6b6b22;
color:var(--hi)}.medium{background:#f5b54a22;color:var(--md)}.low{background:#7aa2ff22;color:var(--lo)}.ok{
background:#3ccf8e22;color:var(--ok)}h1{font-size:22px;margin:0 0 4px}h2{font-size:13px;color:var(--ink2);
text-transform:uppercase;letter-spacing:.05em;margin:22px 0 8px}.cols{display:grid;grid-template-columns:1fr 1fr;gap:18px}
pre{background:#0f141c;border:1px solid var(--line);border-radius:10px;padding:12px;font-size:12px;max-height:520px;
overflow:auto}.box{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 16px}
svg text{fill:var(--ink2);font-size:11px}.legend{display:flex;gap:14px;color:var(--ink2);font-size:12.5px}
.sw{display:inline-block;width:14px;height:3px;vertical-align:middle;margin-right:6px}
@media(max-width:900px){.cols,.tiles{grid-template-columns:1fr}}
"""


class ManifestIn(BaseModel):
    yaml: str


def _page(title: str, body: str) -> str:
    return (
        f"<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,"
        f"initial-scale=1'><title>{e(title)}</title><style>{CSS}</style><header><b>Fleet</b><nav><a href='/'>"
        "Clusters</a><a href='/workloads'>Workloads</a><a href='/upgrades'>Upgrades</a></nav></header>"
        f"<main>{body}</main></html>"
    )


def _badges(item: Item) -> str:
    sev = Counter(v.severity for v in item.violations)
    out = "".join(f"<span class='b {s}'>{sev[s]} {s}</span>" for s in ("high", "medium", "low") if sev[s])
    return out or "<span class='b ok'>passes</span>"


def usage_chart(item: Item) -> str:
    w, rec = item.usage, item.recommendation
    assert w is not None
    assert rec is not None
    hourly = w.cpu.reshape(-1, 12).max(axis=1)  # hourly peaks keep the chart readable
    width, h, left, top = 760, 180, 46, 10
    ymax = max(float(hourly.max()), w.current_cpu, rec.cpu_request) * 1.1

    def x(i: float) -> float:
        return left + (width - left - 10) * i / (len(hourly) - 1)

    def y(v: float) -> float:
        return top + h - h * v / ymax

    pts = " ".join(f"{x(i):.1f},{y(float(v)):.1f}" for i, v in enumerate(hourly))
    split = x(14 * DAY / 12)
    grid = "".join(
        f"<line x1='{left}' x2='{width - 10}' y1='{y(t):.1f}' y2='{y(t):.1f}' stroke='#26303f'/><text "
        f"x='{left - 6}' y='{y(t) + 4:.1f}' text-anchor='end'>{t:.2f}</text>"
        for t in np.linspace(0, ymax, 5)
    )
    return (
        f"<div class='legend'><span><i class='sw' style='background:#7aa2ff'></i>CPU used (hourly peak, cores)</span>"
        f"<span><i class='sw' style='background:#ff6b6b'></i>requested today</span><span><i class='sw' "
        f"style='background:#3ccf8e'></i>recommended</span></div><svg viewBox='0 0 {width} {h + 34}' width='100%' "
        f"role='img' aria-label='CPU use against today and the recommended request'>{grid}"
        f"<polyline points='{pts}' fill='none' stroke='#7aa2ff' stroke-width='1.5'/>"
        f"<line x1='{left}' x2='{width - 10}' y1='{y(w.current_cpu):.1f}' y2='{y(w.current_cpu):.1f}' stroke='#ff6b6b' "
        f"stroke-width='2'/><line x1='{left}' x2='{width - 10}' y1='{y(rec.cpu_request):.1f}' "
        f"y2='{y(rec.cpu_request):.1f}' stroke='#3ccf8e' stroke-width='2'/><line x1='{split:.1f}' x2='{split:.1f}' "
        f"y1='{top}' y2='{top + h}' stroke='#97a3b6' stroke-dasharray='4 3'/><text x='{split + 4:.1f}' y='{top + 12}'>"
        f"recommended from the weeks before this line</text><text x='{left}' y='{top + h + 18}'>4 weeks</text></svg>"
    )


def create_app(clusters: list[Cluster]) -> FastAPI:
    app = FastAPI(title="Kubernetes fleet")
    items = {i.slug: i for c in clusters for i in c.items}

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"status": "ok", "clusters": len(clusters), "objects": len(items)}

    @app.post("/api/check")
    def api_check(body: ManifestIn) -> dict[str, Any]:
        try:
            found = check(body.yaml)
        except yaml.YAMLError as err:
            raise HTTPException(422, f"not valid YAML: {err}") from err
        return {
            "violations": [
                {
                    "rule": v.rule,
                    "severity": v.severity,
                    "object": v.obj,
                    "container": v.container,
                    "message": v.message,
                    "fix": RULES[v.rule][2],
                }
                for v in found
            ]
        }

    @app.get("/api/clusters")
    def api_clusters() -> list[dict[str, Any]]:
        return [
            {
                "name": c.name,
                "version": c.version,
                "target": c.target,
                "objects": len(c.items),
                "violations": sum(len(i.violations) for i in c.items),
                "upgrade_blockers": [b.__dict__ for b in c.blockers],
                **savings(c),
            }
            for c in clusters
        ]

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        allv = [v for c in clusters for i in c.items for v in i.violations]
        sv = [savings(c) for c in clusters]
        cpu_now, cpu_rec = sum(s["cpu_now"] for s in sv), sum(s["cpu_recommended"] for s in sv)
        fixable = sum(len(i.fix.fixed) for c in clusters for i in c.items if i.fix)
        tiles = [
            (str(sum(1 for v in allv if v.severity == "high")), f"high-severity violations ({len(allv)} in all)"),
            (str(fixable), "violations fixed automatically, each checked again"),
            (f"{cpu_now - cpu_rec:.1f} cores", f"of CPU requests to release ({1 - cpu_rec / max(cpu_now, 1e-9):.0%})"),
            (str(sum(len(c.blockers) for c in clusters)), "objects that would break on the planned upgrades"),
        ]
        rows = ""
        for c, s in zip(clusters, sv, strict=True):
            sev = Counter(v.severity for i in c.items for v in i.violations)
            rows += (
                f"<tr><td><b>{e(c.name)}</b></td><td>{e(c.version)} → {e(c.target)}</td><td>{len(c.items)}</td><td>"
                + "".join(f"<span class='b {k}'>{sev[k]} {k}</span>" for k in ("high", "medium", "low") if sev[k])
                + f"</td><td>{s['cpu_now']:.1f} → {s['cpu_recommended']:.1f}</td><td>{s['memory_now'] / 1024:.1f} → "
                f"{s['memory_recommended'] / 1024:.1f}</td><td>{len(c.blockers)}</td></tr>"
            )
        body = (
            "<div class='tiles'>"
            + "".join(f"<div class='tile'><b>{e(v)}</b><span>{e(t)}</span></div>" for v, t in tiles)
            + "</div><table><tr><th>Cluster</th><th>Version → "
            "planned</th><th>Objects</th><th>Violations</th><th>CPU requests (cores) now → recommended</th>"
            f"<th>Memory (GiB) now → recommended</th><th>Upgrade blockers</th></tr>{rows}</table>"
        )
        return _page("Fleet", body)

    @app.get("/workloads", response_class=HTMLResponse)
    def workloads() -> str:
        rows = ""
        order = sorted(items.values(), key=lambda i: (-sum(v.severity == "high" for v in i.violations), i.slug))
        for i in order:
            sizing = (
                f"{i.usage.current_cpu:.2f} → {i.recommendation.cpu_request:.2f} cores"
                if i.usage and i.recommendation
                else "—"
            )
            auto = len(i.fix.fixed) if i.fix else 0
            rows += (
                f"<tr><td><a href='/workloads/{e(i.slug)}'>{e(i.name)}</a><div class='muted'>{e(i.cluster)}</div>"
                f"</td><td>{_badges(i)}</td><td>{auto or '—'}</td><td>{sizing}</td></tr>"
            )
        body = (
            "<h1>Workloads</h1><table><tr><th>Object</th><th>Violations</th><th>Fixed automatically</th>"
            f"<th>CPU request</th></tr>{rows}</table>"
        )
        return _page("Workloads", body)

    @app.get("/workloads/{slug}", response_class=HTMLResponse)
    def workload(slug: str) -> str:
        i = items.get(slug)
        if i is None:
            raise HTTPException(404, "no such object")
        viol = (
            "".join(
                f"<tr><td><span class='b {v.severity}'>{v.severity}</span></td><td>{e(v.message)}"
                f"<div class='muted'>{e(v.container or '')}</div></td><td class='muted'>{e(RULES[v.rule][2])}</td>"
                f"<td>{'fixed automatically' if i.fix and v.rule in i.fix.fixed else 'needs a decision'}</td></tr>"
                for v in i.violations
            )
            or "<tr><td colspan='4'>Passes every policy.</td></tr>"
        )
        sizing = f"<h2>CPU use and requests</h2><div class='box'>{usage_chart(i)}</div>" if i.usage else ""
        fixed = (
            f"<h2>After the automatic fixes (checked again: {len(i.fix.fixed)} fixed, "
            f"{len(i.fix.introduced)} introduced)</h2><pre>{e(yaml.safe_dump(i.fix.obj, sort_keys=False))}</pre>"
            if i.fix and i.fix.fixed
            else ""
        )
        body = (
            f"<h1>{e(i.name)}</h1><p class='muted'>{e(i.cluster)} · {_badges(i)}</p><table><tr><th>Severity</th>"
            f"<th>Problem</th><th>Fix</th><th>Status</th></tr>{viol}</table>{sizing}<div class='cols'><div><h2>"
            f"Manifest</h2><pre>{e(yaml.safe_dump(i.obj, sort_keys=False))}</pre></div><div>{fixed}</div></div>"
        )
        return _page(i.name, body)

    @app.get("/upgrades", response_class=HTMLResponse)
    def upgrades() -> str:
        out = "<h1>Upgrades</h1><p class='muted'>Objects using APIs that a planned version no longer serves.</p>"
        for c in clusters:
            rows = "".join(
                f"<tr><td>{e(b.obj)}</td><td><code>{e(b.api_version)}</code></td><td>{e(b.removed_in)}</td>"
                f"<td><code>{e(b.use_instead)}</code></td></tr>"
                for b in c.blockers
            )
            out += f"<h2>{e(c.name)}: {e(c.version)} → {' → '.join(path(c.version, c.target))}</h2>" + (
                f"<table><tr><th>Object</th><th>API version</th><th>Removed in</th><th>Use instead</th></tr>"
                f"{rows}</table>"
                if rows
                else "<p class='muted'>Nothing blocks this upgrade.</p>"
            )
        return _page("Upgrades", out)

    return app


def demo_app() -> FastAPI:
    from .fleet import demo

    return create_app(demo())
