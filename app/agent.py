import re
import sys

import flow
from llm import chat, classify, generate_reply, parse_json
from rag import retrieve

MIN_SCORE = 0.60
AUTO_ACCEPT = 0.80
MAX_CONTEXT = 600

LIVE_INTENTS = {"payment_issue"}
LIVE_SUFFIX = (" I can't see live payment details myself, so if this doesn't solve it, "
               "just tell me and I'll pass your case to a human agent.")
INTENT_TO_ACTION = {
    "track_order": "status",
    "track_refund": "status",
    "delivery_period": "status",
    "change_shipping_address": "address",
    "cancel_order": "cancel",
}

CRITICAL = re.compile(
    r"\b(scam|fraud|stolen|unauthori[sz]ed|lawyer|sue|legal|chargeback|police|hacked)\b", re.I)
HUMAN_REQUEST = re.compile(
    r"\b(human|real person|real agent|live agent|representative|supervisor|manager|"
    r"(speak|talk|chat) (to|with) (a |an )?(person|someone|agent))\b", re.I)
NEED_MORE = re.compile(
    r"\b(still|didn'?t|doesn'?t|did not|does not|not help\w*|no help|useless|"
    r"nothing|not working|can'?t find|cannot find)\b", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
LONG_NUMBER = re.compile(r"#?\d{4,}")

JUDGE_PROMPT = """You compare a customer message with a reference question.
Answer true if the reference answer would help this customer: same topic and same goal, even if the wording differs.
Answer false only if the customer's problem is clearly different.

Examples:
Customer: where is my parcel -> Reference: when will my package arrive -> {{"same_problem": true}}
Customer: I want a refund -> Reference: help me get my money back -> {{"same_problem": true}}
Customer: my paid account is not active -> Reference: how do I subscribe to the newsletter -> {{"same_problem": false}}
Customer: I was charged twice -> Reference: how do I change my shipping address -> {{"same_problem": false}}

Customer: {message}
Reference: {question}
Return ONLY JSON like {{"same_problem": true}}:"""


def scrub(text):
    return re.sub(r"[ \t]{2,}", " ", LONG_NUMBER.sub("", EMAIL.sub("", text))).strip()


def judge(message, hit):
    raw = chat(JUDGE_PROMPT.format(message=message, question=hit["question"]),
               json_mode=True, temperature=0.0)
    return bool(parse_json(raw).get("same_problem"))


def find_evidence(message, hits, trace, label):
    good = []
    for hit in hits[:3]:
        if hit["score"] < MIN_SCORE:
            trace.append(f"{label}: skip {hit['intent']} (score {hit['score']} < {MIN_SCORE})")
            continue
        if hit["score"] >= AUTO_ACCEPT:
            trace.append(f"{label}: accept {hit['intent']} score={hit['score']} (high similarity)")
            good.append(hit)
            continue
        relevant = judge(message, hit)
        trace.append(f"{label}: judge {hit['intent']} score={hit['score']} -> "
                     f"{'relevant' if relevant else 'not relevant'}")
        if relevant:
            good.append(hit)
    return good


def needs_more(message):
    return bool(NEED_MORE.search(message) or re.match(r"\s*no\b", message, re.I))


def resolve(conversation, state=None, conv_id=None, use_orders=True):
    if isinstance(conversation, str):
        conversation = [{"role": "user", "content": conversation}]
    state = {} if state is None else state

    user_turns = [m["content"] for m in conversation if m["role"] == "user"]
    message = user_turns[-1]
    context = scrub(" ".join(user_turns))[-MAX_CONTEXT:] or message
    history = scrub("\n".join(
        f"{'Customer' if m['role'] == 'user' else 'Agent'}: {m['content']}"
        for m in conversation[:-1]))[-1200:]
    previous = conversation[-2] if len(conversation) > 1 else None
    after_live = bool(previous and previous["role"] == "assistant" and previous.get("live"))

    trace = [f"conversation: {len(conversation)} message(s)"]
    cache = {}

    def order_ref():
        return str(state["order_no"]) if state.get("order_no") else None

    def info():
        if "info" not in cache:
            cache["info"] = classify(context)
            found = cache["info"]
            trace.append(f"classify -> {found['category']} / urgency={found['urgency']} / {found['sentiment']}")
        return cache["info"]

    def finish(decision, reason, classification, evidence, reply, live=False, tool=None):
        return {"decision": decision, "reason": reason, "classification": classification,
                "evidence": evidence, "reply": reply, "trace": trace, "order_ref": order_ref(),
                "live_data": live, "tool": tool}

    def escalate(reason, hits=None):
        flow.clear(state)
        trace.append(f"DECISION: escalate ({reason})")
        return finish("escalate", reason, info(), hits or [], None)

    def from_flow(result):
        trace.extend(result["trace"])
        trace.append(f"DECISION: {result['decision']} ({result['reason']})")
        classification = {"category": result["category"], "summary": result["summary"],
                          "urgency": result["urgency"], "sentiment": "neutral"}
        out = finish(result["decision"], result["reason"], classification, [], result["reply"],
                     tool=result["tool"])
        out["order_ref"] = result["order_ref"] or order_ref()
        return out

    typing_value = state.get("awaiting") in ("new_address", "new_phone")
    if not typing_value:
        critical = CRITICAL.search(message)
        if critical:
            return escalate(f"critical keyword: '{critical.group(0)}'")
        if HUMAN_REQUEST.search(message):
            return escalate("customer asked for a human agent")

    if use_orders:
        result = flow.handle(message, state, conv_id)
        if result:
            return from_flow(result)

    if after_live and needs_more(message):
        return escalate("customer still needs help after general guidance")

    hits = retrieve(context, k=4)
    if not hits:
        return escalate("knowledge base returned no results")
    top = hits[0]
    trace.append(f"retrieve -> top={top['intent']} ({top['score']})")

    if use_orders and top["score"] >= AUTO_ACCEPT and top["intent"] in INTENT_TO_ACTION:
        action = INTENT_TO_ACTION[top["intent"]]
        trace.append(f"intent {top['intent']} -> order flow ({action})")
        return from_flow(flow.start(action, message, state, conv_id))

    classification = info()
    if classification["category"] == "UNKNOWN":
        return escalate("could not classify the complaint")

    evidence = find_evidence(context, hits, trace, "attempt 1")
    if not evidence:
        retry_hits = retrieve(classification["summary"], k=4, category=classification["category"])
        trace.append(f"retry: query='{classification['summary']}' category={classification['category']}")
        evidence = find_evidence(context, retry_hits, trace, "attempt 2")
        hits = retry_hits
    if not evidence:
        return escalate("no relevant evidence in the knowledge base", hits[:2])

    live = evidence[0]["intent"] in LIVE_INTENTS
    reply = generate_reply(message, classification["summary"], evidence, history, live=live)
    if live:
        reply += LIVE_SUFFIX
        trace.append(f"live-data intent ({evidence[0]['intent']}): general guidance only")
    trace.append(f"DECISION: answer using {len(evidence)} evidence item(s)")
    return finish("answer", "relevant evidence found", classification, evidence, reply, live=live)


def terminal_chat():
    conversation, state = [], {}
    while True:
        text = input("you> ").strip()
        if not text:
            break
        conversation.append({"role": "user", "content": text})
        out = resolve(conversation, state, "terminal")
        reply = out["reply"] or "(handed over to a human agent)"
        print("\n".join(f"  . {line}" for line in out["trace"]))
        print(f"bot> {reply}\n")
        conversation.append({"role": "assistant", "content": reply, "live": out["live_data"]})


if __name__ == "__main__":
    if len(sys.argv) > 1:
        out = resolve(" ".join(sys.argv[1:]))
        print("\n".join(out["trace"]))
        print(f"\nDecision: {out['decision']} - {out['reason']}")
        if out["reply"]:
            print(f"\nReply:\n{out['reply']}")
    else:
        terminal_chat()