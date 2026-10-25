"""Downstream KPI dashboard.

A small FastAPI service that reads the dbt-built DuckDB warehouse baked into
its container image and serves:

    /                      the KPI dashboard (server-rendered HTML + SVG)
    /health                build + row-count snapshot used by the post-deploy monitor
    /api/kpi               monthly KPI rows
    /api/revenue-by-country
    /api/segments

Each deployed revision carries exactly one data release, so rolling back the
Cloud Run revision rolls back the data.
"""

from __future__ import annotations

import html
import os
from datetime import date, datetime, timezone
from functools import lru_cache

import duckdb
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

WAREHOUSE = os.environ.get("WAREHOUSE_PATH", "target/warehouse.duckdb")
MART_TABLES = ["dim_customer", "fct_revenue", "kpi_dashboard"]
STORE_NAME = os.environ.get("STORE_NAME", "Kedai Hulu")

app = FastAPI(title="Downstream KPI dashboard", docs_url=None, redoc_url=None)


def _con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(WAREHOUSE, read_only=True)


def _rows(sql: str) -> list[dict]:
    con = _con()
    try:
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        con.close()


def _jsonable(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        out.append({k: (v.isoformat() if isinstance(v, (date, datetime)) else
                        float(v) if hasattr(v, "as_integer_ratio") and not isinstance(v, (int, bool)) else v)
                    for k, v in r.items()})
    return out


def health_snapshot() -> dict:
    con = _con()
    try:
        info = con.execute("select release_version, git_sha, built_at, order_rows, max_order_date "
                           "from marts.meta_build_info").fetchone()
        tables = {t: con.execute(f"select count(*) from marts.{t}").fetchone()[0] for t in MART_TABLES}
    finally:
        con.close()
    return {
        "status": "ok" if info and info[3] and all(tables.values()) else "degraded",
        "release_version": info[0], "git_sha": info[1],
        "built_at": info[2].replace(tzinfo=timezone.utc).isoformat() if info[2] else None,
        "order_rows": info[3], "max_order_date": info[4].isoformat() if info[4] else None,
        "tables": tables,
    }


@app.get("/health")
def health() -> JSONResponse:
    try:
        snap = health_snapshot()
    except Exception as exc:  # warehouse missing or corrupt
        return JSONResponse({"status": "error", "detail": str(exc)}, status_code=503)
    return JSONResponse(snap, status_code=200 if snap["status"] == "ok" else 503)


@app.get("/api/kpi")
def api_kpi() -> list[dict]:
    return _jsonable(_rows("select * from marts.kpi_dashboard order by order_month"))


@app.get("/api/revenue-by-country")
def api_country() -> list[dict]:
    return _jsonable(_rows(
        "select country, sum(net_revenue_myr) as net_revenue_myr, sum(completed_orders) as orders "
        "from marts.fct_revenue group by country order by net_revenue_myr desc"))


@app.get("/api/segments")
def api_segments() -> list[dict]:
    return _jsonable(_rows(
        "select customer_segment, count(*) as customers, sum(lifetime_revenue_myr) as revenue_myr "
        "from marts.dim_customer where not is_test_account group by 1 order by revenue_myr desc"))


# ---------------------------------------------------------------- rendering ----

def _ringgit(x: float) -> str:
    return f"RM {x:,.0f}"


def _month(d: str) -> str:
    return datetime.fromisoformat(d).strftime("%b %Y")


def _river(kpi: list[dict]) -> str:
    """Full-width area chart of net revenue by month."""
    w, h, left, right, top, bottom = 960, 300, 64, 16, 16, 36
    vals = [float(r["net_revenue_myr"]) for r in kpi]
    if not vals:
        return ""
    hi = max(vals) * 1.1
    n = len(vals)
    x = lambda i: left + i * (w - left - right) / max(n - 1, 1)  # noqa: E731
    y = lambda v: top + (1 - v / hi) * (h - top - bottom)  # noqa: E731
    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(vals))
    area = f"M{x(0):.1f},{y(0):.1f} L{pts.replace(' ', ' L')} L{x(n - 1):.1f},{h - bottom} L{x(0):.1f},{h - bottom} Z"
    grid = ""
    step = 10 ** (len(str(int(hi))) - 1)
    step = step * 2 if hi / step < 4 else step * 5 if hi / step > 8 else step
    v = 0
    while v <= hi:
        grid += (f'<line x1="{left}" x2="{w - right}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="grid"/>'
                 f'<text x="{left - 8}" y="{y(v) + 4:.1f}" class="tick" text-anchor="end">{v / 1000:,.0f}k</text>')
        v += step
    labels = "".join(
        f'<text x="{x(i):.1f}" y="{h - 12}" class="tick{"" if i in (0, n - 1) else " mid"}" '
        f'text-anchor="{"end" if i == n - 1 else "start" if i == 0 else "middle"}">{_month(r["order_month"])}</text>'
        for i, r in enumerate(kpi) if (i % 3 == 0 and n - 1 - i >= 2) or i == n - 1)
    last = (f'<circle cx="{x(n - 1):.1f}" cy="{y(vals[-1]):.1f}" r="4.5" class="dot"/>')
    return (f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Net revenue by month">{grid}'
            f'<path d="{area}" class="area"/><polyline points="{pts}" class="line"/>{last}{labels}</svg>')


