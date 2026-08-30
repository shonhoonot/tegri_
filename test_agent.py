import unittest
import re
import json
import hmac
import hashlib
from collections import OrderedDict

# Import modules from current directory
import main
import telegram


class TestAgentExtraction(unittest.TestCase):
    def test_standard_order(self):
        sample = """Таны захиалгыг хүлээн авлаа.
<<<ORDER>>>
{
  "name": "Болд",
  "phone": "99112233",
  "address": "БЗД 26-р хороо"
}
<<<ORDER>>>"""
        clean, order = main.extract_order(sample)
        self.assertEqual(clean, "Таны захиалгыг хүлээн авлаа.")
        self.assertIsNotNone(order)
        self.assertEqual(order["name"], "Болд")
        self.assertEqual(order["phone"], "99112233")
        self.assertEqual(order["price"], 450000)
        self.assertEqual(order["delivery"], 10000)
        self.assertEqual(order["assembly"], 10000)

    def test_markdown_fenced_json(self):
        sample = """Таны захиалгыг хүлээн авлаа.
<<<ORDER>>>
```json
{
  "name": "Болд",
  "phone": "99112233",
  "address": "БЗД 26-р хороо"
}
```
<<<ORDER>>>"""
        clean, order = main.extract_order(sample)
        self.assertEqual(clean, "Таны захиалгыг хүлээн авлаа.")
        self.assertIsNotNone(order)
        self.assertEqual(order["phone"], "99112233")

    def test_unclosed_order_tag(self):
        sample = """Таны захиалгыг хүлээн авлаа.
<<<ORDER>>>
{
  "name": "Болд",
  "phone": "99112233",
  "address": "БЗД 26-р хороо"
}"""
        clean, order = main.extract_order(sample)
        self.assertEqual(clean, "Таны захиалгыг хүлээн авлаа.")
        self.assertIsNotNone(order)
        self.assertEqual(order["phone"], "99112233")
        # Ensure user does NOT see the <<<ORDER>>> tag
        self.assertNotIn("<<<ORDER>>>", clean)
        self.assertNotIn("99112233", clean)

    def test_case_insensitive_order_tag(self):
        sample = """Баярлалаа!
<<<order>>>
{
  "name": "Болд",
  "phone": "99112233",
  "address": "БЗД 26-р хороо"
}
<<<order>>>"""
        clean, order = main.extract_order(sample)
        self.assertEqual(clean, "Баярлалаа!")
        self.assertIsNotNone(order)
        self.assertEqual(order["phone"], "99112233")

    def test_embedded_commentary_with_json(self):
        sample = """Баярлалаа!
<<<ORDER>>>
Захиалгын мэдээлэл:
{
  "name": "Болд",
  "phone": "99112233",
  "address": "БЗД 26-р хороо"
}
Баярлалаа
<<<ORDER>>>"""
        clean, order = main.extract_order(sample)
        self.assertEqual(clean, "Баярлалаа!")
        self.assertIsNotNone(order)
        self.assertEqual(order["phone"], "99112233")

    def test_missing_required_fields(self):
        sample_no_phone = """<<<ORDER>>>
{
  "name": "Болд",
  "address": "БЗД 26-р хороо"
}
<<<ORDER>>>"""
        clean, order = main.extract_order(sample_no_phone)
        self.assertIsNone(order)

        sample_no_address = """<<<ORDER>>>
{
  "name": "Болд",
  "phone": "99112233"
}
<<<ORDER>>>"""
        clean, order = main.extract_order(sample_no_address)
        self.assertIsNone(order)

    def test_invalid_json(self):
        sample_corrupted = """Баярлалаа!
<<<ORDER>>>
{ invalid json here }
<<<ORDER>>>"""
        clean, order = main.extract_order(sample_corrupted)
        self.assertEqual(clean, "Баярлалаа!")
        self.assertIsNone(order)


