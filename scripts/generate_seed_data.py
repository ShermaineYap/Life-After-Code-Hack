"""Generate the deterministic demo dataset for Downstream.

Produces three CSV seeds under ``seeds/`` describing a fictional Malaysian
online store: customers (with PII that must never reach a mart), orders and
product events. The generator is seeded, so every run produces identical
files and CI results are reproducible.

Usage:
    python scripts/generate_seed_data.py
"""

from __future__ import annotations

import csv
import random
from datetime import date, datetime, timedelta
from pathlib import Path

SEED = 20261027
N_CUSTOMERS = 240
START = date(2025, 1, 1)
END = date(2026, 9, 30)
COUNTRIES = ["MY", "MY", "MY", "SG", "ID", "TH"]
FIRST = ["Aisyah", "Wei Ling", "Arjun", "Nurul", "Jun Hao", "Priya", "Daniel",
         "Siti", "Kai", "Mei", "Farah", "Ravi", "Hana", "Zhi Wei", "Amir"]
LAST = ["Tan", "Lim", "Abdullah", "Kumar", "Wong", "Rahman", "Lee", "Ng",
        "Ismail", "Chong", "Raj", "Ong", "Yusof", "Chan", "Goh"]

ROOT = Path(__file__).resolve().parent.parent
SEEDS = ROOT / "seeds"


def _rand_date(rng: random.Random, lo: date, hi: date) -> date:
    return lo + timedelta(days=rng.randint(0, (hi - lo).days))


def build() -> dict[str, list[dict]]:
    rng = random.Random(SEED)
    customers, orders, events = [], [], []

    for cid in range(1, N_CUSTOMERS + 1):
        first, last = rng.choice(FIRST), rng.choice(LAST)
        is_test = cid % 37 == 0  # a handful of internal QA accounts
        handle = f"{first}.{last}".lower().replace(" ", "")
        domain = "qa.example-store.test" if is_test else rng.choice(
            ["mail.test", "inbox.test", "post.test"])
        customers.append({
            "customer_id": cid,
            "full_name": f"{first} {last}",
            "email": f"{handle}{cid}@{domain}",
            "phone": f"+60-1{rng.randint(0, 9)}-{rng.randint(100, 999)} {rng.randint(1000, 9999)}",
            "country": rng.choice(COUNTRIES),
            "signup_date": _rand_date(rng, START, END - timedelta(days=30)).isoformat(),
            "is_test_account": str(is_test).lower(),
        })

    order_id, event_id = 1, 1
    for c in customers:
        signup = date.fromisoformat(c["signup_date"])
        activity = rng.choice([0.2, 0.5, 1.0, 2.0, 4.0])  # orders per month
        months_active = max(1, (END - signup).days // 30)
        for _ in range(max(1, int(activity * months_active * rng.uniform(0.4, 1.0)))):
            od = _rand_date(rng, signup, END)
            status = rng.choices(["completed", "refunded", "cancelled"], [88, 7, 5])[0]
            orders.append({
                "order_id": order_id,
                "customer_id": c["customer_id"],
                "order_date": od.isoformat(),
                "status": status,
                "amount_myr": f"{rng.uniform(15, 480):.2f}",
            })
            order_id += 1
        for _ in range(int(activity * months_active * 3)):
            ed = _rand_date(rng, signup, END)
            ts = datetime(ed.year, ed.month, ed.day, rng.randint(0, 23), rng.randint(0, 59))
            events.append({
                "event_id": event_id,
                "customer_id": c["customer_id"],
                "event_type": rng.choices(["login", "page_view", "add_to_cart"], [5, 8, 2])[0],
                "event_ts": ts.isoformat(sep=" "),
            })
            event_id += 1

    orders.sort(key=lambda r: (r["order_date"], r["order_id"]))
    events.sort(key=lambda r: (r["event_ts"], r["event_id"]))
    return {"raw_customers": customers, "raw_orders": orders, "raw_events": events}


def main() -> None:
    SEEDS.mkdir(exist_ok=True)
    for name, rows in build().items():
        path = SEEDS / f"{name}.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print(f"wrote {path.relative_to(ROOT)} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
