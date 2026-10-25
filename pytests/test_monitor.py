import json
from datetime import datetime, timedelta, timezone

import monitor


def test_identical_release_is_healthy(built):
    snap = monitor.snapshot_from_warehouse(str(built["warehouse"]))
    checks = monitor.evaluate(snap, snap)
    assert all(c["ok"] for c in checks), checks


def test_row_collapse_is_an_anomaly(built):
    base = monitor.snapshot_from_warehouse(str(built["warehouse"]))
    cur = dict(base, order_rows=base["order_rows"] // 10)
    bad = [c["check"] for c in monitor.evaluate(cur, base) if not c["ok"]]
    assert bad == ["order_rows_stable"]


def test_freshness_regression_and_stale_build(built):
    base = monitor.snapshot_from_warehouse(str(built["warehouse"]))
    cur = dict(base, max_order_date="2026-01-31")
    later = datetime.now(timezone.utc) + timedelta(days=5)
    bad = {c["check"] for c in monitor.evaluate(cur, base, now=later) if not c["ok"]}
    assert bad == {"freshness_not_regressed", "build_fresh"}


def test_cli_writes_baseline_only_when_healthy(built, tmp_path):
    baseline = tmp_path / "baseline.json"
    args = ["--warehouse", str(built["warehouse"]), "--baseline", str(baseline),
            "--json", str(tmp_path / "m.json"), "--markdown", str(tmp_path / "m.md"),
            "--write-baseline", str(baseline)]
    assert monitor.main(args) == 0
    snap = json.loads(baseline.read_text())
    snap["order_rows"] *= 10                       # pretend the last release had 10x the rows
    baseline.write_text(json.dumps(snap))
    assert monitor.main(args) == 1
    assert json.loads(baseline.read_text())["order_rows"] == snap["order_rows"]  # not overwritten
    assert "ANOMALY" in (tmp_path / "m.md").read_text()


def test_unreachable_service_fails(tmp_path, monkeypatch):
    real = monitor.snapshot_from_url
    monkeypatch.setattr(monitor, "snapshot_from_url", lambda url: real(url, retries=1, wait_s=0))
    rc = monitor.main(["--url", "http://127.0.0.1:9", "--baseline", str(tmp_path / "none.json"),
                       "--json", str(tmp_path / "m.json"), "--markdown", str(tmp_path / "m.md")])
    assert rc == 1
    report = json.loads((tmp_path / "m.json").read_text())
    assert report["checks"][0]["check"] == "reachable" and not report["checks"][0]["ok"]
