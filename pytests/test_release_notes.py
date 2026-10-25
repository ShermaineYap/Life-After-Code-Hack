import json

import release_notes as rn

CONTRACTS = {"fct_revenue", "kpi_dashboard", "dim_customer"}


def test_first_release():
    assert rn.bump("", {}, CONTRACTS)[0] == "v1.0.0"


def test_new_model_is_minor():
    assert rn.bump("v1.3.2", {"added": ["fct_mac"]}, CONTRACTS)[0] == "v1.4.0"


def test_removed_contract_column_is_major():
    lineage = {"schema_changes": [{"model": "fct_revenue", "removed": ["gross_revenue_myr"], "added": []}]}
    assert rn.bump("v1.3.2", lineage, CONTRACTS)[0] == "v2.0.0"


def test_removed_staging_column_is_not_major():
    lineage = {"schema_changes": [{"model": "stg_orders", "removed": ["x"], "added": []}],
               "modified": ["stg_orders"]}
    assert rn.bump("v1.3.2", lineage, CONTRACTS)[0] == "v1.3.3"


def test_logic_change_is_patch():
    assert rn.bump("v1.3.2", {"modified": ["kpi_dashboard"], "schema_changes": []}, CONTRACTS)[0] == "v1.3.3"


def test_contract_models_read_from_manifest(built):
    manifest = json.loads(built["manifest"].read_text())
    assert rn.contract_models(manifest) == CONTRACTS


def test_writes_notes_and_evidence(tmp_path):
    (tmp_path / "lineage.json").write_text(json.dumps({
        "summary": {"downstream_models": 1, "breaking_changes": 0, "models_with_test_gaps": 0},
        "added": ["fct_monthly_active_customers"], "modified": [], "schema_changes": [],
        "dashboard_tiles": [{"name": "t", "label": "Dashboard tile: Active customers", "owner": "Growth"}]}))
    rc = rn.main(["--previous", "v1.0.0", "--lineage", str(tmp_path / "lineage.json"),
                  "--pii", str(tmp_path / "none.json"), "--sci", str(tmp_path / "none.json"),
                  "--manifest", str(tmp_path / "none.json"), "--out-dir", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / "version.txt").read_text().strip() == "v1.1.0"
    notes = (tmp_path / "release.md").read_text()
    assert "fct_monthly_active_customers" in notes and "Active customers" in notes
    assert json.loads((tmp_path / "evidence.json").read_text())["version"] == "v1.1.0"
