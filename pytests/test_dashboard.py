import pytest
from fastapi.testclient import TestClient

import dashboard.app as dash


@pytest.fixture()
def client(built, monkeypatch):
    monkeypatch.setattr(dash, "WAREHOUSE", str(built["warehouse"]))
    return TestClient(dash.app)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["order_rows"] == 3297
    assert set(body["tables"]) == {"dim_customer", "fct_revenue", "kpi_dashboard"}


def test_kpi_api(client):
    rows = client.get("/api/kpi").json()
    assert len(rows) == 21
    assert rows[-1]["order_month"] == "2026-09-01"
    assert isinstance(rows[-1]["net_revenue_myr"], float)


def test_page_renders_release_and_tiles(client):
    html = client.get("/").text
    assert "Data release" in html and "net revenue in September" in html
    for anchor in ('id="revenue"', 'id="country"', 'id="segments"', 'id="customers"'):
        assert anchor in html
    assert "Malaysia" in html


def test_missing_warehouse_is_a_clear_503(monkeypatch, tmp_path):
    monkeypatch.setattr(dash, "WAREHOUSE", str(tmp_path / "missing.duckdb"))
    c = TestClient(dash.app)
    assert c.get("/health").status_code == 503
    r = c.get("/")
    assert r.status_code == 503 and "dbt build" in r.text
