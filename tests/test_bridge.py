import io
import json
import socket
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "telegram-setup" / "kit"))
import bridge  # noqa: E402

TOKEN = "test-token"


class FakeConnector(BaseHTTPRequestHandler):
    """Answers like the hosted connector: JSON replies, 202 for notifications, 401 without the token."""

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.server.seen.append((dict(self.headers), json.loads(body)))
        if self.headers.get("Authorization") != f"Bearer {self.server.token}":
            return self.send(401, b"unauthorized", "text/plain")
        message = json.loads(body)
        if "id" not in message:
            return self.send(202, b"", None)
        if message["method"] == "slow":
            time.sleep(1)
        result = {"protocolVersion": "2025-06-18"} if message["method"] == "initialize" else {"echo": message["method"]}
        self.send(200, json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}, indent=2).encode(),
                  "application/json")

    def send(self, status, data, kind):
        self.send_response(status)
        if kind:
            self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def start_server(port=0, token=TOKEN):
    server = ThreadingHTTPServer(("127.0.0.1", port), FakeConnector)
    server.seen, server.token = [], token
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def lines(*messages):
    return io.BytesIO(b"".join(json.dumps(m).encode() + b"\n" for m in messages))


def relay(port, *messages, token=TOKEN, **kw):
    out = io.BytesIO()
    kw.setdefault("owned", lambda port: True)
    bridge.Bridge("tg_test", f"http://127.0.0.1:{port}/mcp", token, out=out, doctor="the doctor", **kw).run(
        lines(*messages))
    return [json.loads(x) for x in out.getvalue().splitlines()]


def request(i, method="tools/list"):
    return {"jsonrpc": "2.0", "id": i, "method": method}


class RelayTest(unittest.TestCase):
    def setUp(self):
        self.server = start_server()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_reply_is_relayed_as_one_line_and_a_notification_relays_nothing(self):
        out = io.BytesIO()
        b = bridge.Bridge("tg_test", f"http://127.0.0.1:{self.port}/mcp", TOKEN, out=out, owned=lambda p: True)
        b.run(lines(request(1, "initialize")))
        b.run(lines({"jsonrpc": "2.0", "method": "notifications/initialized"}, request(2)))
        got = out.getvalue().splitlines()
        self.assertEqual([json.loads(x)["id"] for x in got], [1, 2])
        self.assertEqual(self.server.seen[-1][0].get("mcp-protocol-version"), "2025-06-18")

    def test_slow_request_does_not_hold_back_a_later_reply(self):
        got = relay(self.port, request(1, "slow"), request(2))
        self.assertEqual([m["id"] for m in got], [2, 1])

    def test_a_401_says_the_token_does_not_match_and_names_the_doctor(self):
        got = relay(self.port, request(1), token="wrong-token")
        self.assertIn("token", got[0]["error"]["message"])
        self.assertIn("the doctor", got[0]["error"]["message"])

    def test_foreign_listener_never_receives_the_token(self):
        got = relay(self.port, request(1), owned=lambda port: False)
        self.assertEqual(self.server.seen, [])
        self.assertIn("not yours", got[0]["error"]["message"])

    def test_listener_check_finds_our_own_server(self):
        self.assertTrue(bridge.listener_owned(self.port))
        self.assertFalse(bridge.listener_owned(free_port()))


class StartupTest(unittest.TestCase):
    def test_server_that_starts_late_still_answers_initialize(self):
        port = free_port()
        holder = {}
        timer = threading.Timer(3, lambda: holder.setdefault("server", start_server(port)))
        timer.start()
        try:
            got = relay(port, request(1, "initialize"))
        finally:
            timer.join()
            holder["server"].shutdown()
            holder["server"].server_close()
        self.assertEqual(got[0]["result"]["protocolVersion"], "2025-06-18")

    def test_server_that_never_starts_gives_an_error_naming_the_connector(self):
        got = relay(free_port(), request(1), {"jsonrpc": "2.0", "method": "notifications/initialized"}, retry=1)
        self.assertEqual(len(got), 1)
        self.assertIn("tg_test", got[0]["error"]["message"])
        self.assertIn("not running", got[0]["error"]["message"])

    def test_missing_token_answers_without_connecting(self):
        got = relay(free_port(), request(1), token=None)
        self.assertIn("no hosting token", got[0]["error"]["message"])

    def test_url_other_than_loopback_is_refused(self):
        with self.assertRaises(ValueError):
            bridge.Bridge("tg_test", "http://localhost:47601/mcp", TOKEN)


if __name__ == "__main__":
    unittest.main()
