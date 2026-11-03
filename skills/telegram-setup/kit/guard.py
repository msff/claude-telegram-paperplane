"""Let only Claude's own clients reach a hosted connector.

A plain ASGI wrapper around the connector's HTTP app. Anything that runs as
another local user, and any web page the user opens, can reach a port on
127.0.0.1; neither holds the connector's token. Browsers always send an Origin
header on a cross-site POST, while Claude Code and the bridge never do, so a
request with one is refused even when it carries the token. The connector's
own Host check (on for a 127.0.0.1 server) stays in place behind this.
"""
import hmac


class Guard:
    def __init__(self, app, token):
        if not token:
            raise ValueError("a hosted connector needs a token")
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.app(scope, receive, send)
        if scope["type"] != "http":
            return  # no websocket routes; returning unaccepted closes it
        headers = scope.get("headers") or []
        if any(name == b"origin" for name, _ in headers):
            return await refuse(send, 403, "requests from web pages are refused")
        given = next((value for name, value in headers if name == b"authorization"), b"")
        if not hmac.compare_digest(given, self.expected):
            return await refuse(send, 401, "missing or wrong token")
        await self.app(scope, receive, send)


async def refuse(send, status, text):
    body = text.encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})
