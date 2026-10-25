import unittest

from support import OrdersCase


class FlowCase(OrdersCase):
    def setUp(self):
        super().setUp()
        import flow
        self.flow = flow
        self.state = {}

    def send(self, text):
        return self.flow.handle(text, self.state, "conv-test")


class DetectActionTests(unittest.TestCase):
    def setUp(self):
        import sys
        from support import ROOT
        sys.path.insert(0, str(ROOT / "app"))
        import flow
        self.detect = flow.detect_action

    def test_cancel_order(self):
        for text in ("I want to cancel my order", "cancel order 12345", "want help to cancel the purchase"):
            self.assertEqual(self.detect(text), "cancel", text)

    def test_cancel_other_things_is_not_an_order_flow(self):
        for text in ("cancel my subscription to your newsletter", "what is the cancellation fee for my order",
                     "how do I cancel my account"):
            self.assertIsNone(self.detect(text), text)

    def test_address_and_phone(self):
        self.assertEqual(self.detect("I want to change my delivery address"), "address")
        self.assertEqual(self.detect("the address on my order is wrong"), "address")
        self.assertEqual(self.detect("I need to update my phone number"), "phone")

    def test_status(self):
        for text in ("where is my package", "where is my order", "when will my order arrive",
                     "my parcel is late", "I still haven't received my order", "where is my refund"):
            self.assertEqual(self.detect(text), "status", text)

    def test_general_questions_are_left_to_the_knowledge_base(self):
        for text in ("how do I reset my password", "what are your delivery options",
                     "what is your refund policy", "hello", "where is your store address"):
            self.assertIsNone(self.detect(text), text)


class StatusFlowTests(FlowCase):
    def test_on_time_order_step_by_step(self):
        number, email = self.pick("status='shipped' AND estimated_delivery >= date('now', '+1 day')")
        first = self.send("where is my package")
        self.assertIn("order number", first["reply"])
        second = self.send(str(number))
        self.assertIn("email", second["reply"])
        third = self.send(email)
        self.assertEqual(third["decision"], "answer")
        self.assertIn("was shipped", third["reply"])
        self.assertIsNone(self.state["awaiting"])

    def test_everything_in_one_message(self):
        number, email = self.pick("status='processing' AND estimated_delivery >= date('now', '+1 day')")
        result = self.send(f"where is my order {number}, my email is {email}")
        self.assertEqual(result["decision"], "answer")
        self.assertIn("has not shipped yet", result["reply"])

    def test_email_first_then_number(self):
        number, email = self.pick("status='processing' AND estimated_delivery >= date('now', '+1 day')")
        self.send("where is my order")
        self.send(f"my email is {email}")
        result = self.send(str(number))
        self.assertIn("has not shipped yet", result["reply"])

    def test_email_digits_are_not_mistaken_for_an_order_number(self):
        number, email = self.pick("status='processing'")
        self.send("where is my order")
        self.send(str(number))
        self.send(email)
        self.assertEqual(self.state["order_no"], number)

    def test_very_late_order_is_escalated_with_order_reference(self):
        number, email = self.pick("status='shipped' AND estimated_delivery <= date('now', '-5 day')")
        self.send("where is my package")
        self.send(str(number))
        result = self.send(email)
        self.assertEqual(result["decision"], "escalate")
        self.assertIn("{tid}", result["reply"])
        self.assertEqual(result["category"], "DELIVERY")
        self.assertEqual(result["order_ref"], str(number))

    def test_delivered_order_is_answered(self):
        number, email = self.pick("status='delivered'")
        result = self.send(f"status of my order {number} {email}")
        self.assertEqual(result["decision"], "answer")
        self.assertIn("was delivered", result["reply"])

    def test_new_order_number_asks_for_email_again(self):
        first, first_email = self.pick("status='delivered'")
        second, _ = self.pick(f"status='processing' AND order_no != {first}")
        self.send(f"where is my order {first} {first_email}")
        result = self.send(f"where is my order {second}")
        self.assertIn("email", result["reply"])


class VerificationTests(FlowCase):
    def test_wrong_email_asks_again(self):
        number, _ = self.pick("status='processing'")
        self.send("where is my order")
        self.send(str(number))
        result = self.send("nobody@example.com")
        self.assertIn("couldn't match", result["reply"])
        self.assertEqual(self.state["awaiting"], "order_no")

    def test_three_failures_escalate(self):
        number, _ = self.pick("status='processing'")
        self.send("where is my order")
        result = None
        for _ in range(3):
            self.send(str(number))
            result = self.send("nobody@example.com")
        self.assertEqual(result["decision"], "escalate")
        self.assertIn("{tid}", result["reply"])
        self.assertIsNone(self.state["awaiting"])
        self.assertEqual(self.state["fails"], 0)

    def test_successful_lookup_resets_failures(self):
        number, email = self.pick("status='processing'")
        self.send("where is my order")
        self.send(str(number))
        self.send("nobody@example.com")
        self.assertEqual(self.state["fails"], 1)
        self.send(str(number))
        self.send(email)
        self.assertEqual(self.state["fails"], 0)


