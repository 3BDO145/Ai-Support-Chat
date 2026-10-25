import os

import requests
import streamlit as st

API_URL = os.getenv("API_URL", "http://127.0.0.1:8000")
AVATARS = {"user": "🧑", "assistant": "🎧", "admin": "👩‍💼"}

st.set_page_config(page_title="Support inbox", page_icon="🛠️", layout="wide")
st.title("Support team inbox")

password = st.sidebar.text_input("Admin password", type="password")
if not password:
    st.info("Enter the admin password in the sidebar.")
    st.stop()


def api(method, path, **kwargs):
    res = requests.request(method, API_URL + path, headers={"X-Admin-Password": password},
                           timeout=20, **kwargs)
    if res.status_code == 401:
        st.error("Wrong password.")
        st.stop()
    res.raise_for_status()
    return res.json()


status = st.sidebar.radio("Show", ["open", "resolved", "all"])
st.sidebar.button("Refresh")

tickets = api("GET", "/admin/tickets", params={"status": status})
if not tickets:
    st.write("No tickets here.")
    st.stop()

labels = {t["id"]: f"#{t['id']} | {t['urgency'].upper()} | {t['category']} | {t['summary'][:45]}"
          for t in tickets}
selected = st.selectbox("Ticket", list(labels), format_func=labels.get)
data = api("GET", f"/admin/tickets/{selected}")
ticket = data["ticket"]

col1, col2, col3, col4 = st.columns(4)
col1.metric("Status", ticket["status"])
col2.metric("Category", ticket["category"])
col3.metric("Urgency", ticket["urgency"])
col4.metric("Order", ticket["order_ref"] or "-")
st.write(f"**Summary:** {ticket['summary']}")
st.write(f"**Why it was escalated:** {ticket['reason']}")
st.caption(f"Opened {ticket['created']} | last update {ticket['updated']}")

if ticket["order_ref"] and ticket["order_ref"].isdigit():
    try:
        order = api("GET", f"/admin/orders/{ticket['order_ref']}")
    except requests.HTTPError:
        order = None
    if order:
        with st.expander(f"Order #{order['order_no']} details", expanded=True):
            left, right = st.columns(2)
            left.write(f"**Status:** {order['status']}")
            left.write(f"**Customer email:** {order['customer_email']}")
            left.write(f"**Address:** {order['shipping_address']}")
            left.write(f"**Phone:** {order['phone']}")
            right.write(f"**Purchased:** {order['purchased_at']}")
            right.write(f"**Estimated delivery:** {order['estimated_delivery'] or '-'}")
            right.write(f"**Carrier:** {order['carrier'] or '-'} {order['tracking_number'] or ''}")
            right.write(f"**Payment / refund:** {order['payment_status']} / {order['refund_status'] or '-'}")
            if order["changes"]:
                st.write("**Changes made by the assistant**")
                st.dataframe(order["changes"], hide_index=True)

st.subheader("Full conversation")
for message in data["messages"]:
    with st.chat_message("user" if message["role"] == "user" else "assistant",
                         avatar=AVATARS[message["role"]]):
        if message["role"] == "admin":
            st.caption("Human agent")
        st.write(message["content"])
        st.caption(message["created"])

if ticket["status"] == "open":
    with st.form("reply", clear_on_submit=True):
        text = st.text_area("Your reply to the customer")
        if st.form_submit_button("Send reply", type="primary") and text.strip():
            api("POST", f"/admin/tickets/{selected}/reply", json={"text": text})
            st.rerun()
    if st.button("Mark as resolved"):
        api("POST", f"/admin/tickets/{selected}/resolve")
        st.rerun()