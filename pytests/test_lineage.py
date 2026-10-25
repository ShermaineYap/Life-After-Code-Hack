import copy
import json

import lineage_impact as li


def _manifests(built):
    state = json.loads(built["manifest"].read_text())
    return copy.deepcopy(state), state


def test_no_change_is_clean(built):
    new, state = _manifests(built)
    r = li.analyse(new, state)
    assert r["summary"]["models_modified"] == 0
    assert r["summary"]["breaking_changes"] == 0
    assert r["summary"]["slim_build_models"] == 0


def test_column_rename_is_a_contract_break(built):
    new, state = _manifests(built)
    node = new["nodes"]["model.downstream.stg_orders"]
    node["checksum"]["checksum"] = "changed"
    cols = node["columns"]
    cols["order_amount_myr"] = cols.pop("amount_myr")
    node["raw_code"] = node["raw_code"].replace("amount_myr", "amount_myr as order_amount_myr")

    r = li.analyse(new, state)
    broken_by = {b["referenced_by"] for b in r["breaking_changes"]}
    assert broken_by == {"fct_revenue", "int_customer_orders"}
    assert r["schema_changes"] == [{"model": "stg_orders", "removed": ["amount_myr"],
                                    "added": ["order_amount_myr"]}]
    assert {t["name"] for t in r["dashboard_tiles"]} == {
        "tile_revenue_trend", "tile_revenue_by_country", "tile_new_customers", "tile_customer_segments"}
    assert "fct_revenue" in r["downstream_models"]
    assert "contract break" in li.to_markdown(r)


def test_leaf_change_has_small_blast_radius(built):
    new, state = _manifests(built)
    new["nodes"]["model.downstream.kpi_dashboard"]["checksum"]["checksum"] = "changed"
    r = li.analyse(new, state)
    assert r["modified"] == ["kpi_dashboard"]
    assert r["downstream_models"] == []
    assert {t["name"] for t in r["dashboard_tiles"]} == {"tile_revenue_trend", "tile_new_customers"}
    assert r["summary"]["slim_build_share"] < 0.2


def test_new_model_without_tests_is_flagged(built):
    new, state = _manifests(built)
    uid = "model.downstream.fct_monthly_active_customers"
    new["nodes"][uid] = {
        "resource_type": "model", "name": "fct_monthly_active_customers",
        "original_file_path": "models/marts/fct_monthly_active_customers.sql",
        "checksum": {"checksum": "x"}, "raw_code": "select 1",
        "columns": {"activity_month": {}, "active_customers": {}},
    }
    r = li.analyse(new, state)
    gap = next(g for g in r["test_gaps"] if g["model"] == "fct_monthly_active_customers")
    assert gap["key_columns_missing_unique_or_not_null"] == ["activity_month"]
    assert set(gap["untested_columns"]) == {"activity_month", "active_customers"}


def test_first_release_does_not_nag_about_descriptive_columns(built):
    new, _ = _manifests(built)
    r = li.analyse(new, None)
    assert r["summary"]["first_release"] is True
    assert r["summary"]["models_added"] == r["summary"]["models_total"]
    assert r["test_gaps"] == []  # every key column in the project is tested


def test_cli_exit_code(built, tmp_path):
    new, state = _manifests(built)
    node = new["nodes"]["model.downstream.stg_orders"]
    node["checksum"]["checksum"] = "changed"
    node["columns"].pop("amount_myr")
    (tmp_path / "new.json").write_text(json.dumps(new))
    (tmp_path / "state.json").write_text(json.dumps(state))
    args = ["--manifest", str(tmp_path / "new.json"), "--state-manifest", str(tmp_path / "state.json"),
            "--json", str(tmp_path / "l.json"), "--markdown", str(tmp_path / "l.md")]
    assert li.main(args) == 0
    assert li.main(args + ["--fail-on-breaking"]) == 2
