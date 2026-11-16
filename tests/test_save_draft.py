"""save_draft that reads the draft back: serve.replace_save_draft(rt).

chigwell's save_draft answers "Draft saved" as soon as messages.saveDraft
returns true, even when a Telegram app with the chat open erases the draft a
moment later. serve.replace_save_draft swaps it for a tool that saves the same
way, reads the chat's draft back with messages.getPeerDialogs and reports what
the server holds.

No network, no Telegram session, no Telethon or FastMCP: a stand-in for
chigwell's runtime module records the requests the tool builds, and a stand-in
client answers them. Each test name starts with the number of the rule it checks:

 1  exactly one save_draft after the swap; serve() still exposes exactly the read role's tools
 2  the same parameters as upstream
 3  no_webpage reaches SaveDraftRequest unchanged, with the resolved peer and the text
 4  reply_to_msg_id becomes InputReplyToMessage, otherwise reply_to is None
 5  one read, GetPeerDialogsRequest(peers=[peer]), the draft taken from dialogs[0]
 6  the same text read back, outer whitespace aside: "Draft saved to chat <id> and read back",
    with the length, the server time and the open-chat warning
 7  no draft read back: "Draft NOT confirmed", "found no draft", never "Draft saved to chat"
 8  another text read back: "found a different draft (<m> characters instead of <n>"
 9  an empty message clears: "Draft cleared in chat", or "Draft NOT cleared" with text left
10  a failed save returns log_and_format_error("save_draft", e, chat_id=...) and reads nothing
11  a failed read: "reading it back failed (<Error>)" and "NOT confirmed"
12  serve.py imports only the standard library at the top; replace_save_draft imports no Telethon
"""
import ast
import asyncio
import contextlib
import inspect
import io
import os
import pkgutil
import sys
import sysconfig
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path

KIT = Path(__file__).resolve().parents[1] / "skills" / "telegram-setup" / "kit"
sys.path.insert(0, str(KIT))
import connectors  # noqa: E402
import serve  # noqa: E402

CHAT_ID = 42
TEXT = "Draft text for the test"  # 23 characters
SERVER_TIME = datetime(2026, 3, 4, 5, 6, 7, tzinfo=timezone.utc)
CONFIRMED = f"Draft saved to chat {CHAT_ID} and read back"
UPSTREAM_REPLY = f"Draft saved to chat {CHAT_ID}. Open the chat in Telegram to see and send it."
FORMATTED_ERROR = "formatted save_draft error"


# --- Telethon stand-ins: requests that remember how they were built ---

class Request:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def __repr__(self):
        return f"{type(self).__name__}({self.kwargs!r})"


class SaveDraftRequest(Request):
    pass


class GetPeerDialogsRequest(Request):
    pass


class InputReplyToMessage(Request):
    pass


class Peer:
    """What resolve_input_entity returns; only that function makes one."""

    def __init__(self, chat_id):
        self.chat_id = chat_id


def draft(text):
    return types.SimpleNamespace(message=text, date=SERVER_TIME)


EMPTY_DRAFT = types.SimpleNamespace(date=SERVER_TIME)  # like draftMessageEmpty: no message field


def dialog(d):
    return types.SimpleNamespace(draft=d)


class Client:
    """Answers saveDraft with True and getPeerDialogs with the dialogs given; records every request."""

    def __init__(self, dialogs, save_error=None, read_error=None):
        self.dialogs = list(dialogs)
        self.save_error, self.read_error = save_error, read_error
        self.requests = []

    async def __call__(self, request):
        self.requests.append(request)
        if isinstance(request, SaveDraftRequest):
            if self.save_error:
                raise self.save_error
            return True
        if isinstance(request, GetPeerDialogsRequest):
            if self.read_error:
                raise self.read_error
            return types.SimpleNamespace(dialogs=self.dialogs, messages=[], chats=[], users=[])
        raise AssertionError(f"unexpected request {request!r}")


class ReadBackStandInError(Exception):
    pass


# --- FastMCP stand-in: the first tool registered under a name stays, like FastMCP's ToolManager ---

class Tool:
    def __init__(self, name, fn):
        self.name, self.fn = name, fn


class ToolManager:
    def __init__(self):
        self.tools = {}
        self.dropped = []  # registrations FastMCP would ignore because the name is taken

    def add_tool(self, fn, name=None, **kw):
        name = name or fn.__name__
        if name in self.tools:
            self.dropped.append(name)
            return self.tools[name]
        self.tools[name] = Tool(name, fn)
        return self.tools[name]

    def get_tool(self, name):
        return self.tools.get(name)

    def list_tools(self):
        return list(self.tools.values())

    def remove_tool(self, name):
        if name not in self.tools:
            raise KeyError(f"Unknown tool: {name}")
        del self.tools[name]


