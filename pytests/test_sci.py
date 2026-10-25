import json

import pytest

import sci


def test_formula_matches_hand_calculation():
    r = sci.compute(3600, vcpus=2, memory_gb=4, utilisation=0.5, region="asia-southeast1")
    watts = 2 * (0.71 + 0.5 * (4.26 - 0.71)) + 4 * 0.392
    energy = watts * 1.1 / 1000                      # kWh for one hour
    assert r["energy_kwh"] == pytest.approx(energy)
    assert r["operational_gco2e"] == pytest.approx(energy * 366)
    embodied = 1200 * 1000 * (3600 / (4 * 365 * 24 * 3600)) * (2 / 48)
    assert r["embodied_gco2e"] == pytest.approx(embodied)
    assert r["sci_gco2e_per_release"] == pytest.approx(energy * 366 + embodied)


def test_cleaner_region_lowers_operational_only():
    sg = sci.compute(100, region="asia-southeast1")
    fi = sci.compute(100, region="europe-north1")
    assert fi["operational_gco2e"] < sg["operational_gco2e"] / 10
    assert fi["embodied_gco2e"] == pytest.approx(sg["embodied_gco2e"])


def test_reads_dbt_run_results(built):
    elapsed, nodes = sci.from_run_results(str(built["run_results"]))
    assert elapsed > 0
    assert nodes == 11  # 8 models + 3 seeds


def test_baseline_comparison(tmp_path):
    base = {"current": sci.compute(10.0, nodes=11, label="full refresh")}
    (tmp_path / "base.json").write_text(json.dumps(base))
    rr = {"elapsed_time": 2.5, "results": [{"unique_id": "model.downstream.kpi_dashboard", "status": "success"}]}
    (tmp_path / "rr.json").write_text(json.dumps(rr))
    out = tmp_path / "sci.json"
    sci.main(["--run-results", str(tmp_path / "rr.json"), "--label", "slim", "--baseline",
              str(tmp_path / "base.json"), "--json", str(out), "--markdown", str(tmp_path / "sci.md")])
    report = json.loads(out.read_text())
    assert report["reduction_vs_baseline"] == pytest.approx(0.75)
    assert "75% lower SCI" in (tmp_path / "sci.md").read_text()
