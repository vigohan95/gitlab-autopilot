"""Synthetic monitor for the deployed environments (DevSecOps stage: monitor).

    python scripts/monitor.py --target production=https://x.hf.space --target staging=https://y.hf.space

Probes ``/healthz`` (expects HTTP 200 and ``{"status": "ok"}``) and ``/version``
of each target, writes ``monitor-report.json`` and exits non-zero if any target
is unhealthy so that the pipeline turns red and the incident path runs.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime

import httpx


def probe_target(name: str, base: str, timeout: float = 15.0, retries: int = 3, backoff: float = 5.0) -> dict:
    base = base.rstrip("/")
    result: dict = {"name": name, "url": base, "ok": False, "checks": [], "version": None}
    for attempt in range(1, retries + 1):
        checks = []
        ok = True
        with httpx.Client(timeout=timeout, follow_redirects=True) as c:
            for path in ("/healthz", "/version"):
                started = time.perf_counter()
                try:
                    r = c.get(base + path)
                    latency = round((time.perf_counter() - started) * 1000, 1)
                    body = r.json() if "json" in r.headers.get("content-type", "") else None
                    healthy = r.status_code == 200 and (path != "/healthz" or (body or {}).get("status") == "ok")
                    if path == "/version" and body:
                        result["version"] = body.get("version")
                    checks.append(
                        {"path": path, "status": r.status_code, "latency_ms": latency, "ok": healthy, "body": body}
                    )
                except httpx.HTTPError as exc:
                    checks.append(
                        {"path": path, "status": None, "latency_ms": None, "ok": False, "error": type(exc).__name__}
                    )
                    healthy = False
                ok = ok and healthy
        result["checks"] = checks
        result["attempts"] = attempt
        if ok:
            result["ok"] = True
            break
        if attempt < retries:
            time.sleep(backoff)
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", action="append", default=[], help="name=url (repeatable)")
    ap.add_argument("--expect-version", help="fail if production reports a different version")
    ap.add_argument("--report", default="monitor-report.json")
    ap.add_argument("--retries", type=int, default=3)
    args = ap.parse_args(argv)

    targets = [t.split("=", 1) for t in args.target if "=" in t]
    if not targets:
        print("no targets configured (set PULSE_STAGING_URL / PULSE_PRODUCTION_URL); nothing to monitor")
        json.dump({"ts": datetime.now(UTC).isoformat(), "targets": [], "ok": True}, open(args.report, "w"))
        return 0

    results = [probe_target(name, url, retries=args.retries) for name, url in targets]
    all_ok = all(r["ok"] for r in results)
    if args.expect_version:
        for r in results:
            if r["name"] == "production" and r["version"] not in (None, args.expect_version):
                r["ok"] = False
                r["checks"].append({"path": "/version", "ok": False, "error": f"expected {args.expect_version}"})
                all_ok = False

    report = {"ts": datetime.now(UTC).isoformat(timespec="seconds"), "targets": results, "ok": all_ok}
    with open(args.report, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    for r in results:
        state = "UP  " if r["ok"] else "DOWN"
        print(f"[{state}] {r['name']:<11} {r['url']}  version={r['version']}  attempts={r['attempts']}")
        for c in r["checks"]:
            detail = f"status={c.get('status')} latency={c.get('latency_ms')}ms ok={c['ok']} {c.get('error', '')}"
            print(f"        {c['path']:<9} {detail}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
