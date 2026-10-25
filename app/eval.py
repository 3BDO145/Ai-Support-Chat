import sys
import time

import pandas as pd
from datasets import load_dataset

from agent import resolve
from config import KB_PATH
from rag import retrieve

N_RETRIEVAL = 150
N_AGENT = int(sys.argv[1]) if len(sys.argv) > 1 else 15

OUT_OF_SCOPE = [
    "I paid for my subscription but my account is not activated",
    "What's the weather like in Cairo today?",
    "Can you recommend a good restaurant near me?",
    "My laptop screen is cracked and I want it repaired",
    "Write me a poem about the sea",
]

kb = pd.read_csv(KB_PATH)
full = load_dataset("bitext/Bitext-customer-support-llm-chatbot-training-dataset", split="train").to_pandas()
test = full[~full["instruction"].str.strip().isin(kb["instruction"])]
test = test.sample(N_RETRIEVAL, random_state=1).reset_index(drop=True)
print(f"Held-out pool: {len(test)} questions, none of them in the index\n")

top1 = top4 = cat1 = 0
right_scores, wrong_scores = [], []
for row in test.itertuples():
    hits = retrieve(row.instruction, k=4)
    intents = [h["intent"] for h in hits]
    if intents[0] == row.intent:
        top1 += 1
        right_scores.append(hits[0]["score"])
    else:
        wrong_scores.append(hits[0]["score"])
    top4 += row.intent in intents
    cat1 += hits[0]["category"] == row.category
n = len(test)
print("== Retrieval ==")
print(f"intent hit@1: {top1 / n:.0%} | hit@4: {top4 / n:.0%} | category@1: {cat1 / n:.0%}")
if right_scores and wrong_scores:
    print(f"avg top1 score when correct: {sum(right_scores) / len(right_scores):.3f} | "
          f"when wrong: {sum(wrong_scores) / len(wrong_scores):.3f}")

print(f"\n== Agent on {N_AGENT} in-scope questions (knowledge base path only) ==")
answered = classified = grounded = 0
times = []
for row in test.head(N_AGENT).itertuples():
    started = time.time()
    out = resolve(row.instruction, use_orders=False)
    times.append(time.time() - started)
    classified += out["classification"]["category"] == row.category
    if out["decision"] == "answer":
        answered += 1
        grounded += any(e["intent"] == row.intent for e in out["evidence"])
    print(f"- {out['decision']:8} true={row.intent:24} | {row.instruction[:55]}")
print(f"classification accuracy: {classified}/{N_AGENT}")
print(f"answered: {answered}/{N_AGENT} | evidence had the right intent: {grounded}/{answered or 1}")
print(f"avg time per request: {sum(times) / len(times):.1f}s")

print("\n== Out of scope (expected: escalate) ==")
escalated = 0
for text in OUT_OF_SCOPE:
    out = resolve(text, use_orders=False)
    escalated += out["decision"] == "escalate"
    print(f"- {out['decision']:8} | {text}")
print(f"correctly escalated: {escalated}/{len(OUT_OF_SCOPE)}")