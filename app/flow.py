import re

import orders

MAX_FAILS = 3
MAX_MISSES = 2
LATE_ESCALATION_DAYS = 3

CANCEL = re.compile(r"\bcancel\w*\b", re.I)
CANCEL_SKIP = re.compile(r"\b(subscription|newsletter|membership|account|fees?|policy|charges?)\b", re.I)
ORDER_WORD = re.compile(r"\b(order|purchase)\b", re.I)
ADDRESS = re.compile(r"\baddress\b", re.I)
PHONE = re.compile(r"\b(phone|mobile|telephone|contact number)\b", re.I)
CHANGE = re.compile(r"\b(change|update|edit|correct|fix|modify|wrong|incorrect|different|new)\b", re.I)
STATUS_QUESTION = re.compile(
    r"\b(where|when|status|track\w*|late|delay\w*|arriv\w*|hasn'?t|haven'?t|"
    r"not (?:arrived|here|received)|still)\b", re.I)
STATUS_SUBJECT = re.compile(
    r"\b(my|the|this)\s+(order|package|parcel|shipment|delivery|item|purchase|product|article|refund)\b", re.I)

ORDER_NO = re.compile(r"(?<![\w.@+-])#?(\d{4,8})(?![\w@-])")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
YES = re.compile(r"^\s*(yes|y|yeah|yep|yup|sure|ok|okay|confirm|go ahead|do it)\b", re.I)
NO = re.compile(r"^\s*(no|n|nope|nah|don'?t|do not|never ?mind)\b", re.I)
EXIT = re.compile(r"\b(never ?mind|forget it|stop|leave it|skip it)\b", re.I)

CATEGORY = {"status": "DELIVERY", "address": "SHIPPING", "phone": "ACCOUNT", "cancel": "CANCEL"}


def blank():
    return {"action": None, "awaiting": None, "order_no": None, "email": None,
            "new_value": None, "fails": 0, "misses": 0}


def ensure(state):
    for key, value in blank().items():
        state.setdefault(key, value)


def clear(state):
    ensure(state)
    state.update(action=None, awaiting=None, new_value=None, misses=0)


def detect_action(text):
    if CANCEL.search(text) and ORDER_WORD.search(text) and not CANCEL_SKIP.search(text):
        return "cancel"
    if ADDRESS.search(text) and CHANGE.search(text):
        return "address"
    if PHONE.search(text) and CHANGE.search(text):
        return "phone"
    if STATUS_QUESTION.search(text) and STATUS_SUBJECT.search(text):
        return "status"
    return None


def _reply(text, trace, action, state):
    return {"decision": "answer", "reason": "handled with order tools", "reply": text,
            "category": CATEGORY.get(action, "ORDER"), "urgency": "low",
            "summary": f"order {action or 'request'}", "order_ref": _ref(state),
            "trace": trace, "tool": action}


def _handoff(text, reason, trace, action, urgency, summary, state):
    return {"decision": "escalate", "reason": reason, "reply": text,
            "category": CATEGORY.get(action, "ORDER"), "urgency": urgency,
            "summary": summary, "order_ref": _ref(state), "trace": trace, "tool": action}


def _ref(state):
    return str(state["order_no"]) if state.get("order_no") else None


def _take_identifiers(state, message):
    found = ORDER_NO.search(message)
    if found:
        number = int(found.group(1))
        if state["order_no"] is not None and number != state["order_no"]:
            state["email"] = None
        state["order_no"] = number
    email = EMAIL.search(message)
    if email:
        state["email"] = email.group(0).lower()


def handle(message, state, conv_id=None):
    ensure(state)
    if state["awaiting"]:
        return _continue(message, state, conv_id)
    action = detect_action(message)
    if action is None:
        return None
    return start(action, message, state, conv_id)


def start(action, message, state, conv_id=None):
    ensure(state)
    state.update(action=action, new_value=None, misses=0)
    _take_identifiers(state, message)
    return _advance(state, conv_id, [f"order flow: {action}"])


def _advance(state, conv_id, trace):
    state["misses"] = 0
    action = state["action"]
    if state["order_no"] is None:
        state["awaiting"] = "order_no"
        return _reply("Sure, I can help with that. What is your order number?", trace, action, state)
    if state["email"] is None:
        state["awaiting"] = "email"
        return _reply("Thanks. Please send the email address used for the order so I can verify it.",
                      trace, action, state)
    found = orders.lookup_order(state["order_no"], state["email"])
    if not found["ok"]:
        return _failed_lookup(state, trace)
    state["fails"] = 0
    order = found["order"]
    trace.append(f"lookup_order({order['order_no']}) -> {order['status']}")
    return _run_action(order, state, trace)


def _failed_lookup(state, trace):
    state["fails"] += 1
    trace.append(f"lookup_order({state['order_no']}) -> no match ({state['fails']}/{MAX_FAILS})")
    action = state["action"]
    if state["fails"] >= MAX_FAILS:
        reply = _handoff(
            "I still can't match those details to an order, so I've opened case #{tid} and passed "
            "this to a human agent. They will reply here.",
            "order verification failed 3 times", trace, action, "medium",
            "Customer could not verify order ownership", state)
        state["fails"] = 0
        state.update(order_no=None, email=None)
        clear(state)
        return reply
    state.update(order_no=None, email=None, awaiting="order_no")
    return _reply("I couldn't match that order number and email. Please send the order number again.",
                  trace, action, state)


