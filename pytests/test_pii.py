import duckdb

import pii_sentinel as ps


def test_clean_warehouse_passes(built):
    assert ps.scan(str(built["warehouse"])) == []


def test_raw_email_in_a_mart_is_blocked(warehouse_copy):
    con = duckdb.connect(str(warehouse_copy))
    con.execute("create view marts.leak as select customer_id, email from raw.raw_customers")
    con.close()
    findings = ps.scan(str(warehouse_copy))
    assert any(f["table"] == "leak" and f["column"] == "email" and f["severity"] == "high" for f in findings)


def test_pii_behind_an_innocent_name_is_found_by_value(warehouse_copy):
    con = duckdb.connect(str(warehouse_copy))
    con.execute("create view marts.contacts as select customer_id, phone as contact from raw.raw_customers")
    con.close()
    f = next(x for x in ps.scan(str(warehouse_copy)) if x["table"] == "contacts")
    assert f["column"] == "contact"
    assert any("look like phone" in r for r in f["reasons"])


def test_cli_exit_code_and_junit(warehouse_copy, tmp_path):
    con = duckdb.connect(str(warehouse_copy))
    con.execute("create view staging.oops as select email from raw.raw_customers")
    con.close()
    rc = ps.main(["--warehouse", str(warehouse_copy), "--json", str(tmp_path / "p.json"),
                  "--markdown", str(tmp_path / "p.md"), "--junit", str(tmp_path / "p.xml")])
    assert rc == 1
    assert 'failures="1"' in (tmp_path / "p.xml").read_text()


def test_luhn():
    assert ps._luhn("4111 1111 1111 1111")
    assert not ps._luhn("4111 1111 1111 1112")
