import unittest
from datetime import date, timedelta

from support import OrdersCase


class LookupTests(OrdersCase):
    def test_right_email_finds_order(self):
        number, email = self.pick("status='processing'")
        self.assertTrue(self.orders.lookup_order(number, email)["ok"])

    def test_hash_and_uppercase_are_accepted(self):
        number, email = self.pick("status='processing'")
        self.assertTrue(self.orders.lookup_order(f"#{number}", email.upper())["ok"])

    def test_wrong_email_and_unknown_order_look_the_same(self):
        number, email = self.pick("status='processing'")
        wrong_email = self.orders.lookup_order(number, "nobody@example.com")
        unknown = self.orders.lookup_order(99999999, email)
        self.assertEqual(wrong_email, unknown)
        self.assertEqual(wrong_email["error"], "no_match")

    def test_garbage_order_number(self):
        self.assertFalse(self.orders.lookup_order("abc", "a@b.com")["ok"])

    def test_public_view_hides_private_fields(self):
        number, email = self.pick("status='processing'")
        order = self.orders.lookup_order(number, email)["order"]
        self.assertNotIn("customer_email", order)
        self.assertNotIn("phone", order)
        self.assertTrue(order["phone_masked"].endswith("**"))


class StatusMessageTests(OrdersCase):
    def order(self, where):
        number, email = self.pick(where)
        return self.orders.lookup_order(number, email)["order"]

    def test_shipped_mentions_tracking(self):
        order = self.order("status='shipped'")
        text = self.orders.status_message(order)["text"]
        self.assertIn(str(order["order_no"]), text)
        self.assertIn(order["tracking_number"], text)

    def test_late_order_counts_days(self):
        order = self.order("status='shipped' AND estimated_delivery <= date('now', '-5 day')")
        info = self.orders.status_message(order)
        self.assertGreaterEqual(info["days_late"], 5)
        self.assertIn("late", info["text"])

    def test_on_time_order_is_not_late(self):
        order = self.order("status='processing' AND estimated_delivery >= date('now', '+1 day')")
        self.assertEqual(self.orders.status_message(order)["days_late"], 0)

    def test_today_can_be_injected(self):
        order = self.order("status='processing' AND estimated_delivery >= date('now', '+1 day')")
        future = date.today() + timedelta(days=400)
        self.assertGreater(self.orders.status_message(order, today=future)["days_late"], 300)

    def test_delivered_order_is_never_late(self):
        order = self.order("status='delivered'")
        self.assertEqual(self.orders.status_message(order)["days_late"], 0)

    def test_canceled_order_mentions_refund(self):
        order = self.order("status='canceled' AND refund_status='refund_pending'")
        self.assertIn("refund", self.orders.status_message(order)["text"])


class AddressTests(OrdersCase):
    def test_change_before_shipping(self):
        number, email = self.pick("status='processing'")
        result = self.orders.change_address(number, email, "12 O'Brien St, Springfield", conv_id="c1")
        self.assertTrue(result["ok"])
        self.assertEqual(self.field(number, "shipping_address"), "12 O'Brien St, Springfield")

    def test_change_is_audited(self):
        number, email = self.pick("status='processing'")
        self.orders.change_address(number, email, "12 Oak St, Springfield", conv_id="c1")
        changes = self.orders.recent_changes(number)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["conv_id"], "c1")
        self.assertEqual(changes[0]["new_value"], "12 Oak St, Springfield")

    def test_refused_after_shipping(self):
        number, email = self.pick("status='shipped'")
        before = self.field(number, "shipping_address")
        result = self.orders.change_address(number, email, "5 Pine St, Riverton")
        self.assertEqual(result["error"], "not_allowed")
        self.assertEqual(self.field(number, "shipping_address"), before)
        self.assertEqual(self.orders.recent_changes(number), [])

    def test_refused_with_wrong_email(self):
        number, _ = self.pick("status='processing'")
        result = self.orders.change_address(number, "other@example.com", "5 Pine St, Riverton")
        self.assertEqual(result["error"], "no_match")

    def test_invalid_values(self):
        number, email = self.pick("status='processing'")
        for bad in ("x", "no digits at all here", "1 A St'; DROP TABLE orders;--", "<b>1 Main St</b>"):
            self.assertEqual(self.orders.change_address(number, email, bad)["error"], "invalid", bad)
        self.assertGreater(self.query("SELECT COUNT(*) AS n FROM orders")["n"], 0)


class PhoneTests(OrdersCase):
    def test_allowed_while_shipped_and_normalized(self):
        number, email = self.pick("status='shipped'")
        result = self.orders.change_phone(number, email, "+1 (555) 010-1234")
        self.assertTrue(result["ok"])
        self.assertEqual(self.field(number, "phone"), "+15550101234")

    def test_refused_after_delivery(self):
        number, email = self.pick("status='delivered'")
        self.assertEqual(self.orders.change_phone(number, email, "+15550101234")["error"], "not_allowed")

    def test_invalid_phone(self):
        number, email = self.pick("status='processing'")
        for bad in ("12ab", "123", "+" + "1" * 20):
            self.assertEqual(self.orders.change_phone(number, email, bad)["error"], "invalid", bad)


class CancelTests(OrdersCase):
    def test_cancel_before_shipping(self):
        number, email = self.pick("status='processing'")
        result = self.orders.cancel_order(number, email, conv_id="c2")
        self.assertTrue(result["ok"])
        self.assertEqual(self.field(number, "status"), "canceled")
        self.assertIn(result["refund_status"], ("refund_pending", "not_charged"))
        self.assertEqual(self.field(number, "refund_status"), result["refund_status"])

    def test_paid_order_gets_pending_refund(self):
        number, email = self.pick("status='processing' AND payment_status='paid'")
        self.assertEqual(self.orders.cancel_order(number, email)["refund_status"], "refund_pending")

    def test_cancel_twice_is_refused(self):
        number, email = self.pick("status='processing'")
        self.orders.cancel_order(number, email)
        self.assertEqual(self.orders.cancel_order(number, email)["error"], "not_allowed")

    def test_shipped_order_cannot_be_canceled(self):
        number, email = self.pick("status='shipped'")
        self.assertEqual(self.orders.cancel_order(number, email)["error"], "not_allowed")
        self.assertEqual(self.field(number, "status"), "shipped")

    def test_no_edits_on_canceled_order(self):
        number, email = self.pick("status='processing'")
        self.orders.cancel_order(number, email)
        self.assertEqual(self.orders.change_address(number, email, "5 Pine St, Riverton")["error"], "not_allowed")


class AdminTests(OrdersCase):
    def test_admin_view_has_full_record_and_history(self):
        number, email = self.pick("status='processing'")
        self.orders.change_address(number, email, "12 Oak St, Springfield")
        view = self.orders.admin_view(number)
        self.assertEqual(view["customer_email"], email)
        self.assertEqual(len(view["changes"]), 1)
        self.assertNotIn("olist_order_id", view)

    def test_admin_view_unknown_order(self):
        self.assertIsNone(self.orders.admin_view(99999999))

    def test_demo_samples_credentials_work(self):
        samples = self.orders.demo_samples()
        self.assertTrue(samples)
        for sample in samples:
            self.assertTrue(self.orders.lookup_order(sample["order_no"], sample["email"])["ok"])


if __name__ == "__main__":
    unittest.main()