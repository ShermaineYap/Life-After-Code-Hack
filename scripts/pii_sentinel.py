"""PII Sentinel: fail the pipeline if personal data reaches a governed schema.

Scans every table and view in the built DuckDB warehouse outside the raw
landing schema and flags columns that hold personal data, using two signals:

1. the column name (email, phone, NRIC/IC number, passport, card, address);
2. a sample of the values (email, phone, Malaysian NRIC and card-number
   patterns), which catches PII hiding behind an innocent column name.

Hashed columns (64 hex chars) never match the value patterns, so a correctly
masked column such as ``email_hash`` passes.

Outputs a JSON report, a Markdown summary for the merge request, and a JUnit
XML file that GitLab shows in the pipeline's Tests tab.

Usage:
    python scripts/pii_sentinel.py --warehouse target/warehouse.duckdb \
        --json reports/pii.json --markdown reports/pii.md --junit reports/pii-junit.xml

Exit codes: 0 = clean, 1 = high-severity PII found in a governed schema.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from xml.sax.saxutils import escape

import duckdb

EXEMPT_SCHEMAS = {"raw", "information_schema", "pg_catalog", "main"}
SAMPLE_ROWS = 200

NAME_RULES = [
    ("email", re.compile(r"(^|_)e?_?mail(_address)?$", re.I), "high"),
    ("phone", re.compile(r"(^|_)(phone|mobile|msisdn|tel)(_no|_number)?$", re.I), "high"),
    ("national_id", re.compile(r"(^|_)(nric|ic_no|ic_number|mykad|ssn|national_id)$", re.I), "high"),
    ("passport", re.compile(r"(^|_)passport(_no|_number)?$", re.I), "high"),
    ("payment_card", re.compile(r"(^|_)(card_number|pan|credit_card)$", re.I), "high"),
    ("address", re.compile(r"(^|_)(street|address|postcode|postal_code)$", re.I), "medium"),
]
VALUE_RULES = [
    ("email", re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$"), "high"),
    ("phone", re.compile(r"^\+?\d{1,3}[-\s]?\d{1,4}[-\s]?\d{3,4}[-\s]?\d{3,4}$"), "high"),
    ("national_id", re.compile(r"^\d{6}-\d{2}-\d{4}$"), "high"),
    ("payment_card", re.compile(r"^(?:\d[ -]?){13,19}$"), "high"),
]
MATCH_SHARE = 0.5  # a value rule fires when at least half the sampled values match


def _luhn(digits: str) -> bool:
    nums = [int(d) for d in re.sub(r"\D", "", digits)][::-1]
    total = sum(n if i % 2 == 0 else (n * 2 - 9 if n * 2 > 9 else n * 2) for i, n in enumerate(nums))
    return len(nums) >= 13 and total % 10 == 0


def scan(warehouse: str) -> list[dict]:
    con = duckdb.connect(warehouse, read_only=True)
    cols = con.execute("""
        select table_schema, table_name, column_name, data_type
        from information_schema.columns
        order by table_schema, table_name, ordinal_position
    """).fetchall()
    findings = []
    for schema, table, column, dtype in cols:
        if schema in EXEMPT_SCHEMAS:
            continue
        reasons = []
        severity = None
        for label, rule, sev in NAME_RULES:
            if rule.search(column):
                reasons.append(f"column name looks like {label}")
                severity = "high" if sev == "high" or severity == "high" else sev
        if "CHAR" in dtype.upper() or "TEXT" in dtype.upper() or dtype.upper() == "VARCHAR":
            values = [r[0] for r in con.execute(
                f'select cast("{column}" as varchar) from "{schema}"."{table}" '
                f'where "{column}" is not null limit {SAMPLE_ROWS}').fetchall()]
            if values:
                for label, rule, sev in VALUE_RULES:
                    hits = [v for v in values if rule.match(v.strip())]
                    if label == "payment_card":
                        hits = [v for v in hits if _luhn(v)]
                    if len(hits) / len(values) >= MATCH_SHARE:
                        reasons.append(f"{len(hits)}/{len(values)} sampled values look like {label}")
                        severity = "high"
        if reasons:
            findings.append({
                "schema": schema, "table": table, "column": column,
                "severity": severity or "medium", "reasons": reasons,
                "fix": (f"Drop `{column}` from {schema}.{table}, or replace it with a salted hash "
                        f"(see stg_customers.email_hash) and mark it `meta: {{pii: masked}}`."),
            })
    con.close()
    return findings


def to_markdown(findings: list[dict]) -> str:
    high = [f for f in findings if f["severity"] == "high"]
    if not findings:
        return "## PII Sentinel\n\nNo personal data found outside the raw landing schema.\n"
    lines = ["## PII Sentinel", "",
             f"**{len(high)} high-severity finding(s)**, {len(findings) - len(high)} medium.", "",
             "| Severity | Column | Why | Fix |", "| --- | --- | --- | --- |"]
    for f in findings:
        lines.append(f"| {f['severity']} | `{f['schema']}.{f['table']}.{f['column']}` | "
                     f"{'; '.join(f['reasons'])} | {f['fix']} |")
    return "\n".join(lines) + "\n"


def to_junit(findings: list[dict]) -> str:
    high = [f for f in findings if f["severity"] == "high"]
    cases = ['<testcase classname="pii_sentinel" name="no_pii_outside_raw">'
             + "".join(f'<failure message="{escape(f["schema"] + "." + f["table"] + "." + f["column"])}">'
                       f'{escape("; ".join(f["reasons"]))}</failure>' for f in high)
             + "</testcase>"]
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<testsuite name="pii_sentinel" '
            f'tests="1" failures="{1 if high else 0}">{"".join(cases)}</testsuite>\n')


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--warehouse", default="target/warehouse.duckdb")
    ap.add_argument("--json", dest="json_out", default="reports/pii.json")
    ap.add_argument("--markdown", dest="md_out", default="reports/pii.md")
    ap.add_argument("--junit", dest="junit_out", default="reports/pii-junit.xml")
    args = ap.parse_args(argv)

    findings = scan(args.warehouse)
    outputs = {
        args.json_out: json.dumps({"findings": findings}, indent=2),
        args.md_out: to_markdown(findings),
        args.junit_out: to_junit(findings),
    }
    for path, text in outputs.items():
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text, encoding="utf-8")
    print(to_markdown(findings))
    return 1 if any(f["severity"] == "high" for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
