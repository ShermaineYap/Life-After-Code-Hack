"""Post-deploy data monitor: catch the failures dbt tests can't.

dbt tests check that each row is valid. They pass happily when an upstream
extract silently loses 90% of its rows. This monitor compares the deployed
data product against the last known-good release ("baseline") and fails on:

* row-count collapse in any mart (default: more than 30% fewer rows);
* freshness regression (the latest order date moves backwards);
* stale build (the deployed build is older than --max-age-hours);
* the service not answering, or answering without data.

It reads either the live service (``--url``, which calls ``/health``) or a
warehouse file directly (``--warehouse``), so it runs the same way against
Cloud Run, a CI artefact, or a laptop.

Usage:
    python scripts/monitor.py --url https://<service>.run.app \
        --baseline monitoring/baseline.json --json reports/monitor.json --markdown reports/monitor.md
    python scripts/monitor.py --warehouse target/warehouse.duckdb --write-baseline monitoring/baseline.json

Exit codes: 0 = healthy, 1 = anomaly (the pipeline's rollback job keys off this).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

MART_TABLES = ["dim_customer", "fct_revenue", "kpi_dashboard"]


def snapshot_from_warehouse(path: str) -> dict:
    import duckdb
    con = duckdb.connect(path, read_only=True)
    info = con.execute("select release_version, git_sha, built_at, order_rows, max_order_date "
                       "from marts.meta_build_info").fetchone()
    tables = {t: con.execute(f"select count(*) from marts.{t}").fetchone()[0] for t in MART_TABLES}
    con.close()
    return {
        "release_version": info[0], "git_sha": info[1],
        "built_at": info[2].replace(tzinfo=timezone.utc).isoformat() if info[2] else None,
        "order_rows": info[3], "max_order_date": info[4].isoformat() if info[4] else None,
        "tables": tables,
    }


def snapshot_from_url(url: str, retries: int = 6, wait_s: float = 10.0) -> dict:
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # cold start or rollout in progress
            last = exc
            time.sleep(wait_s if attempt < retries - 1 else 0)
    raise RuntimeError(f"service did not answer /health: {last}")


def evaluate(cur: dict, base: dict | None, *, max_drop: float = 0.3,
             max_age_hours: float = 48.0, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    checks = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail})

    add("has_data", cur.get("order_rows", 0) > 0 and all(v > 0 for v in cur.get("tables", {}).values()),
        f"order_rows={cur.get('order_rows')}, tables={cur.get('tables')}")

    if cur.get("built_at"):
        built = datetime.fromisoformat(cur["built_at"].replace("Z", "+00:00"))
        if built.tzinfo is None:
            built = built.replace(tzinfo=timezone.utc)
        age_h = (now - built).total_seconds() / 3600
        add("build_fresh", age_h <= max_age_hours, f"build age {age_h:.1f} h (limit {max_age_hours} h)")

    if base:
        b_rows, c_rows = base.get("order_rows") or 0, cur.get("order_rows") or 0
        if b_rows:
            drop = 1 - c_rows / b_rows
            add("order_rows_stable", drop <= max_drop,
                f"{c_rows} rows vs baseline {b_rows} ({c_rows / b_rows - 1:+.0%})")
        for t, b in (base.get("tables") or {}).items():
            c = (cur.get("tables") or {}).get(t, 0)
            if b:
                drop = 1 - c / b
                add(f"{t}_rows_stable", drop <= max_drop, f"{c} rows vs baseline {b} ({c / b - 1:+.0%})")
        if base.get("max_order_date") and cur.get("max_order_date"):
            add("freshness_not_regressed",
                date.fromisoformat(cur["max_order_date"]) >= date.fromisoformat(base["max_order_date"]),
                f"latest order {cur['max_order_date']} vs baseline {base['max_order_date']}")
    return checks


def to_markdown(cur: dict, checks: list[dict], base: dict | None) -> str:
    bad = [c for c in checks if not c["ok"]]
    head = (f"**ANOMALY: {len(bad)} check(s) failed. Rolling back is recommended.**" if bad
            else "**Healthy.** All post-deploy checks passed.")
    lines = ["## Post-deploy data monitor", "", head, "",
             f"Release `{cur.get('release_version')}` (`{cur.get('git_sha')}`), "
             f"baseline `{(base or {}).get('release_version', 'none')}`.", "",
             "| Check | Result | Detail |", "| --- | --- | --- |"]
    lines += [f"| {c['check']} | {'pass' if c['ok'] else '**FAIL**'} | {c['detail']} |" for c in checks]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--url")
    src.add_argument("--warehouse")
    ap.add_argument("--baseline", default="monitoring/baseline.json")
    ap.add_argument("--max-drop", type=float, default=0.3)
    ap.add_argument("--max-age-hours", type=float, default=48.0)
    ap.add_argument("--json", dest="json_out", default="reports/monitor.json")
    ap.add_argument("--markdown", dest="md_out", default="reports/monitor.md")
    ap.add_argument("--write-baseline", help="when healthy, save this snapshot as the new baseline")
    args = ap.parse_args(argv)

    try:
        cur = snapshot_from_url(args.url) if args.url else snapshot_from_warehouse(args.warehouse)
    except Exception as exc:
        cur = {"error": str(exc), "order_rows": 0, "tables": {}}
    base = None
    if args.baseline and Path(args.baseline).exists():
        base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))

    checks = evaluate(cur, base, max_drop=args.max_drop, max_age_hours=args.max_age_hours)
    if "error" in cur:
        checks.insert(0, {"check": "reachable", "ok": False, "detail": cur["error"]})
    healthy = all(c["ok"] for c in checks)
    report = {"healthy": healthy, "current": cur, "baseline": base, "checks": checks}
    for path, text in ((args.json_out, json.dumps(report, indent=2, default=str)),
                       (args.md_out, to_markdown(cur, checks, base))):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text, encoding="utf-8")
    if healthy and args.write_baseline:
        Path(args.write_baseline).parent.mkdir(parents=True, exist_ok=True)
        Path(args.write_baseline).write_text(json.dumps(cur, indent=2, default=str), encoding="utf-8")
    print(to_markdown(cur, checks, base))
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
