import asyncio
import contextlib
import io
import json
import os
import sys
import tempfile
import textwrap
import types
import unittest
from pathlib import Path
from unittest import mock

KIT = Path(__file__).resolve().parents[1] / "skills" / "telegram-setup" / "kit"
sys.path.insert(0, str(KIT))
import connectors  # noqa: E402
import serve  # noqa: E402
from guard import Guard  # noqa: E402

SECRETS = {"api_id": "1", "api_hash": "h", "session": "s", "user_id": "42", "http/tg_main": "tok"}


def read(service, item):
    return SECRETS.get(item)


def settings_in(folder, **extra):
    data = {"state": str(Path(folder) / "state"), "hosting": "http", "connectors": {
        "tg_main": {"role": "read", "clients": ["desktop", "code"], "port": 47601},
        "tg_saved": {"role": "saved", "port": 47602},
        "tg_bots": {"role": "bots", "port": 47603},
        "tg_one": {"role": "chat", "clients": ["code"], "allow_file": "one.txt", "port": 47604},
    }}
    data.update(extra)
    (Path(folder) / "settings.json").write_text(json.dumps(data))
    return connectors.load(Path(folder) / "settings.json")


class ArgumentsTest(unittest.TestCase):
    def test_legacy_surface_argument_selects_the_same_connector(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.check_legacy()

    def check_legacy(self):
        self.assertEqual(serve.parse_args(["tg_main"]), ("run", "tg_main"))
        self.assertEqual(serve.parse_args(["tg_main", "cc"]), ("run", "tg_main"))
        self.assertEqual(serve.parse_args(["tg_main", "desktop", "--host"]), ("host", "tg_main"))
        self.assertEqual(serve.parse_args(["--headers"]), ("headers", None))

    def test_unknown_connector_exits_non_zero(self):
        with self.assertRaises(SystemExit) as cm:
            serve.main(["tg_nope"])
        self.assertNotEqual(cm.exception.code, 0)

    def test_stray_arguments_are_refused(self):
        for argv in ([], ["--host"], ["tg_main", "--port", "1"], ["tg_main", "extra"]):
            with self.assertRaises(SystemExit):
                serve.parse_args(argv)


class HeadersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = settings_in(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def headers(self, name="tg_main", url="http://127.0.0.1:47601/mcp", owned=True):
        env = {"CLAUDE_CODE_MCP_SERVER_NAME": name, "CLAUDE_CODE_MCP_SERVER_URL": url}
        return serve.headers(self.settings, env, owned=lambda port: owned, read=read, wait=0)

    def test_recorded_url_and_own_listener_get_the_token(self):
        self.assertEqual(self.headers(), {"Authorization": "Bearer tok"})

    def test_everything_else_is_refused(self):
        for kw in ({"name": "tg_other"}, {"name": "../tg_main"}, {"name": ""},
                   {"url": "http://localhost:47601/mcp"}, {"url": "http://127.0.0.1:47602/mcp"},
                   {"owned": False}):
            with self.subTest(**kw), self.assertRaises(SystemExit):
                self.headers(**kw)

    def test_no_token_while_hosting_is_off(self):
        self.settings = settings_in(self.tmp.name, hosting="stdio")
        with self.assertRaises(SystemExit):
            self.headers()


class PrepareTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.settings = settings_in(self.tmp.name)
        self.connector = self.home / "chigwell"
        (self.connector / "telegram_mcp").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def prepare(self, name, transport="stdio", base=None):
        return serve.prepare(self.settings, self.settings.connector(name), transport, base or {"HOME": "/h"},
                             read=read, connector=self.connector)

    def test_inherited_telegram_and_mcp_variables_are_dropped(self):
        env = self.prepare("tg_main", base={"HOME": "/h", "TELEGRAM_SESSION_STRING_2": "x",
                                            "MCP_ALLOWED_HOSTS": "evil", "GROQ_API_KEY": "k"})
        self.assertEqual(env["HOME"], "/h")
        self.assertNotIn("TELEGRAM_SESSION_STRING_2", env)
        self.assertNotIn("MCP_ALLOWED_HOSTS", env)
        self.assertNotIn("GROQ_API_KEY", env)

    def test_hosted_connector_gets_loopback_and_its_port(self):
        env = self.prepare("tg_main", "http")
        self.assertEqual((env["MCP_TRANSPORT"], env["MCP_HOST"], env["MCP_PORT"]), ("http", "127.0.0.1", "47601"))
        self.assertEqual(env["TELEGRAM_ALLOWED_ROOTS"], str(self.settings.state / "files"))
        self.assertNotIn("TELEGRAM_ALLOWED_CHAT_IDS", env)

    def test_saved_messages_is_confined_to_the_users_own_chat(self):
        self.assertEqual(self.prepare("tg_saved")["TELEGRAM_ALLOWED_CHAT_IDS"], "42")

    def test_empty_bots_list_refuses_to_start(self):
        (self.home / "bots.txt").write_text("# no bots yet\n")
        with self.assertRaises(SystemExit):
            self.prepare("tg_bots")

    def test_one_chat_list_with_two_chats_refuses_to_start(self):
        (self.home / "one.txt").write_text("-1\n-2\n")
        with self.assertRaises(SystemExit):
            self.prepare("tg_one")
        (self.home / "one.txt").write_text("-1\n")
        self.assertEqual(self.prepare("tg_one")["TELEGRAM_ALLOWED_CHAT_IDS"], "-1")

    def test_dotenv_on_the_connectors_path_refuses_to_start(self):
        (self.connector / ".env").write_text("TELEGRAM_SESSION_STRING_X=1\n")
        with self.assertRaises(SystemExit):
            self.prepare("tg_main")

    def test_missing_keychain_item_refuses_to_start(self):
        with self.assertRaises(SystemExit):
            serve.prepare(self.settings, self.settings.connector("tg_main"), "stdio", {},
                          read=lambda s, i: None, connector=self.connector)


FAKE_RUNTIME = """
class _Tool:
    def __init__(self, name): self.name = name
class _Manager:
    def __init__(self, names): self.names = set(names)
    def list_tools(self): return [_Tool(n) for n in self.names]
    def remove_tool(self, n): self.names.discard(n)
class _Settings:
    json_response = False
class _MCP:
    def __init__(self, names):
        self._tool_manager = _Manager(names)
        self.settings = _Settings()
    def tool(self, **kw):
        def deco(f):
            self._tool_manager.names.add(f.__name__)
            return f
        return deco
    def streamable_http_app(self): return "the-app"
    async def run_streamable_http_async(self): pass
mcp = _MCP(NAMES)
roots = []
def ToolAnnotations(**kw): return kw
def with_account(**kw): return lambda f: f
def validate_id(*a): return lambda f: f
def _apply_exposed_tools_mode(): return []
"""

FAKE_MAIN = """
import os, sys
from telegram_mcp import runtime
def main():
    # chigwell's own parsing: every positional word becomes an allowed root
    runtime.roots[:] = [os.environ.get("TELEGRAM_ALLOWED_ROOTS", "")] + [a for a in sys.argv[1:] if not a.startswith("-")]
    runtime._apply_exposed_tools_mode()
"""


class HandoverTest(unittest.TestCase):
    """serve() against a stand-in connector with chigwell's import layout and argv parsing."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "telegram_mcp" / "tools").mkdir(parents=True)
        names = list(connectors.ROLES["read"].tools) + ["send_message", "get_full_chat"]
        names.remove("list_topic_messages")  # added by serve's extras
        (root / "telegram_mcp" / "__init__.py").write_text("")
        (root / "telegram_mcp" / "runtime.py").write_text(f"NAMES = {names!r}\n" + FAKE_RUNTIME)
        (root / "telegram_mcp" / "tools" / "__init__.py").write_text("")
        (root / "telegram_mcp" / "tools" / "messages.py").write_text("def message_to_dict(m, i): return {}\n")
        (root / "main.py").write_text(textwrap.dedent(FAKE_MAIN))
        self.root = root
        self.argv, self.path = sys.argv, list(sys.path)
        self.served = []

        async def record(config):
            self.served.append(config)

        sys.modules["uvicorn"] = types.SimpleNamespace(
            Config=lambda app, **kw: types.SimpleNamespace(app=app, **kw),
            Server=lambda config: types.SimpleNamespace(serve=lambda: record(config)))

    def tearDown(self):
        sys.argv, sys.path[:] = self.argv, self.path
        for name in [m for m in sys.modules if m in ("main", "uvicorn") or m.startswith("telegram_mcp")]:
            del sys.modules[name]
        self.tmp.cleanup()

    def run_serve(self, argv, token=None):
        sys.argv = ["serve.py", *argv]
        with contextlib.redirect_stderr(io.StringIO()):
            serve.serve(set(connectors.ROLES["read"].tools), token, 47601, connector=self.root)
        return sys.modules["telegram_mcp.runtime"]

    def test_launcher_arguments_never_become_file_roots(self):
        for argv in (["tg_main", "cc"], ["tg_main", "--host"]):
            with self.subTest(argv=argv):
                rt = self.run_serve(argv)
                self.assertEqual(rt.roots, [""])
                self.tearDown()
                self.setUp()

    def test_only_the_roles_tools_survive(self):
        rt = self.run_serve(["tg_main"])
        self.assertEqual(rt.mcp._tool_manager.names, set(connectors.ROLES["read"].tools))

    def test_hosted_start_serves_the_guarded_app_on_loopback_only_with_json_replies(self):
        rt = self.run_serve(["tg_main", "--host"], token="tok")
        self.assertTrue(rt.mcp.settings.json_response)
        asyncio.run(rt.mcp.run_streamable_http_async())
        config, = self.served
        self.assertEqual((config.host, config.port), ("127.0.0.1", 47601))
        self.assertIsInstance(config.app, Guard)
        self.assertEqual((config.app.app, config.app.expected), ("the-app", b"Bearer tok"))

    def test_a_connector_without_a_streamable_http_app_is_refused(self):
        with self.assertRaises(serve.Refused):
            serve.host_http(types.SimpleNamespace(settings=types.SimpleNamespace(json_response=False)), "tok", 47601)


class HostedStartTest(unittest.TestCase):
    """main() in host mode, with the Keychain, the environment and the server stubbed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = settings_in(self.tmp.name)
        self.settings.state.mkdir()
        self.keychain, self.started = dict(SECRETS), []

    def tearDown(self):
        self.tmp.cleanup()

    def start(self, stored=True):
        def write(service, item, value, timeout=15):
            if stored:
                self.keychain[item] = value
            return stored

        err, cwd, umask = io.StringIO(), os.getcwd(), os.umask(0o022)
        try:
            with mock.patch.object(serve.connectors, "load", lambda: self.settings), \
                    mock.patch.object(serve, "kc_write", write), \
                    mock.patch.object(serve, "prepare", lambda s, c, transport, base: {"TRANSPORT": transport}), \
                    mock.patch.object(serve, "serve", lambda allow, token, port: self.started.append((token, port))), \
                    mock.patch.object(serve, "trim_log", lambda: None), \
                    mock.patch.dict(os.environ), contextlib.redirect_stderr(err):
                serve.main(["tg_main", "--host"])
        finally:
            os.chdir(cwd)
            os.umask(umask)
        return err.getvalue()

    def test_every_start_stores_and_serves_a_new_token(self):
        self.start()
        self.start()
        (first, port), (second, _) = self.started
        self.assertEqual(port, 47601)
        self.assertNotIn("tok", (first, second))
        self.assertNotEqual(first, second)
        self.assertEqual(self.keychain["http/tg_main"], second)

    def test_a_refusal_exits_cleanly_so_launchd_does_not_restart_it(self):
        err = self.start(stored=False)
        self.assertIn("could not store a new hosting token", err)
        self.assertEqual(self.started, [])


if __name__ == "__main__":
    unittest.main()
