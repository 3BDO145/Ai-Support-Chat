import json
import re
import sys

import ollama

from config import MODEL_NAME, OLLAMA_HOST

client = ollama.Client(host=OLLAMA_HOST)

CATEGORIES = ["ACCOUNT", "CANCEL", "CONTACT", "DELIVERY", "FEEDBACK",
              "INVOICE", "ORDER", "PAYMENT", "REFUND", "SHIPPING", "SUBSCRIPTION"]

CLASSIFY_PROMPT = """You are a customer-support triage assistant.
Read the customer message and return ONLY a JSON object with these keys:
- "category": one of {categories}
- "summary": one short sentence describing the problem
- "urgency": one of "low", "medium", "high"
- "sentiment": one of "positive", "neutral", "negative", "angry"

Rules:
- Pick the category that best matches the MAIN problem.
- Use "high" urgency only for money lost, fraud, legal threats, or blocked access.

Customer message: {message}
JSON:"""

REPLY_PROMPT = """You are a polite, professional customer-support agent.
Write a SHORT reply (2 to 4 sentences, plain text, no lists, no numbered steps).
Use ONLY ideas from the reference answers below.
Never invent steps, phone numbers, links, prices, dates, or order numbers.
You cannot look up orders, payments or accounts, so do not ask the customer for
order numbers or account details, and never promise to check anything.
Give the general help from the references instead.

Conversation so far:
{history}

Latest customer message: {message}
Problem summary: {summary}

Reference answers:
{evidence}
{extra}
Reply:"""

LIVE_EXTRA = ("The customer asks about a specific payment. "
              "Write one empathetic sentence and share only the general guidance "
              "from the references.\n")
STRICT_EXTRA = ("IMPORTANT: Do NOT give numbered steps or click-by-click instructions. "
                "Write one empathetic sentence and one general sentence.\n")
FALLBACK = ("Thanks for reaching out, I'd be glad to help with this. "
            "Could you tell me a bit more about what's going on?")

STEP_WORDS = re.compile(
    r"\b(click|locate|follow these steps|navigate to|go to the|select the|press the)\b", re.I)
PLACEHOLDER = re.compile(r"\{\{\s*([^}]*?)\s*\}\}")
ALLOWED_PLACEHOLDER = re.compile(r"(number|\bid)$", re.I)
CONTACT_PLACEHOLDER = re.compile(r"phone|url|website|portal|email|address|chat|hours|support", re.I)
ASKS_FOR_ID = re.compile(
    r"\b(provide|share|send|give|confirm|enter|ask for)\b[^.?!]*"
    r"\b(order|tracking|invoice|account|reference)\s+(number|id)\b", re.I)


def chat(prompt, json_mode=False, temperature=0.2):
    kwargs = {"format": "json"} if json_mode else {}
    res = client.chat(
        model=MODEL_NAME,
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": temperature, "num_predict": 300},
        **kwargs,
    )
    return res["message"]["content"].strip()


def parse_json(raw):
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.S)
        if not match:
            return {}
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    return data if isinstance(data, dict) else {}


def classify(message):
    raw = chat(CLASSIFY_PROMPT.format(categories=CATEGORIES, message=message),
               json_mode=True, temperature=0.0)
    data = parse_json(raw)
    category = str(data.get("category", "")).upper()
    if category not in CATEGORIES:
        category = "UNKNOWN"
    urgency = str(data.get("urgency", "medium")).lower()
    if urgency not in ("low", "medium", "high"):
        urgency = "medium"
    sentiment = str(data.get("sentiment", "neutral")).lower()
    if sentiment not in ("positive", "neutral", "negative", "angry"):
        sentiment = "neutral"
    return {
        "category": category,
        "summary": str(data.get("summary", message))[:200],
        "urgency": urgency,
        "sentiment": sentiment,
    }


def clean(text):
    kept = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        names = PLACEHOLDER.findall(sentence)
        if any(CONTACT_PLACEHOLDER.search(n) or not ALLOWED_PLACEHOLDER.search(n) for n in names):
            continue
        kept.append(PLACEHOLDER.sub(lambda m: m.group(1).lower(), sentence))
    out = " ".join(s.strip() for s in kept if s.strip())
    out = re.sub(r"(^|\s)\d+\.\s*(?=\s|$)", " ", out)
    return re.sub(r"\s{2,}", " ", out).strip()


def drop_asks(text):
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(s for s in sentences if s and not ASKS_FOR_ID.search(s))


def generate_reply(message, summary, hits, history="", live=False):
    evidence = "\n\n".join(
        f"[{i + 1}] {drop_asks(clean(h['response']))}" for i, h in enumerate(hits))
    for strict in (False, True):
        extra = (LIVE_EXTRA if live else "") + (STRICT_EXTRA if strict else "")
        prompt = REPLY_PROMPT.format(
            history=history or "(first message)", message=message, summary=summary,
            evidence=evidence, extra=extra)
        reply = drop_asks(clean(chat(prompt, temperature=0.1 if strict else 0.3)))
        reply = " ".join(re.split(r"(?<=[.!?])\s+", reply)[:4])
        if reply and not STEP_WORDS.search(reply):
            return reply
    return FALLBACK


if __name__ == "__main__":
    text = " ".join(sys.argv[1:]) or "where is my package"
    print(json.dumps(classify(text), indent=2, ensure_ascii=False))