class MCP:
    def __init__(self, names):
        self._tool_manager = ToolManager()
        for name in names:
            self._tool_manager.add_tool(upstream_save_draft if name == "save_draft" else other_tool, name=name)

    def tool(self, name=None, **kw):
        def deco(fn):
            self._tool_manager.add_tool(fn, name=name)
            return fn
        return deco

    def add_tool(self, fn, name=None, **kw):
        self._tool_manager.add_tool(fn, name=name)

    def remove_tool(self, name):
        self._tool_manager.remove_tool(name)


async def upstream_save_draft(chat_id, message, reply_to_msg_id=None, no_webpage=False, account=None):
    """chigwell's save_draft as pinned: answers without reading anything back."""
    return UPSTREAM_REPLY


async def other_tool(**kwargs):
    return "other tool"


def runtime(client, names=("save_draft",)):
    """A stand-in for chigwell's telegram_mcp.runtime module."""
    calls = types.SimpleNamespace(errors=[])

    async def ensure_connected(cl=None):
        return None

    async def resolve_input_entity(identifier, client=None):
        return Peer(identifier)

    def log_and_format_error(function_name, error, *args, **kwargs):
        calls.errors.append((function_name, error, args, kwargs))
        return FORMATTED_ERROR

    return types.SimpleNamespace(
        mcp=MCP(names),
        ToolAnnotations=lambda **kw: kw,
        with_account=lambda **kw: (lambda fn: fn),
        validate_id=lambda *names, **kw: (lambda fn: fn),
        get_client=lambda account=None: client,
        ensure_connected=ensure_connected,
        resolve_input_entity=resolve_input_entity,
        functions=types.SimpleNamespace(messages=types.SimpleNamespace(
            SaveDraftRequest=SaveDraftRequest, GetPeerDialogsRequest=GetPeerDialogsRequest)),
        types=types.SimpleNamespace(InputReplyToMessage=InputReplyToMessage),
        log_and_format_error=log_and_format_error,
        calls=calls,
    )


@contextlib.contextmanager
def modules(**replacements):
    """Put modules in sys.modules (None makes a name unimportable) and put back what was there."""
    names = {name.replace("__", "."): value for name, value in replacements.items()}
    saved = {name: sys.modules[name] for name in names if name in sys.modules}
    sys.modules.update(names)
    try:
        yield
    finally:
        for name in names:
            if name in saved:
                sys.modules[name] = saved[name]
            else:
                sys.modules.pop(name, None)


def standard_library():
    names = getattr(sys, "stdlib_module_names", None)  # Python 3.10+
    if names is not None:
        return set(names)
    # Python 3.9: what the interpreter itself ships, outside site-packages
    lib = sysconfig.get_paths()["stdlib"]
    return set(sys.builtin_module_names) | {m.name for m in pkgutil.iter_modules([lib, os.path.join(lib, "lib-dynload")])}


def imported(nodes, into_functions):
    """Top-level module names imported by these statements."""
    found = set()
    todo = list(nodes)
    while todo:
        node = todo.pop()
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            found.add(node.module.split(".")[0])
        if into_functions or not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            todo.extend(ast.iter_child_nodes(node))
    return found


class Base(unittest.TestCase):
    def replace(self, rt):
        replace_save_draft = getattr(serve, "replace_save_draft", None)
        if replace_save_draft is None:
            self.fail("serve.py has no replace_save_draft(rt) yet")
        replace_save_draft(rt)
        return rt

    def tool(self, rt):
        tool = rt.mcp._tool_manager.get_tool("save_draft")
        self.assertIsNotNone(tool, "no save_draft is registered")
        return tool.fn

    def save(self, message=TEXT, dialogs=None, save_error=None, read_error=None, **kwargs):
        """Swap the tool in, call it as an MCP client would, return (reply, client, rt)."""
        client = Client([dialog(draft(message))] if dialogs is None else dialogs, save_error, read_error)
        rt = self.replace(runtime(client))
        reply = asyncio.run(self.tool(rt)(chat_id=CHAT_ID, message=message, **kwargs))
        return reply, client, rt

    def sent(self, client):
        saves = [r for r in client.requests if isinstance(r, SaveDraftRequest)]
        self.assertEqual(len(saves), 1, client.requests)
        return saves[0].kwargs

    def assert_not_confirmed_no_draft(self, reply):
        self.assertTrue(reply.startswith("Draft NOT confirmed"), reply)
        self.assertIn("found no draft", reply)
        self.assertNotIn("Draft saved to chat", reply)


