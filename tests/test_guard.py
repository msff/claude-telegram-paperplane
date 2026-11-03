import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "telegram-setup" / "kit"))
from guard import Guard  # noqa: E402

TOKEN = "s3cret-token"


def call(scope):
    """Run one ASGI call through the guard; returns (status or None, whether the app ran)."""
    reached, sent = [], []

    async def app(scope, receive, send):
        reached.append(scope["type"])

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        sent.append(message)

    asyncio.run(Guard(app, TOKEN)(scope, receive, send))
    status = next((m["status"] for m in sent if m["type"] == "http.response.start"), None)
    return status, bool(reached)


def http(*headers):
    return {"type": "http", "method": "POST", "path": "/mcp", "headers": list(headers)}


AUTH = (b"authorization", f"Bearer {TOKEN}".encode())


class GuardTest(unittest.TestCase):
    def test_no_token_is_refused(self):
        self.assertEqual(call(http()), (401, False))

    def test_wrong_token_of_the_same_length_is_refused(self):
        wrong = (b"authorization", f"Bearer {'x' * len(TOKEN)}".encode())
        self.assertEqual(call(http(wrong)), (401, False))

    def test_origin_header_is_refused_even_with_the_token(self):
        self.assertEqual(call(http(AUTH, (b"origin", b"https://example.com"))), (403, False))

    def test_right_token_without_origin_reaches_the_app(self):
        self.assertEqual(call(http(AUTH)), (None, True))

    def test_lifespan_events_reach_the_app(self):
        self.assertEqual(call({"type": "lifespan"}), (None, True))

    def test_websocket_never_reaches_the_app(self):
        self.assertEqual(call({"type": "websocket", "headers": [AUTH]}), (None, False))

    def test_empty_token_is_refused_at_construction(self):
        with self.assertRaises(ValueError):
            Guard(lambda *a: None, "")


if __name__ == "__main__":
    unittest.main()
