import json
import os
import sqlite3
import time
from contextlib import contextmanager

from config import TICKETS_DB_PATH as DB_PATH


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


@contextmanager
def _db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init():
    with _db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conv_id TEXT, role TEXT, content TEXT, meta TEXT, created TEXT);
        CREATE INDEX IF NOT EXISTS ix_messages ON messages(conv_id, id);
        CREATE TABLE IF NOT EXISTS tickets(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conv_id TEXT, status TEXT, category TEXT, urgency TEXT,
            summary TEXT, reason TEXT, order_ref TEXT,
            created TEXT, updated TEXT);
        CREATE TABLE IF NOT EXISTS conv_state(
            conv_id TEXT PRIMARY KEY, data TEXT);
        """)


def add_message(conv_id, role, content, meta=None):
    with _db() as c:
        cur = c.execute(
            "INSERT INTO messages(conv_id, role, content, meta, created) VALUES (?,?,?,?,?)",
            (conv_id, role, content, json.dumps(meta) if meta else None, _now()))
        return cur.lastrowid


def get_messages(conv_id):
    with _db() as c:
        rows = c.execute(
            "SELECT id, role, content, meta, created FROM messages WHERE conv_id=? ORDER BY id",
            (conv_id,)).fetchall()
    return [
        {"id": r["id"], "role": r["role"], "content": r["content"],
         "meta": json.loads(r["meta"]) if r["meta"] else None, "created": r["created"]}
        for r in rows
    ]


def get_state(conv_id):
    with _db() as c:
        row = c.execute("SELECT data FROM conv_state WHERE conv_id=?", (conv_id,)).fetchone()
    return json.loads(row["data"]) if row else {}


def set_state(conv_id, state):
    with _db() as c:
        c.execute("INSERT OR REPLACE INTO conv_state(conv_id, data) VALUES (?,?)",
                  (conv_id, json.dumps(state)))


def create_ticket(conv_id, category, urgency, summary, reason, order_ref):
    with _db() as c:
        cur = c.execute(
            "INSERT INTO tickets(conv_id, status, category, urgency, summary, reason, order_ref, created, updated)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (conv_id, "open", category, urgency, summary, reason, order_ref, _now(), _now()))
        return cur.lastrowid


def _one(sql, args):
    with _db() as c:
        row = c.execute(sql, args).fetchone()
    return dict(row) if row else None


def open_ticket(conv_id):
    return _one("SELECT * FROM tickets WHERE conv_id=? AND status='open' ORDER BY id DESC LIMIT 1", (conv_id,))


def latest_ticket(conv_id):
    return _one("SELECT * FROM tickets WHERE conv_id=? ORDER BY id DESC LIMIT 1", (conv_id,))


def get_ticket(ticket_id):
    return _one("SELECT * FROM tickets WHERE id=?", (ticket_id,))


def touch_ticket(ticket_id):
    with _db() as c:
        c.execute("UPDATE tickets SET updated=? WHERE id=?", (_now(), ticket_id))


def set_status(ticket_id, status):
    with _db() as c:
        c.execute("UPDATE tickets SET status=?, updated=? WHERE id=?", (status, _now(), ticket_id))


def list_tickets(status="open"):
    sql = "SELECT * FROM tickets"
    args = ()
    if status in ("open", "resolved"):
        sql += " WHERE status=?"
        args = (status,)
    sql += " ORDER BY CASE urgency WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, id DESC"
    with _db() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]