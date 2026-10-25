import sys
import types
import unittest

from support import OrdersCase

calls = {"classify": 0, "chat": 0}
settings = {"hits": [], "info": None, "judge": "false"}


def default_info():
    return {"category": "REFUND", "summary": "customer wants a refund",
            "urgency": "low", "sentiment": "neutral"}


def fake_classify(text):
    calls["classify"] += 1
    return dict(settings["info"] or default_info())


def fake_chat(prompt, json_mode=False, temperature=0.2):
    calls["chat"] += 1
    return '{"same_problem": %s}' % settings["judge"]


def fake_generate_reply(message, summary, hits, history="", live=False):
    return "generated reply"


def fake_retrieve(query, k=4, category=None):
    return list(settings["hits"])


def hit(intent, score, category="REFUND"):
    return {"id": "1", "question": "q", "category": category, "intent": intent,
            "response": "r", "score": score}


def setUpModule():
    fake_llm = types.ModuleType("llm")
    fake_llm.chat = fake_chat
    fake_llm.classify = fake_classify
    fake_llm.generate_reply = fake_generate_reply
    fake_llm.parse_json = __import__("json").loads
    fake_rag = types.ModuleType("rag")
    fake_rag.retrieve = fake_retrieve
    sys.modules["llm"] = fake_llm
    sys.modules["rag"] = fake_rag
    sys.modules.pop("agent", None)


def tearDownModule():
    for name in ("llm", "rag", "agent"):
        sys.modules.pop(name, None)


class AgentCase(OrdersCase):
    def setUp(self):
        super().setUp()
        import agent
        self.agent = agent
        self.state = {}
        self.history = []
        calls.update(classify=0, chat=0)
        settings.update(hits=[hit("get_refund", 0.9)], info=None, judge="false")

    def say(self, text, **kwargs):
        self.history.append({"role": "user", "content": text})
        out = self.agent.resolve(self.history, self.state, "conv-test", **kwargs)
        reply = out["reply"] or "(handoff)"
        self.history.append({"role": "assistant", "content": reply, "live": out["live_data"]})
        return out


class EscalationRuleTests(AgentCase):
    def test_critical_keyword_escalates(self):
        out = self.say("this is a scam, I will call my lawyer")
        self.assertEqual(out["decision"], "escalate")
        self.assertIn("critical keyword", out["reason"])
        self.assertIsNone(out["reply"])

    def test_human_request_escalates(self):
        out = self.say("I want to talk to a human")
        self.assertEqual(out["decision"], "escalate")
        self.assertIn("human", out["reason"])

    def test_escalation_clears_the_running_flow(self):
        self.say("I want to cancel my order")
        self.assertEqual(self.state["awaiting"], "order_no")
        self.say("let me speak to a manager")
        self.assertIsNone(self.state["awaiting"])

    def test_escalation_keeps_the_order_reference(self):
        number, _ = self.pick("status='processing'")
        self.say("I want to cancel my order")
        self.say(str(number))
        out = self.say("this is fraud")
        self.assertEqual(out["order_ref"], str(number))

    def test_address_text_is_not_mistaken_for_a_threat(self):
        number, email = self.pick("status='processing'")
        self.say("I want to change my delivery address")
        self.say(str(number))
        self.say(email)
        out = self.say("42 Legal Avenue, Springfield")
        self.assertEqual(out["decision"], "answer")
        self.assertIn("Reply yes", out["reply"])

    def test_follow_up_after_live_guidance_escalates(self):
        settings["hits"] = [hit("payment_issue", 0.9, "PAYMENT")]
        first = self.say("my card was declined at checkout")
        self.assertTrue(first["live_data"])
        self.assertIn("can't see live payment details", first["reply"])
        second = self.say("that still doesn't help")
        self.assertEqual(second["decision"], "escalate")


class KnowledgeBasePathTests(AgentCase):
    def test_strong_match_is_answered_without_judging(self):
        out = self.say("i want my money back")
        self.assertEqual(out["decision"], "answer")
        self.assertEqual(out["reply"], "generated reply")
        self.assertEqual(out["evidence"][0]["intent"], "get_refund")
        self.assertEqual(calls["chat"], 0)

    def test_weak_matches_escalate(self):
        settings["hits"] = [hit("newsletter_subscription", 0.45, "SUBSCRIPTION")]
        out = self.say("something odd happened")
        self.assertEqual(out["decision"], "escalate")
        self.assertIn("no relevant evidence", out["reason"])

    def test_gray_zone_is_decided_by_the_judge(self):
        settings["hits"] = [hit("get_refund", 0.7)]
        settings["judge"] = "true"
        self.assertEqual(self.say("refund please")["decision"], "answer")
        self.history.clear()
        settings["judge"] = "false"
        self.assertEqual(self.say("refund please")["decision"], "escalate")

    def test_unknown_category_escalates(self):
        settings["info"] = {"category": "UNKNOWN", "summary": "?", "urgency": "low", "sentiment": "neutral"}
        self.assertIn("could not classify", self.say("blah")["reason"])

    def test_empty_index_escalates(self):
        settings["hits"] = []
        self.assertEqual(self.say("anything")["decision"], "escalate")

    def test_emails_and_numbers_are_removed_from_the_search_text(self):
        text = self.agent.scrub("order 10019 email customer1@example.com thanks")
        self.assertNotIn("10019", text)
        self.assertNotIn("@", text)


class OrderRoutingTests(AgentCase):
    def test_knowledge_base_intent_can_start_an_order_flow(self):
        settings["hits"] = [hit("delivery_period", 0.88, "DELIVERY")]
        out = self.say("I have to see when will my article arrive")
        self.assertEqual(out["tool"], "status")
        self.assertIn("order number", out["reply"])
        self.assertEqual(calls["classify"], 0)

    def test_routing_needs_a_strong_match(self):
        settings["hits"] = [hit("delivery_period", 0.7, "DELIVERY")]
        out = self.say("something about shipping times")
        self.assertIsNone(out["tool"])

    def test_orders_can_be_switched_off(self):
        out = self.say("where is my package", use_orders=False)
        self.assertIsNone(out["tool"])
        self.assertEqual(out["decision"], "answer")

    def test_full_cancellation_conversation(self):
        number, email = self.pick("status='processing'")
        steps = [("I want to cancel my order", "order number"), (str(number), "email"),
                 (email, "Reply yes or no"), ("yes", "has been canceled")]
        for text, expected in steps:
            out = self.say(text)
            self.assertIn(expected, out["reply"])
        self.assertEqual(self.field(number, "status"), "canceled")
        self.assertEqual(out["tool"], "cancel")

    def test_late_order_conversation_ends_in_a_ticket_request(self):
        number, email = self.pick("status='shipped' AND estimated_delivery <= date('now', '-5 day')")
        self.say("where is my package")
        self.say(str(number))
        out = self.say(email)
        self.assertEqual(out["decision"], "escalate")
        self.assertEqual(out["classification"]["category"], "DELIVERY")
        self.assertEqual(out["order_ref"], str(number))
        self.assertIn("late", out["reason"])

    def test_general_question_during_a_finished_flow_goes_to_the_knowledge_base(self):
        number, email = self.pick("status='delivered'")
        self.say(f"where is my order {number} {email}")
        out = self.say("what is your refund policy")
        self.assertIsNone(out["tool"])
        self.assertEqual(out["reply"], "generated reply")


if __name__ == "__main__":
    unittest.main()