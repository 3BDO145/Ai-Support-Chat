import hmac
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import date

from config import ORDERS_DB_PATH as DB_PATH

BEFORE_SHIPPING = ("created", "approved", "invoiced", "processing")
PHONE_ALLOWED = BEFORE_SHIPPING + ("shipped",)
IN_PROGRESS = BEFORE_SHIPPING + ("shipped",)

REASONS = {
    "shipped": "it has already been handed to the carrier",
    "delivered": "it has already been delivered",
    "canceled": "the order is canceled",
    "unavailable": "the order could not be fulfilled",
}
STATUS_LABEL = {
    "created": "received",
    "approved": "payment approved",
    "invoiced": "invoiced",
    "processing": "being prepared",
}
REFUND_TEXT = {
    "refunded": " Your refund has been issued.",
    "refund_pending": " Your refund is being processed.",
    "not_charged": " You were not charged for this order.",
}


@contextmanager
def _db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init():
    if not os.path.exists(DB_PATH):
        raise RuntimeError(f"{DB_PATH} not found. Run: python app/prepare_orders.py")
    with _db() as c:
        columns = {r["name"] for r in c.execute("PRAGMA table_info(orders)")}
        if not {"shipping_address", "phone"} <= columns:
            raise RuntimeError("orders.db is outdated. Run: python app/prepare_orders.py")
        c.execute(
            "CREATE TABLE IF NOT EXISTS order_changes("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, order_no INTEGER, field TEXT, "
            "old_value TEXT, new_value TEXT, actor TEXT, conv_id TEXT, created TEXT)")


def _fail(code, message=""):
    return {"ok": False, "error": code, "message": message}


def _find(conn, order_no, email):
    try:
        number = int(str(order_no).strip().lstrip("#"))
    except ValueError:
        return None
    row = conn.execute("SELECT * FROM orders WHERE order_no=?", (number,)).fetchone()
    if row is None:
        return None
    given = (email or "").strip().lower().encode()
    if not hmac.compare_digest(row["customer_email"].lower().encode(), given):
        return None
    return row


def _audit(conn, order_no, field, old, new, conv_id, actor):
    conn.execute(
        "INSERT INTO order_changes(order_no, field, old_value, new_value, actor, conv_id, created) "
        "VALUES (?,?,?,?,?,?,?)",
        (order_no, field, old, new, actor, conv_id, time.strftime("%Y-%m-%d %H:%M:%S")))


def can_change(status, what):
    allowed = PHONE_ALLOWED if what == "phone" else BEFORE_SHIPPING
    if status in allowed:
        return True, ""
    return False, REASONS.get(status, "of its current status")


def validate_address(text):
    value = re.sub(r"\s+", " ", text or "").strip()
    if not 10 <= len(value) <= 200:
        return None, "The address should be between 10 and 200 characters."
    if not re.search(r"\d", value) or not re.search(r"[^\W\d_]", value):
        return None, "Please include the street, number and city."
    if not re.fullmatch(r"[\w\s,.\-#/'()]+", value):
        return None, "The address contains characters I can't accept."
    return value, ""


def validate_phone(text):
    value = re.sub(r"[\s\-().]", "", text or "")
    if not re.fullmatch(r"\+?\d{8,15}", value):
        return None, "Please send a phone number with 8 to 15 digits, optionally starting with +."
    return value, ""


def _mask(phone):
    return phone[:-2] + "**" if phone else ""


def _public(row):
    data = dict(row)
    data.pop("customer_email", None)
    data.pop("olist_order_id", None)
    data["phone_masked"] = _mask(data.pop("phone") or "")
    return data


def lookup_order(order_no, email):
    with _db() as c:
        row = _find(c, order_no, email)
        if row is None:
            return _fail("no_match", "I couldn't find an order with that number and email.")
        return {"ok": True, "order": _public(row)}


