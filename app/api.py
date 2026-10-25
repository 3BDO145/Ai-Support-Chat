import hmac
import time

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

import db
import orders
from agent import resolve
from config import ADMIN_PASSWORD, DEMO_MODE

HANDOFF_TEXT = (
    "I can't resolve this on my own, so I've opened case #{tid} and passed it to a human agent "
    "along with our full conversation. They will reply here in this chat."
)

db.init()
orders.init()
app = FastAPI(title="SupportOps AI", version="3.0")


def admin_only(x_admin_password: str = Header(default="")):
    if not ADMIN_PASSWORD:
        raise HTTPException(503, "ADMIN_PASSWORD is not set on the server")
    if not hmac.compare_digest(x_admin_password.encode(), ADMIN_PASSWORD.encode()):
        raise HTTPException(401, "wrong admin password")


class ChatIn(BaseModel):
    conv_id: str = Field(min_length=8, max_length=64)
    message: str = Field(min_length=1, max_length=1000)


class ReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


def is_duplicate(messages, text, window=60):
    if not messages:
        return False
    last = messages[-1]
    if last["role"] != "user" or last["content"] != text:
        return False
    sent = time.mktime(time.strptime(last["created"], "%Y-%m-%d %H:%M:%S"))
    return time.time() - sent < window


@app.get("/")
def root():
    return {"service": "SupportOps AI", "docs": "/docs"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/demo/orders")
def demo_orders():
    return orders.demo_samples() if DEMO_MODE else []


@app.post("/chat")
def chat(body: ChatIn):
    message = body.message.strip()
    if is_duplicate(db.get_messages(body.conv_id), message):
        return {"decision": "duplicate"}
    db.add_message(body.conv_id, "user", message)

    ticket = db.open_ticket(body.conv_id)
    if ticket:
        db.touch_ticket(ticket["id"])
        return {"decision": "handoff", "ticket_id": ticket["id"]}

    history = [
        {"role": m["role"], "content": m["content"],
         "live": bool(m["meta"] and m["meta"].get("live_data"))}
        for m in db.get_messages(body.conv_id)
    ]
    state = db.get_state(body.conv_id)
    out = resolve(history, state, body.conv_id)
    db.set_state(body.conv_id, state)

    if out["decision"] == "answer" and out["evidence"]:
        out["classification"]["retrieved_category"] = out["evidence"][0]["category"]
        out["classification"]["retrieved_intent"] = out["evidence"][0]["intent"]

    if out["decision"] == "escalate":
        info = out["classification"]
        ticket_id = db.create_ticket(body.conv_id, info["category"], info["urgency"],
                                     info["summary"], out["reason"], out["order_ref"])
        reply = (out["reply"] or HANDOFF_TEXT).replace("{tid}", str(ticket_id))
        if not out["reply"] and out["order_ref"]:
            reply += f" I've noted order #{out['order_ref']} on the case."
        out["reply"] = reply
        out["ticket_id"] = ticket_id

    db.add_message(body.conv_id, "assistant", out["reply"], meta=out)
    return out


@app.get("/conversations/{conv_id}")
def conversation(conv_id: str):
    return {"messages": db.get_messages(conv_id), "ticket": db.latest_ticket(conv_id)}


@app.get("/admin/tickets", dependencies=[Depends(admin_only)])
def tickets(status: str = "open"):
    return db.list_tickets(status)


@app.get("/admin/tickets/{ticket_id}", dependencies=[Depends(admin_only)])
def ticket_detail(ticket_id: int):
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(404, "ticket not found")
    return {"ticket": ticket, "messages": db.get_messages(ticket["conv_id"])}


@app.get("/admin/orders/{order_no}", dependencies=[Depends(admin_only)])
def admin_order(order_no: int):
    order = orders.admin_view(order_no)
    if not order:
        raise HTTPException(404, "order not found")
    return order


@app.post("/admin/tickets/{ticket_id}/reply", dependencies=[Depends(admin_only)])
def ticket_reply(ticket_id: int, body: ReplyIn):
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(404, "ticket not found")
    db.add_message(ticket["conv_id"], "admin", body.text.strip())
    db.touch_ticket(ticket_id)
    return {"ok": True}


@app.post("/admin/tickets/{ticket_id}/resolve", dependencies=[Depends(admin_only)])
def ticket_resolve(ticket_id: int):
    if not db.get_ticket(ticket_id):
        raise HTTPException(404, "ticket not found")
    db.set_status(ticket_id, "resolved")
    return {"ok": True}