class TestOrderDeduplication(unittest.TestCase):
    def setUp(self):
        main._sent_orders.clear()

    def test_order_deduplication_lifecycle(self):
        sender = "user_123"
        order = {"phone": "+976 9911-2233", "address": "БЗД 26-р хороо"}

        # Initially not sent
        self.assertFalse(main.is_order_already_sent(sender, order))

        # Mark sent
        main.mark_order_sent(sender, order)
        self.assertTrue(main.is_order_already_sent(sender, order))

        # Normalized duplicate check with different formatting
        formatted_order = {"phone": "99112233", "address": "  БЗД   26-р хороо  "}
        self.assertTrue(main.is_order_already_sent(sender, formatted_order))

        # Different phone is not considered duplicate
        diff_phone_order = {"phone": "99112234", "address": "БЗД 26-р хороо"}
        self.assertFalse(main.is_order_already_sent(sender, diff_phone_order))


class TestWebhookSecurityAndHelpers(unittest.TestCase):
    def test_verify_signature(self):
        body = b'{"hello": "world"}'
        secret = "supersecret"
        sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

        # Temporarily mock APP_SECRET
        old_secret = main.APP_SECRET
        try:
            main.APP_SECRET = secret
            self.assertTrue(main.verify_signature(body, sig))
            self.assertFalse(main.verify_signature(body, "sha256=invalid"))
            self.assertFalse(main.verify_signature(body, None))

            # When APP_SECRET is unset, it passes through
            main.APP_SECRET = None
            self.assertTrue(main.verify_signature(body, None))
        finally:
            main.APP_SECRET = old_secret

    def test_already_seen_lru(self):
        main._seen_message_ids.clear()
        self.assertFalse(main.already_seen("mid_1"))
        self.assertTrue(main.already_seen("mid_1"))

    def test_trim_history(self):
        hist = [
            {"role": "user", "content": "1"},
            {"role": "assistant", "content": "2"},
            {"role": "user", "content": "3"},
            {"role": "assistant", "content": "4"},
        ]
        # Simulate max history = 3
        if len(hist) > 3:
            del hist[: len(hist) - 3]
        if hist and hist[0]["role"] == "assistant":
            del hist[0]
        self.assertEqual(len(hist), 2)
        self.assertEqual(hist[0]["role"], "user")


class TestTelegramFormatting(unittest.TestCase):
    def test_esc_html_entities(self):
        self.assertEqual(telegram._esc("<script>alert(1)</script>"), "&lt;script&gt;alert(1)&lt;/script&gt;")
        self.assertEqual(telegram._esc("А & Б"), "А &amp; Б")
        self.assertEqual(telegram._esc('"quotes"'), "&quot;quotes&quot;")

    def test_esc_null_and_empty(self):
        self.assertEqual(telegram._esc(None), "—")
        self.assertEqual(telegram._esc(""), "—")
        self.assertEqual(telegram._esc("null"), "—")
        self.assertEqual(telegram._esc("None"), "—")
        self.assertEqual(telegram._esc("  NULL  "), "—")

    def test_money(self):
        self.assertEqual(telegram._money(450000, 450000), 450000)
        self.assertEqual(telegram._money("450000", 450000), 450000)
        self.assertEqual(telegram._money("450,000", 450000), 450000)
        self.assertEqual(telegram._money(None, 10000), 10000)
        self.assertEqual(telegram._money("bad_val", 10000), 10000)


class TestFastAPIEndpoints(unittest.TestCase):
    def setUp(self):
        from starlette.testclient import TestClient
        self.client = TestClient(main.app)

    def test_root_and_health_endpoints(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"status": "ok", "service": "tegri-agent"})

        res_h = self.client.get("/health")
        self.assertEqual(res_h.status_code, 200)
        self.assertEqual(res_h.json(), {"status": "ok", "service": "tegri-agent"})

    def test_webhook_verify_endpoint(self):
        old_token = main.VERIFY_TOKEN
        try:
            main.VERIFY_TOKEN = "my_verify_token"
            # Correct verification
            res = self.client.get("/webhook?hub.mode=subscribe&hub.verify_token=my_verify_token&hub.challenge=123456")
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.text, "123456")

            # Incorrect token
            res_fail = self.client.get("/webhook?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=123456")
            self.assertEqual(res_fail.status_code, 403)
        finally:
            main.VERIFY_TOKEN = old_token

    def test_webhook_post_bad_json(self):
        res = self.client.post("/webhook", content=b"invalid json")
        self.assertEqual(res.status_code, 400)

    def test_webhook_post_non_page_object(self):
        res = self.client.post("/webhook", json={"object": "user"})
        self.assertEqual(res.status_code, 404)


if __name__ == "__main__":
    unittest.main()

