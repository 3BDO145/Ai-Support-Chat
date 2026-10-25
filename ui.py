import os
import sys
import uuid

import requests
import streamlit as st

sys.path.insert(0, "app")
from llm import clean

API_URL = os.getenv("API_URL", "http://127.0.0.1:8000")
GREETING = "Hi, I'm the SupportOps assistant. Tell me what's going on and I'll do my best to help."

EXAMPLES = {
    "Where is my order": "where is my order",
    "Change delivery address": "I want to change my delivery address",
    "Update phone number": "I need to update my phone number",
    "Cancel my order": "I want to cancel my order",
    "I want my money back": "i want my money back",
    "Refund policy": "what is your refund policy",
    "Legal threat": "this is a scam, I will call my lawyer",
    "Off-topic": "Write me a poem about the sea",
}

st.set_page_config(page_title="SupportOps AI", page_icon="🎧", layout="centered")

if "conv_id" not in st.session_state:
    st.session_state.conv_id = uuid.uuid4().hex


def set_pending(text):
    st.session_state.pending = text


def new_conversation():
    st.session_state.conv_id = uuid.uuid4().hex


def demo_orders():
    try:
        res = requests.get(f"{API_URL}/demo/orders", timeout=5)
        res.raise_for_status()
        return res.json()
    except Exception:
        return []


with st.sidebar:
    st.title("SupportOps AI")
    st.caption("LLM, RAG and an agent with order tools. Knowledge base: Bitext (synthetic data).")
    st.toggle("Show agent internals", value=False, key="show_details")
    st.divider()
    st.write("**Quick examples**")
    for label, text in EXAMPLES.items():
        st.button(label, use_container_width=True, on_click=set_pending, args=(text,))
    rows = demo_orders()
    if rows:
        st.divider()
        with st.expander("Demo orders"):
            st.caption("Order numbers and the email each one is registered with.")
            st.dataframe(rows, hide_index=True)
    st.divider()
    st.button("New conversation", use_container_width=True, on_click=new_conversation)


def fetch():
    try:
        res = requests.get(f"{API_URL}/conversations/{st.session_state.conv_id}", timeout=10)
        res.raise_for_status()
        return res.json()
    except Exception:
        return None


def render_details(meta):
    cls = meta["classification"]
    with st.expander("Agent internals"):
        st.write(f"**Decision:** {meta['decision']} - {meta['reason']}")
        if meta.get("tool"):
            st.write(f"**Order tool flow:** {meta['tool']}")
        st.write(f"**Category:** {cls['category']} | **Sentiment:** {cls['sentiment']} "
                 f"| **Urgency guess:** {cls['urgency']}")
        if "retrieved_category" in cls:
            st.write(f"**Category from evidence:** {cls['retrieved_category']} / {cls['retrieved_intent']}")
        for item in meta.get("evidence", []):
            st.caption(f"{item['category']} / {item['intent']} (score {item['score']})")
            st.write(f"_Reference:_ {clean(item['response']) or '(template placeholders only)'}")
        for step in meta["trace"]:
            st.code(step, language=None)


def render(message):
    role = message["role"]
    avatar = {"user": "🧑", "assistant": "🎧", "admin": "👩‍💼"}[role]
    with st.chat_message("user" if role == "user" else "assistant", avatar=avatar):
        if role == "admin":
            st.caption("Human agent")
        st.write(message["content"])
        meta = message.get("meta")
        if meta and meta.get("decision") == "escalate":
            st.caption("Passed to a human agent")
        if st.session_state.get("show_details") and meta:
            render_details(meta)


@st.fragment(run_every=4)
def history():
    data = fetch()
    if data is None:
        st.warning("Can't reach the server. Is uvicorn running?")
        return
    with st.chat_message("assistant", avatar="🎧"):
        st.write(GREETING)
    for message in data["messages"]:
        render(message)
    ticket = data.get("ticket")
    if ticket and ticket["status"] == "open":
        st.info(f"Case #{ticket['id']} is with a human agent. Your next messages are added to it "
                "and their reply will appear here.")
    elif ticket and ticket["status"] == "resolved":
        st.success(f"Case #{ticket['id']} was marked as resolved by the support team.")


history()

user_text = st.chat_input("Type your message...") or st.session_state.pop("pending", None)
if user_text:
    with st.chat_message("user", avatar="🧑"):
        st.write(user_text)
    with st.chat_message("assistant", avatar="🎧"):
        with st.spinner("Typing..."):
            try:
                res = requests.post(f"{API_URL}/chat",
                                    json={"conv_id": st.session_state.conv_id, "message": user_text},
                                    timeout=180)
                res.raise_for_status()
            except Exception as error:
                st.error(f"Could not reach the API at {API_URL}.\n\n{error}")
                st.stop()
    st.rerun()