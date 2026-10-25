import hashlib
import os
import sqlite3

import pandas as pd
from datasets import load_dataset

from config import ORDERS_DB_PATH

DATASET = "Zeyustun/orders"
N_ORDERS = 1000
FIRST_ORDER_NO = 10001
LATE_PERCENT = 30
QUOTA = {"delivered": 560, "shipped": 150, "canceled": 90, "unavailable": 60,
         "invoiced": 60, "processing": 60, "approved": 10, "created": 10}
IN_PROGRESS = {"shipped", "invoiced", "processing", "approved", "created"}
CARRIERS = ["FastShip", "SwiftPost", "BlueLine Express", "GlobalParcel"]
STREETS = ["Maple", "Oak", "Cedar", "Pine", "Elm", "Lake", "Hill", "River", "Sunset", "Park"]
CITIES = ["Springfield", "Riverton", "Fairview", "Georgetown", "Greenville", "Clinton"]
REQUIRED = ["order_id", "customer_id", "order_status", "order_purchase_timestamp",
            "order_approved_at", "order_delivered_carrier_date",
            "order_delivered_customer_date", "order_estimated_delivery_date"]
DATE_COLS = REQUIRED[3:]


def h(value, salt):
    return int(hashlib.sha256(f"{salt}:{value}".encode()).hexdigest(), 16)


def day(ts):
    return None if ts is None or pd.isna(ts) else ts.strftime("%Y-%m-%d")


def pick(df, status, n):
    group = df[df["order_status"] == status]
    if status == "delivered":
        return group.sample(min(n, len(group)), random_state=42)
    recent = group.sort_values("order_purchase_timestamp", ascending=False)
    if status == "shipped":
        head = recent.head(int(n * 0.7))
        rest = recent.drop(head.index)
        return pd.concat([head, rest.sample(min(n - len(head), len(rest)), random_state=42)])
    return recent.head(n)


def retime(row, today):
    def gap(col):
        delta = row[col] - row["order_purchase_timestamp"]
        return None if pd.isna(delta) else max(int(delta.days), 0)

    est_gap = max(gap("order_estimated_delivery_date") or 14, 7)
    approved_gap = gap("order_approved_at")
    shipped_gap = gap("order_delivered_carrier_date")
    needed = shipped_gap if shipped_gap is not None else (approved_gap or 0)
    k = h(row["order_id"], "late")
    if k % 100 < LATE_PERCENT:
        age = est_gap + 1 + (k // 100) % 20
    else:
        remaining = 1 + (k // 100) % max(est_gap - needed, 1)
        age = max(est_gap - remaining, needed)
    purchase = today - pd.Timedelta(days=age)

    def at(days):
        return None if days is None else purchase + pd.Timedelta(days=days)

    return {"purchase": purchase, "approved": at(approved_gap), "shipped": at(shipped_gap),
            "estimated": purchase + pd.Timedelta(days=est_gap)}


def main():
    print("Downloading Olist orders from Hugging Face...")
    df = load_dataset(DATASET, split="train").to_pandas()
    print(f"Rows: {len(df)} | Columns: {list(df.columns)}")
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise SystemExit(f"Missing columns: {missing}")

    for col in DATE_COLS:
        df[col] = pd.to_datetime(df[col], errors="coerce")

    today = pd.Timestamp.now().normalize()
    offset = today - df["order_delivered_customer_date"].max().normalize()
    for col in DATE_COLS:
        df[col] = df[col] + offset

    sample = pd.concat([pick(df, status, n) for status, n in QUOTA.items()])
    if len(sample) < N_ORDERS:
        extra = df[(df["order_status"] == "delivered") & ~df.index.isin(sample.index)]
        sample = pd.concat([sample, extra.sample(N_ORDERS - len(sample), random_state=7)])
    sample = sample.sample(frac=1, random_state=7).reset_index(drop=True)

    rows = []
    for i, r in enumerate(sample.to_dict("records")):
        status, oid = r["order_status"], r["order_id"]
        if status in IN_PROGRESS:
            t = retime(r, today)
            purchased, approved, shipped, estimated = t["purchase"], t["approved"], t["shipped"], t["estimated"]
            delivered = None
        else:
            purchased, approved = r["order_purchase_timestamp"], r["order_approved_at"]
            shipped = r["order_delivered_carrier_date"]
            delivered = r["order_delivered_customer_date"] if status == "delivered" else None
            estimated = r["order_estimated_delivery_date"] if status == "delivered" else None

        paid = approved is not None and not pd.isna(approved)
        has_shipped = shipped is not None and not pd.isna(shipped)
        refund = None
        if status in ("canceled", "unavailable"):
            if not paid:
                refund = "not_charged"
            else:
                refund = "refunded" if h(oid, "refund") % 3 else "refund_pending"

        rows.append((
            FIRST_ORDER_NO + i, oid,
            f"customer{h(r['customer_id'], 'email') % 10**8:08d}@example.com",
            status, day(purchased), day(approved), day(shipped), day(delivered), day(estimated),
            CARRIERS[h(oid, "carrier") % len(CARRIERS)] if has_shipped else None,
            f"TRK{h(oid, 'trk') % 10**10:010d}" if has_shipped else None,
            round(15 + (h(oid, "total") % 38500) / 100, 2),
            "paid" if paid else "pending", refund,
            f"{1 + h(oid, 'num') % 999} {STREETS[h(oid, 'st') % len(STREETS)]} St, "
            f"{CITIES[h(oid, 'city') % len(CITIES)]}",
            f"+1-555-01{h(oid, 'ph') % 100:02d}",
        ))

    os.makedirs(os.path.dirname(ORDERS_DB_PATH) or ".", exist_ok=True)
    con = sqlite3.connect(ORDERS_DB_PATH)
    con.executescript("""
        DROP TABLE IF EXISTS orders;
        CREATE TABLE orders(
            order_no INTEGER PRIMARY KEY, olist_order_id TEXT, customer_email TEXT,
            status TEXT, purchased_at TEXT, approved_at TEXT, shipped_at TEXT,
            delivered_at TEXT, estimated_delivery TEXT, carrier TEXT, tracking_number TEXT,
            total REAL, payment_status TEXT, refund_status TEXT,
            shipping_address TEXT, phone TEXT);
    """)
    con.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()

    cutoff = today.strftime("%Y-%m-%d")
    print(f"\nSaved {len(rows)} orders to {ORDERS_DB_PATH}")
    print("Orders per status:")
    for status, n in con.execute("SELECT status, COUNT(*) FROM orders GROUP BY status ORDER BY 2 DESC"):
        print(f"  {status:12} {n}")
    late, on_time = con.execute(
        "SELECT SUM(estimated_delivery < ?), SUM(estimated_delivery >= ?) FROM orders "
        "WHERE status IN ('shipped','invoiced','processing','approved','created')",
        (cutoff, cutoff)).fetchone()
    print(f"In-progress orders: {on_time} on time, {late} late")
    future = con.execute(
        "SELECT COUNT(*) FROM orders WHERE delivered_at > ? OR shipped_at > ? OR purchased_at > ?",
        (cutoff, cutoff, cutoff)).fetchone()[0]
    print(f"Orders with a date in the future (should be 0): {future}")
    con.close()


if __name__ == "__main__":
    main()