"""Command line: check manifests, fix them, plan an upgrade, serve the dashboard, run the evaluation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kubefleet", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="policy violations in YAML or JSON manifests (exit 1 if any)")
    c.add_argument("paths", nargs="+")
    f = sub.add_parser("fix", help="apply the automatic fixes and print the result")
    f.add_argument("path")
    u = sub.add_parser("upgrade", help="objects that block an upgrade")
    u.add_argument("paths", nargs="+")
    u.add_argument("--from", dest="current", required=True)
    u.add_argument("--to", dest="target", required=True)
    s = sub.add_parser("serve", help="the dashboard with a demo fleet")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    e = sub.add_parser("evaluate", help="policies and rightsizing on held-out data")
    e.add_argument("--out", default="eval/reports")
    e.add_argument("--gate", action="store_true")
    args = ap.parse_args(argv)

    import yaml

    if args.cmd == "check":
        from .policy import check

        found = [v for p in args.paths for v in check(Path(p).read_text(encoding="utf-8"))]
        for v in found:
            print(f"{v.severity:6} {v.obj:40} {v.container or '-':20} {v.message}")
        return 1 if found else 0
    if args.cmd == "fix":
        from .fixes import autofix

        docs = [d for d in yaml.safe_load_all(Path(args.path).read_text(encoding="utf-8")) if isinstance(d, dict)]
        results = [autofix(d) for d in docs]
        print("---\n".join(yaml.safe_dump(r.obj, sort_keys=False) for r in results))
        print(
            json.dumps(
                {
                    "fixed": sorted({x for r in results for x in r.fixed}),
                    "still_needs_a_decision": sorted({x for r in results for x in r.remaining}),
                }
            ),
            file=sys.stderr,
        )
        return 0
    if args.cmd == "upgrade":
        from .upgrade import blockers

        objs = [
            d
            for p in args.paths
            for d in yaml.safe_load_all(Path(p).read_text(encoding="utf-8"))
            if isinstance(d, dict)
        ]
        for b in blockers(objs, args.current, args.target):
            print(f"{b.obj:40} {b.api_version:35} removed in {b.removed_in:6} use {b.use_instead}")
        return 0
    if args.cmd == "serve":
        import uvicorn

        from .app import demo_app

        uvicorn.run(demo_app(), host=args.host, port=args.port)
        return 0
    from .evaluate import run, save

    result = run()
    save(result, Path(args.out))
    print((Path(args.out) / "report.md").read_text(encoding="utf-8"))
    return 1 if args.gate and not result["ok"] else 0


if __name__ == "__main__":
    sys.exit(main())