class SaveDraftTest(Base):
    def test_rule_01_exactly_one_save_draft_after_the_swap_and_it_is_the_new_one(self):
        rt = self.replace(runtime(Client([])))
        manager = rt.mcp._tool_manager
        self.assertEqual([t.name for t in manager.list_tools()].count("save_draft"), 1)
        self.assertIsNot(manager.get_tool("save_draft").fn, upstream_save_draft,
                         "upstream save_draft is still the registered one")
        self.assertNotIn("save_draft", manager.dropped,
                         "a save_draft was registered while the upstream one was still there; FastMCP drops it")

    def test_rule_02_parameters_are_upstreams(self):
        params = inspect.signature(self.tool(self.replace(runtime(Client([]))))).parameters
        self.assertEqual({name: p.default for name, p in params.items()}, {
            "chat_id": inspect.Parameter.empty,
            "message": inspect.Parameter.empty,
            "reply_to_msg_id": None,
            "no_webpage": False,
            "account": None,
        })

    def test_rule_03_no_webpage_true_reaches_save_draft_request_unchanged(self):
        _, client, _ = self.save(no_webpage=True)
        sent = self.sent(client)
        self.assertIsInstance(sent.get("peer"), Peer, "peer is not the one resolve_input_entity returned")
        self.assertEqual(sent["peer"].chat_id, CHAT_ID)
        self.assertEqual(sent.get("message"), TEXT)
        self.assertIs(sent.get("no_webpage"), True)
        self.assertIsNone(sent.get("reply_to"))

    def test_rule_03_no_webpage_false_is_not_turned_on(self):
        for kwargs in ({}, {"no_webpage": False}):
            with self.subTest(**kwargs):
                _, client, _ = self.save(**kwargs)
                self.assertFalse(self.sent(client).get("no_webpage"))

    def test_rule_04_reply_to_msg_id_becomes_input_reply_to_message(self):
        _, client, _ = self.save(reply_to_msg_id=7)
        reply_to = self.sent(client).get("reply_to")
        self.assertIsInstance(reply_to, InputReplyToMessage)
        self.assertEqual(reply_to.kwargs.get("reply_to_msg_id"), 7)

    def test_rule_04_without_reply_to_msg_id_reply_to_is_none(self):
        _, client, _ = self.save()
        self.assertIsNone(self.sent(client).get("reply_to"))

    def test_rule_05_one_get_peer_dialogs_read_with_the_saved_peer(self):
        reply, client, _ = self.save()
        self.assertEqual([type(r) for r in client.requests], [SaveDraftRequest, GetPeerDialogsRequest])
        save, read = client.requests
        peers = list(read.kwargs.get("peers") or [])
        self.assertEqual(len(peers), 1, read)
        self.assertIs(peers[0], save.kwargs.get("peer"))
        # the only draft the stand-in server holds is dialogs[0].draft, and it matches
        self.assertTrue(reply.startswith(CONFIRMED), reply)

    def test_rule_06_same_text_read_back_is_confirmed_with_length_server_time_and_warning(self):
        reply, _, _ = self.save()
        self.assertTrue(reply.startswith(CONFIRMED), reply)
        self.assertRegex(reply, rf"\b{len(TEXT)}\b", "no character count")
        self.assertIn("2026-03-04", reply, "no server date")
        self.assertIn("05:06:07", reply, "no server time")
        self.assertRegex(reply, r"(?i)\bopen\b", "no warning about a chat left open")

    def test_rule_06_outer_whitespace_does_not_matter(self):
        for sent, held in ((f"  {TEXT}\n", TEXT), (TEXT, f"{TEXT} \n"), (f"\n{TEXT}", f" {TEXT}")):
            with self.subTest(sent=sent, held=held):
                reply, _, _ = self.save(message=sent, dialogs=[dialog(draft(held))])
                self.assertTrue(reply.startswith(CONFIRMED), reply)

    def test_rule_07_draft_field_none_is_not_confirmed(self):
        reply, _, _ = self.save(dialogs=[dialog(None)])
        self.assert_not_confirmed_no_draft(reply)

    def test_rule_07_draft_without_message_like_draft_message_empty_is_not_confirmed(self):
        reply, _, _ = self.save(dialogs=[dialog(EMPTY_DRAFT)])
        self.assert_not_confirmed_no_draft(reply)

    def test_rule_07_no_dialogs_is_not_confirmed(self):
        reply, _, _ = self.save(dialogs=[])
        self.assert_not_confirmed_no_draft(reply)

    def test_rule_08_different_text_read_back_is_not_confirmed(self):
        reply, _, _ = self.save(dialogs=[dialog(draft("Other text"))])
        self.assertTrue(reply.startswith("Draft NOT confirmed"), reply)
        self.assertIn(f"found a different draft (10 characters instead of {len(TEXT)}", reply)

    def test_rule_09_empty_message_with_nothing_read_back_is_cleared(self):
        for message in ("", "  \n"):
            for name, dialogs in (("draft None", [dialog(None)]), ("draftMessageEmpty", [dialog(EMPTY_DRAFT)]),
                                  ("no dialogs", [])):
                with self.subTest(message=message, read_back=name):
                    reply, _, _ = self.save(message=message, dialogs=dialogs)
                    self.assertTrue(reply.startswith("Draft cleared in chat"), reply)

    def test_rule_09_empty_message_with_text_left_on_the_server_is_not_cleared(self):
        for message in ("", "  \n"):
            with self.subTest(message=message):
                reply, _, _ = self.save(message=message, dialogs=[dialog(draft("Old draft"))])
                self.assertTrue(reply.startswith("Draft NOT cleared"), reply)

    def test_rule_10_failed_save_returns_log_and_format_error_and_reads_nothing(self):
        error = RuntimeError("CHAT_WRITE_FORBIDDEN stand-in")
        reply, client, rt = self.save(save_error=error)
        self.assertEqual(reply, FORMATTED_ERROR)
        self.assertEqual(rt.calls.errors, [("save_draft", error, (), {"chat_id": CHAT_ID})])
        self.assertFalse([r for r in client.requests if isinstance(r, GetPeerDialogsRequest)],
                         "a read was made after the save failed")

    def test_rule_11_failed_read_is_reported_not_confirmed(self):
        reply, _, _ = self.save(read_error=ReadBackStandInError("timed out"))
        self.assertIn("reading it back failed (ReadBackStandInError)", reply)
        self.assertIn("NOT confirmed", reply)