def _bars(rows: list[dict], label: str, value: str, fmt) -> str:
    top = max((float(r[value]) for r in rows), default=1) or 1
    out = []
    for r in rows:
        share = float(r[value]) / top
        out.append(f'<div class="bar"><span class="bar-label">{html.escape(str(r[label]))}</span>'
                   f'<span class="bar-track"><span class="bar-fill" style="width:{share * 100:.1f}%"></span></span>'
                   f'<span class="bar-value">{fmt(float(r[value]))}</span></div>')
    return "".join(out)


def _spark(kpi: list[dict], column: str = "new_customers") -> str:
    w, h = 420, 90
    vals = [int(r.get(column) or 0) for r in kpi]
    if not vals:
        return ""
    hi = max(vals) or 1
    pts = " ".join(f"{6 + i * (w - 12) / max(len(vals) - 1, 1):.1f},{8 + (1 - v / hi) * (h - 16):.1f}"
                   for i, v in enumerate(vals))
    lx, ly = pts.split(" ")[-1].split(",")
    return (f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{column.replace('_', ' ')} per month">'
            f'<polyline points="{pts}" class="line thin"/><circle cx="{lx}" cy="{ly}" r="4" class="dot"/></svg>')


COUNTRY_NAMES = {"MY": "Malaysia", "SG": "Singapore", "ID": "Indonesia", "TH": "Thailand"}
SEGMENT_NAMES = {"vip": "VIP (RM 5k+)", "repeat": "Repeat (3+ orders)", "one_time": "One order", "prospect": "No orders yet"}

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{store} KPIs</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Schibsted+Grotesk:wght@400;500;700;800&display=swap" rel="stylesheet">
<style>
:root{{--paper:#F5F7F6;--ink:#18283A;--quiet:#5A6B78;--river:#0E7470;--rule:#CBD7D4;--amber:#B7740B;--alert:#B3262E}}
@media (prefers-color-scheme:dark){{:root{{--paper:#121C24;--ink:#E6EEEC;--quiet:#9AACB4;--river:#4FB8B1;--rule:#2C3C46;--amber:#E0A23A;--alert:#F07178}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.5 "Schibsted Grotesk",system-ui,sans-serif}}
main{{max-width:1040px;margin:0 auto;padding:32px 24px 64px}}
header{{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:baseline;gap:12px;padding-bottom:20px;border-bottom:1px solid var(--rule)}}
h1{{font-size:1.25rem;font-weight:700;margin:0}}
.stamp{{color:var(--quiet);font-size:.9rem}}
.stamp b{{color:var(--ink);font-weight:500}}
.status{{display:inline-block;width:9px;height:9px;border-radius:50%;background:var(--river);margin-right:6px;vertical-align:1px}}
.status.bad{{background:var(--alert)}}
.hero{{padding:36px 0 8px}}
.hero h2{{font-size:clamp(2rem,5vw,3.4rem);font-weight:800;line-height:1.05;margin:0;letter-spacing:-.02em;max-width:22ch}}
.hero p{{color:var(--quiet);margin:10px 0 0;max-width:60ch}}
.delta-up{{color:var(--river)}} .delta-down{{color:var(--amber)}}
svg{{width:100%;height:auto;display:block}}
.grid{{stroke:var(--rule);stroke-width:1}}
.tick{{fill:var(--quiet);font-size:12px}}
.area{{fill:var(--river);opacity:.14}}
.line{{fill:none;stroke:var(--river);stroke-width:2.5;stroke-linejoin:round}}
.line.thin{{stroke-width:2}}
.dot{{fill:var(--river)}}
.cols{{display:grid;grid-template-columns:1fr 1fr;gap:48px;margin-top:40px;padding-top:28px;border-top:1px solid var(--rule)}}
h3{{font-size:1rem;font-weight:700;margin:0 0 4px}}
.sub{{color:var(--quiet);font-size:.9rem;margin:0 0 16px}}
.figure{{margin:0;font-size:2rem;font-weight:800;line-height:1.1}}
.bar{{display:grid;grid-template-columns:9.5rem 1fr 6.5rem;align-items:center;gap:12px;padding:6px 0}}
.bar-label{{font-size:.95rem}}
.bar-track{{height:10px;background:color-mix(in srgb,var(--rule) 55%,transparent);border-radius:5px;overflow:hidden}}
.bar-fill{{display:block;height:100%;background:var(--river);border-radius:5px}}
.bar-value{{text-align:right;font-size:.95rem;white-space:nowrap}}
footer{{margin-top:48px;color:var(--quiet);font-size:.85rem}}
@media (max-width:720px){{.cols{{grid-template-columns:1fr;gap:32px}}.bar{{grid-template-columns:6.5rem 1fr auto;gap:10px}}.bar-label,.bar-value{{font-size:.875rem}}.tick{{font-size:26px}}.tick.mid{{display:none}}}}
</style></head>
<body><main>
<header><h1>{store}</h1>
<div class="stamp"><span class="status{status_cls}"></span>Data release <b>{version}</b>, built {built}, with orders through {through}</div></header>
<section class="hero" id="revenue">
<h2>{headline}</h2>
<p>{sub}</p>
{river}
</section>
<div class="cols">
<section id="country"><h3>Revenue by country</h3><p class="sub">Net of refunds, all months, test accounts excluded.</p>{country}</section>
<section id="segments"><h3>Customer segments</h3><p class="sub">Lifetime revenue by segment.</p>{segments}</section>
</div>
<div class="cols">
<section id="customers"><h3>New customers per month</h3><p class="sub">{new_last} in {last_month}, first-time buyers only.</p>{spark}</section>
<section><h3>Average order value</h3><p class="sub">Completed orders, {last_month}.</p><p class="figure">{aov}</p></section>
</div>
{active}<footer>Every number on this page comes from release {version}. Rolling back the release rolls back the numbers.</footer>
</main></body></html>"""


def _active_panel(kpi: list[dict], last: dict) -> str:
    """Shown once a release adds kpi_dashboard.active_customers (demo issue 1)."""
    if "active_customers" not in last:
        return ""
    return ('<div class="cols"><section id="active"><h3>Monthly active customers</h3>'
            f'<p class="sub">Real customers with an order or product event in {_month(last["order_month"])}.</p>'
            f'<p class="figure">{int(last["active_customers"] or 0):,}</p></section>'
            f'<section><h3>Active customers per month</h3><p class="sub">Since {_month(kpi[0]["order_month"])}.</p>'
            f'{_spark(kpi, "active_customers")}</section></div>')


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    try:
        kpi = api_kpi()
        snap = health_snapshot()
    except Exception as exc:
        return HTMLResponse(f"<p>The warehouse could not be read: {html.escape(str(exc))}. "
                            "Rebuild it with <code>dbt build</code> and restart the service.</p>", status_code=503)
    last, prev = (kpi[-1], kpi[-2]) if len(kpi) > 1 else (kpi[-1], None)
    change = ""
    if prev and float(prev["net_revenue_myr"]):
        pct = float(last["net_revenue_myr"]) / float(prev["net_revenue_myr"]) - 1
        word = "up" if pct >= 0 else "down"
        change = f'<span class="delta-{word}">{word} {abs(pct):.0%}</span> on {_month(prev["order_month"])}'
    built = snap["built_at"][:16].replace("T", " ") + " UTC" if snap["built_at"] else "unknown"
    page = PAGE.format(
        store=html.escape(STORE_NAME),
        status_cls="" if snap["status"] == "ok" else " bad",
        version=html.escape(str(snap["release_version"])), built=built,
        through=snap["max_order_date"] or "unknown",
        headline=f'{_ringgit(float(last["net_revenue_myr"]))} net revenue in {datetime.fromisoformat(last["order_month"]).strftime("%B")}',
        sub=(f"{change}. " if change else "") + f'{int(last["orders"])} completed orders. Net revenue by month since {_month(kpi[0]["order_month"])}:',
        river=_river(kpi),
        country=_bars([dict(r, country=COUNTRY_NAMES.get(r["country"], r["country"])) for r in api_country()],
                     "country", "net_revenue_myr", _ringgit),
        segments=_bars([dict(r, customer_segment=SEGMENT_NAMES.get(r["customer_segment"], r["customer_segment"]))
                        for r in api_segments()], "customer_segment", "revenue_myr", _ringgit),
        spark=_spark(kpi), new_last=int(last["new_customers"]), last_month=_month(last["order_month"]),
        aov=_ringgit(float(last["avg_order_value_myr"] or 0)),
        active=_active_panel(kpi, last),
    )
    return HTMLResponse(page)


@lru_cache
def _port() -> int:
    return int(os.environ.get("PORT", "8080"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=_port())