def status_message(order, today=None):
    today = today or date.today()
    number, status = order["order_no"], order["status"]
    estimate = order.get("estimated_delivery")
    days_late = 0
    if estimate and status in IN_PROGRESS:
        days_late = max((today - date.fromisoformat(estimate)).days, 0)

    if days_late:
        eta = f" It was expected by {estimate}, so it is {days_late} day(s) late."
    elif estimate:
        eta = f" Estimated delivery: {estimate}."
    else:
        eta = ""

    if status == "delivered":
        text = f"Order #{number} was delivered on {order['delivered_at']}."
    elif status == "shipped":
        text = (f"Order #{number} was shipped on {order['shipped_at']} with {order['carrier']} "
                f"(tracking number {order['tracking_number']})." + eta)
    elif status in BEFORE_SHIPPING:
        text = f"Order #{number} is {STATUS_LABEL[status]} and has not shipped yet." + eta
    elif status == "canceled":
        text = f"Order #{number} was canceled." + REFUND_TEXT.get(order.get("refund_status"), "")
    else:
        text = (f"Unfortunately an item in order #{number} was unavailable, so it could not be "
                "fulfilled." + REFUND_TEXT.get(order.get("refund_status"), ""))
    return {"text": text, "days_late": days_late}


def _change(order_no, email, what, new_value, conv_id, actor):
    column = {"address": "shipping_address", "phone": "phone"}[what]
    validate = validate_address if what == "address" else validate_phone
    statuses = PHONE_ALLOWED if what == "phone" else BEFORE_SHIPPING
    with _db() as c:
        row = _find(c, order_no, email)
        if row is None:
            return _fail("no_match", "I couldn't verify that order number and email.")
        allowed, reason = can_change(row["status"], what)
        if not allowed:
            return _fail("not_allowed", f"This can't be changed because {reason}.")
        value, error = validate(new_value)
        if value is None:
            return _fail("invalid", error)
        marks = ",".join("?" * len(statuses))
        cur = c.execute(
            f"UPDATE orders SET {column}=? WHERE order_no=? AND status IN ({marks})",
            (value, row["order_no"], *statuses))
        if cur.rowcount != 1:
            return _fail("not_allowed", "The order changed while I was working on it.")
        _audit(c, row["order_no"], column, row[column], value, conv_id, actor)
        return {"ok": True, "old": row[column], "new": value}


def change_address(order_no, email, new_address, conv_id=None, actor="ai_agent"):
    return _change(order_no, email, "address", new_address, conv_id, actor)


def change_phone(order_no, email, new_phone, conv_id=None, actor="ai_agent"):
    return _change(order_no, email, "phone", new_phone, conv_id, actor)


def cancel_order(order_no, email, conv_id=None, actor="ai_agent"):
    marks = ",".join("?" * len(BEFORE_SHIPPING))
    with _db() as c:
        row = _find(c, order_no, email)
        if row is None:
            return _fail("no_match", "I couldn't verify that order number and email.")
        allowed, reason = can_change(row["status"], "cancel")
        if not allowed:
            return _fail("not_allowed", f"This order can't be canceled because {reason}.")
        refund = "refund_pending" if row["payment_status"] == "paid" else "not_charged"
        cur = c.execute(
            f"UPDATE orders SET status='canceled', refund_status=? WHERE order_no=? AND status IN ({marks})",
            (refund, row["order_no"], *BEFORE_SHIPPING))
        if cur.rowcount != 1:
            return _fail("not_allowed", "The order changed while I was working on it.")
        _audit(c, row["order_no"], "status", row["status"], "canceled", conv_id, actor)
        return {"ok": True, "refund_status": refund}


def recent_changes(order_no, limit=10):
    with _db() as c:
        rows = c.execute(
            "SELECT field, old_value, new_value, actor, conv_id, created FROM order_changes "
            "WHERE order_no=? ORDER BY id DESC LIMIT ?", (order_no, limit)).fetchall()
    return [dict(r) for r in rows]


def admin_view(order_no):
    with _db() as c:
        row = c.execute("SELECT * FROM orders WHERE order_no=?", (order_no,)).fetchone()
    if row is None:
        return None
    data = dict(row)
    data.pop("olist_order_id", None)
    data["changes"] = recent_changes(order_no)
    return data


def demo_samples():
    today = date.today().isoformat()
    with _db() as c:
        rows = c.execute(
            "SELECT MIN(order_no) AS order_no, customer_email AS email, status, "
            "CASE WHEN status IN ('created','approved','invoiced','processing','shipped') "
            "AND estimated_delivery < ? THEN 'late' "
            "WHEN status IN ('created','approved','invoiced','processing','shipped') THEN 'on time' "
            "ELSE '' END AS timing "
            "FROM orders GROUP BY status, timing ORDER BY status, timing", (today,)).fetchall()
    return [dict(r) for r in rows]