class ServeTest(Base):
    def run_serve(self, rt):
        """serve() against the stand-in runtime, with chigwell's import layout faked in sys.modules."""
        rt._apply_exposed_tools_mode = lambda: []
        package = types.ModuleType("telegram_mcp")
        tools = types.ModuleType("telegram_mcp.tools")
        messages = types.ModuleType("telegram_mcp.tools.messages")
        entry = types.ModuleType("main")
        package.__path__, tools.__path__ = [], []
        package.runtime, package.tools, tools.messages = rt, tools, messages
        messages.message_to_dict = lambda m, i: {}
        entry.main = lambda: rt._apply_exposed_tools_mode()  # chigwell's main runs the exposure filter
        argv, path = sys.argv, list(sys.path)
        try:
            with modules(main=entry, telegram_mcp=package, telegram_mcp__tools=tools,
                         telegram_mcp__tools__messages=messages), contextlib.redirect_stderr(io.StringIO()):
                serve.serve(set(connectors.ROLES["read"].tools), None, 0, connector=KIT / "no-connector-here")
        finally:
            sys.argv, sys.path[:] = argv, path

    def test_rule_01_serve_exposes_exactly_the_read_roles_tools_with_the_new_save_draft(self):
        names = list(connectors.ROLES["read"].tools) + ["send_message", "get_full_chat"]
        names.remove("list_topic_messages")  # added by serve's extras
        rt = runtime(Client([dialog(draft(TEXT))]), names)
        self.run_serve(rt)
        manager = rt.mcp._tool_manager
        self.assertEqual({t.name for t in manager.list_tools()}, set(connectors.ROLES["read"].tools))
        self.assertIsNot(manager.get_tool("save_draft").fn, upstream_save_draft,
                         "serve() kept upstream save_draft: replace_save_draft(runtime) is not called")
        reply = asyncio.run(self.tool(rt)(chat_id=CHAT_ID, message=TEXT))
        self.assertTrue(reply.startswith(CONFIRMED), reply)

    def test_rule_12_serve_imports_stay_standard_library_and_replace_save_draft_imports_no_telethon(self):
        tree = ast.parse((KIT / "serve.py").read_text())
        stdlib = standard_library()
        kit = {p.stem for p in KIT.glob("*.py")}
        self.assertEqual(imported(tree.body, into_functions=False) - stdlib - kit, set(),
                         "serve.py imports a third-party module at the top")
        fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "replace_save_draft"), None)
        if fn is None:
            self.fail("serve.py has no replace_save_draft(rt) yet")
        self.assertEqual(imported([fn], into_functions=True) - stdlib, set(),
                         "replace_save_draft imports a non-standard module itself instead of using rt")
        # and at run time: swap and call with Telethon, FastMCP and chigwell unimportable
        with modules(telethon=None, mcp=None, telegram_mcp=None):
            reply, _, _ = self.save(no_webpage=True, reply_to_msg_id=7)
        self.assertTrue(reply.startswith(CONFIRMED), reply)


if __name__ == "__main__":
    unittest.main()
