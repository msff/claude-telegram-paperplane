"""Relay Claude Desktop's stdio connection to a connector hosted on 127.0.0.1.

Desktop's config only starts stdio commands, so when hosting is on the launcher
runs this instead of a second Telegram server: each JSON-RPC line from stdin is
POSTed to the hosted connector and each reply goes back as one stdout line.
Standard library only, so a bridge stays small.
"""
import http.client
import json
import os
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

from connectors import kc_read

LSOF = "/usr/sbin/lsof"
RETRY_SECONDS = 30      # the server opens its port only after it reaches Telegram
TIMEOUT = 600           # a tool call may wait out a Telegram flood limit
EXIT_GRACE = 10         # on stdin EOF, how long replies still in flight may finish


def listener_owned(port):
    """True when the listener on 127.0.0.1:<port> belongs to this user.

    The token goes only to our own server: another local user could bind a
    stopped connector's port to collect it.
    """
    try:
        done = subprocess.run([LSOF, "-nP", "-a", f"-iTCP@127.0.0.1:{port}", "-sTCP:LISTEN",
                               "-u", str(os.getuid()), "-t"],
                              capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0 and bool(done.stdout.strip())


class Unreachable(Exception):
    """The hosted connector cannot answer this request; the text says why."""


def request_ids(message):
    """IDs of the requests in a message, which are owed a reply even when the server is down."""
    items = message if isinstance(message, list) else [message]
    return [m["id"] for m in items if isinstance(m, dict) and "method" in m and m.get("id") is not None]


class Bridge:
    def __init__(self, name, url, token, out=None, doctor="the Telegram doctor",
                 retry=RETRY_SECONDS, owned=listener_owned, reread=None):
        parts = urlsplit(url)
        if parts.scheme != "http" or parts.hostname != "127.0.0.1" or not parts.port:
            raise ValueError(f"{name}: hosted URL must be http://127.0.0.1:<port>/..., not {url!r}")
        self.name, self.port, self.path = name, parts.port, parts.path or "/"
        self.token = token
        self.reread = reread    # reads the token again: a hosted server makes a new one each time it starts
        self.out = out if out is not None else sys.stdout.buffer
        self.doctor = doctor
        self.retry = retry
        self.owned = owned
        self.lock = threading.Lock()
        self.learned = {}   # protocol version and session ID, sent back on later requests

    def run(self, inp=None):
        inp = inp if inp is not None else sys.stdin.buffer
        threads = []
        for raw in inp:
            line = raw.strip()
            if not line:
                continue
            t = threading.Thread(target=self.handle, args=(line,), daemon=True)
            t.start()
            threads = [x for x in threads if x.is_alive()] + [t]
        deadline = time.monotonic() + EXIT_GRACE
        for t in threads:
            t.join(max(0.0, deadline - time.monotonic()))

    def handle(self, line):
        try:
            message = json.loads(line)
        except ValueError:
            self.write({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            return
        try:
            reply = self.post(line)
        except Unreachable as e:
            for i in request_ids(message):
                self.write({"jsonrpc": "2.0", "id": i, "error": {
                    "code": -32603, "message": f"Telegram connector {self.name} {e}. Run {self.doctor}."}})
            return
        if reply is not None:
            self.learn(message, reply)
            self.write(reply)

    def post(self, body):
        """Send one message; the parsed reply, or None when the server accepted it without one.

        A 401 comes from the guard before the connector runs anything, so after
        one the message goes out once more if the token has changed since.
        """
        if not self.token and self.reread:
            self.token = self.reread()
        if not self.token:
            raise Unreachable("has no hosting token in the Keychain")
        status, data = self.send(body)
        if status == 401 and self.reread:
            fresh = self.reread()
            if fresh and fresh != self.token:
                self.token = fresh
                status, data = self.send(body)
        if status == 401:
            raise Unreachable("refused the token (it does not match the one in the Keychain)")
        if status == 403:
            raise Unreachable("refused the request")
        if status == 202 and not data.strip():
            return None
        try:
            return json.loads(data)
        except ValueError:
            raise Unreachable(f"sent a reply that is not JSON (HTTP {status})") from None

    def send(self, body):
        """POST once to our own listener; the HTTP status and body."""
        deadline = time.monotonic() + self.retry
        delay = 0.25
        while True:
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=TIMEOUT)
            try:
                conn.connect()
                break
            except ConnectionRefusedError:
                conn.close()
                if time.monotonic() + delay > deadline:
                    raise Unreachable(f"is not running (nothing answers on port {self.port})") from None
                time.sleep(delay)
                delay = min(delay * 2, 1.0)
            except OSError as e:
                conn.close()
                raise Unreachable(f"cannot be reached ({e})") from None
        # Never retried once the connector may have run it: a repeated tool call could send a message twice.
        try:
            if not self.owned(self.port):
                raise Unreachable(f"port {self.port} is held by a process that is not yours, "
                                  "so the token was not sent")
            headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                       "Authorization": f"Bearer {self.token}", **self.learned}
            conn.request("POST", self.path, body=body, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            session = resp.getheader("mcp-session-id")
        except (OSError, http.client.HTTPException) as e:
            raise Unreachable(f"dropped the connection ({e!r})") from None
        finally:
            conn.close()
        if session:
            self.learned["mcp-session-id"] = session
        return resp.status, data

    def learn(self, message, reply):
        if isinstance(message, dict) and message.get("method") == "initialize" and isinstance(reply, dict):
            version = (reply.get("result") or {}).get("protocolVersion")
            if isinstance(version, str):
                self.learned["mcp-protocol-version"] = version

    def write(self, message):
        line = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
        with self.lock:
            self.out.write(line)
            self.out.flush()


def run(settings, c):
    """Relay stdin and stdout to this install's hosted copy of the connector."""
    def reread():
        return kc_read(settings.service, settings.token_item(c))

    Bridge(c.name, settings.url(c), reread(), reread=reread,
           doctor=f"'{settings.home}/setup.sh doctor'").run()