class AddressFlowTests(FlowCase):
    def verified_start(self, text="I want to change my delivery address"):
        number, email = self.pick("status='processing'")
        self.send(text)
        self.send(str(number))
        reply = self.send(email)
        return number, email, reply

    def test_address_changed_after_confirmation(self):
        number, email, reply = self.verified_start()
        self.assertIn("new address", reply["reply"])
        confirm = self.send("14 Maple St, Riverton")
        self.assertIn("Reply yes", confirm["reply"])
        self.assertNotEqual(self.field(number, "shipping_address"), "14 Maple St, Riverton")
        done = self.send("yes")
        self.assertIn("Done", done["reply"])
        self.assertEqual(self.field(number, "shipping_address"), "14 Maple St, Riverton")
        self.assertEqual(len(self.orders.recent_changes(number)), 1)

    def test_declining_changes_nothing(self):
        number, email, _ = self.verified_start()
        before = self.field(number, "shipping_address")
        self.send("14 Maple St, Riverton")
        result = self.send("no")
        self.assertIn("nothing was changed", result["reply"])
        self.assertEqual(self.field(number, "shipping_address"), before)
        self.assertEqual(self.orders.recent_changes(number), [])

    def test_invalid_address_is_asked_again(self):
        number, email, _ = self.verified_start()
        result = self.send("x")
        self.assertIn("between 10 and 200", result["reply"])
        self.assertEqual(self.state["awaiting"], "new_address")

    def test_unclear_confirmation_does_not_change_anything(self):
        number, email, _ = self.verified_start()
        before = self.field(number, "shipping_address")
        self.send("14 Maple St, Riverton")
        result = self.send("hmm")
        self.assertIn("yes", result["reply"])
        self.assertEqual(self.field(number, "shipping_address"), before)

    def test_shipped_order_is_refused(self):
        number, email = self.pick("status='shipped'")
        self.send("I want to change my delivery address")
        self.send(str(number))
        result = self.send(email)
        self.assertIn("can't change the address", result["reply"])
        self.assertIsNone(self.state["awaiting"])

    def test_never_mind_stops_the_request(self):
        self.send("I want to change my delivery address")
        result = self.send("never mind")
        self.assertIn("stopped", result["reply"])
        self.assertIsNone(self.state["awaiting"])


class PhoneFlowTests(FlowCase):
    def test_phone_changed_after_confirmation(self):
        number, email = self.pick("status='shipped'")
        self.send("I need to update my phone number")
        self.send(str(number))
        self.send(email)
        bad = self.send("12ab")
        self.assertIn("8 to 15 digits", bad["reply"])
        self.send("+1 555 010 4321")
        done = self.send("yes")
        self.assertIn("Done", done["reply"])
        self.assertEqual(self.field(number, "phone"), "+15550104321")


class CancelFlowTests(FlowCase):
    def start(self):
        number, email = self.pick("status='processing'")
        self.send("I want to cancel my order")
        self.send(str(number))
        return number, self.send(email)

    def test_cancel_after_confirmation(self):
        number, reply = self.start()
        self.assertIn("Reply yes or no", reply["reply"])
        self.assertEqual(self.field(number, "status"), "processing")
        done = self.send("yes")
        self.assertIn("has been canceled", done["reply"])
        self.assertEqual(self.field(number, "status"), "canceled")

    def test_declining_keeps_the_order(self):
        number, _ = self.start()
        self.send("no")
        self.assertEqual(self.field(number, "status"), "processing")

    def test_shipped_order_cannot_be_canceled(self):
        number, email = self.pick("status='shipped'")
        self.send("I want to cancel my order")
        self.send(str(number))
        result = self.send(email)
        self.assertIn("can't cancel", result["reply"])
        self.assertEqual(self.field(number, "status"), "shipped")


class FallThroughTests(FlowCase):
    def test_unrelated_message_is_not_handled(self):
        self.assertIsNone(self.send("how do I reset my password"))

    def test_flow_is_dropped_after_two_unrelated_answers(self):
        self.send("where is my order")
        first = self.send("what is your refund policy")
        self.assertIn("order number", first["reply"])
        self.assertIsNone(self.send("what is your refund policy"))
        self.assertIsNone(self.state["awaiting"])


if __name__ == "__main__":
    unittest.main()