def _run_action(order, state, trace):
    number = order["order_no"]
    action = state["action"]

    if action == "status":
        info = orders.status_message(order)
        clear(state)
        late = info["days_late"]
        if late >= LATE_ESCALATION_DAYS:
            text = (info["text"] + " I'm sorry about the delay. I've opened case #{tid} and passed it "
                    "to a human agent with your order details. They will reply here.")
            return _handoff(text, f"order #{number} is {late} days late", trace, action,
                            "high" if late >= 10 else "medium",
                            f"Order #{number} is {late} days late", state)
        text = info["text"]
        if late:
            text += " It should arrive very soon. If it doesn't, tell me and I'll pass your case to a human agent."
        return _reply(text + " Is there anything else I can help with?", trace, action, state)

    allowed, reason = orders.can_change(order["status"], action)
    if not allowed:
        clear(state)
        label = "cancel" if action == "cancel" else f"change the {action} of"
        return _reply(f"I can't {label} order #{number} because {reason}. "
                      "If you need more help, just ask for a human agent.", trace, action, state)

    if action == "cancel":
        state["awaiting"] = "confirm"
        return _reply(f"Order #{number} has not shipped yet, so I can cancel it (total {order['total']:.2f}). "
                      "Do you want me to cancel it? Reply yes or no.", trace, action, state)

    state["awaiting"] = "new_address" if action == "address" else "new_phone"
    if action == "address":
        text = (f"The delivery address on order #{number} is currently {order['shipping_address']}. "
                "What is the new address?")
    else:
        text = (f"The contact phone on order #{number} currently ends with {order['phone_masked'][-4:]}. "
                "What is the new phone number?")
    return _reply(text, trace, action, state)


def _continue(message, state, conv_id):
    trace = [f"order flow: continue {state['action']} (waiting for {state['awaiting']})"]
    awaiting = state["awaiting"]
    if awaiting == "confirm":
        return _confirm(message, state, conv_id, trace)
    if awaiting in ("new_address", "new_phone"):
        return _take_value(message, state, trace)
    return _take_ids(message, state, conv_id, trace)


def _stop(state, trace):
    action = state["action"]
    clear(state)
    return _reply("No problem, I've stopped that request. Let me know if you need anything else.",
                  trace, action, state)


def _miss(state, trace, text):
    state["misses"] += 1
    if state["misses"] >= MAX_MISSES:
        clear(state)
        return None
    return _reply(text, trace, state["action"], state)


def _take_ids(message, state, conv_id, trace):
    if EXIT.search(message):
        return _stop(state, trace)
    _take_identifiers(state, message)
    if state["awaiting"] == "order_no" and state["order_no"] is None:
        return _miss(state, trace, "I need your order number to continue. You can find it in your "
                                   "confirmation email. Say 'never mind' to stop.")
    if state["awaiting"] == "email" and state["email"] is None:
        return _miss(state, trace, "I need the email address used for the order. Say 'never mind' to stop.")
    return _advance(state, conv_id, trace)


def _take_value(message, state, trace):
    if len(message.split()) <= 4 and EXIT.search(message):
        return _stop(state, trace)
    action = state["action"]
    validate = orders.validate_address if action == "address" else orders.validate_phone
    value, error = validate(message)
    if value is None:
        return _miss(state, trace, error)
    state.update(new_value=value, awaiting="confirm", misses=0)
    number = state["order_no"]
    if action == "address":
        text = f"I'll change the delivery address of order #{number} to: {value}. "
    else:
        text = f"I'll change the contact phone of order #{number} to {value}. "
    return _reply(text + "Reply yes to confirm or no to keep the current one.", trace, action, state)


def _confirm(message, state, conv_id, trace):
    action = state["action"]
    if NO.match(message):
        clear(state)
        return _reply("Okay, nothing was changed.", trace, action, state)
    if not YES.match(message):
        return _miss(state, trace, "Please reply yes to confirm or no to cancel.")

    number, email = state["order_no"], state["email"]
    if action == "address":
        result = orders.change_address(number, email, state["new_value"], conv_id=conv_id)
    elif action == "phone":
        result = orders.change_phone(number, email, state["new_value"], conv_id=conv_id)
    else:
        result = orders.cancel_order(number, email, conv_id=conv_id)
    trace.append(f"{action} order #{number} -> {'ok' if result['ok'] else result['error']}")
    clear(state)

    if not result["ok"]:
        return _reply(result["message"], trace, action, state)
    if action == "address":
        text = f"Done. The delivery address for order #{number} is now {result['new']}."
    elif action == "phone":
        text = f"Done. The contact phone for order #{number} is now {result['new']}."
    else:
        refund = orders.REFUND_TEXT.get(result["refund_status"], "")
        text = f"Order #{number} has been canceled." + refund
    return _reply(text + " Is there anything else I can help with?", trace, action, state)