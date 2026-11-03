import argparse
import asyncio
import contextlib
import io
import json
import plistlib
import socket
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "telegram-setup" / "kit"))
import connectors  # noqa: E402
import tgsetup  # noqa: E402

L = str(tgsetup.LAUNCHER)


class FakeMac:
    """The Keychain, launchd and Claude Code's user config, held in memory.

    Like the real launchd, a booted-out job lingers for a few `print` calls
    (`lag`), and a bootstrap of a job that is still there fails with error 5.
    """

    def __init__(self, folder):
        self.keychain, self.code, self.loaded, self.leaving, self.silent = {}, {}, set(), {}, set()
        self.boots = []
        self.lag, self.refuse = 0, False
        self.agents = Path(folder) / "LaunchAgents"

    def claude(self, *args):
        if args[:2] == ("mcp", "add-json"):
            self.code[args[2]] = json.loads(args[3])
        elif args[:2] == ("mcp", "remove"):
            self.code.pop(args[2], None)
        return subprocess.CompletedProcess(args, 0, "", "")

    def launchctl(self, *args):
        if args[0] == "bootstrap":
            label = plistlib.loads(Path(args[2]).read_bytes())["Label"]
            if self.refuse or label in self.loaded or label in self.leaving:
                return subprocess.CompletedProcess(args, 5, "", "Bootstrap failed: 5: Input/output error")
            self.loaded.add(label)
            self.boots.append(label)
        elif args[0] == "bootout":
            label = args[1].rsplit("/", 1)[1]
            if label in self.loaded and self.lag:
                self.leaving[label] = self.lag
            self.loaded.discard(label)
        elif args[0] == "print":
            label = args[1].rsplit("/", 1)[1]
            if label in self.leaving:
                self.leaving[label] -= 1
                if not self.leaving[label]:
                    del self.leaving[label]
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 0 if label in self.loaded else 113, "", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    def answers(self, s, c, wait=0):
        if c.name in self.silent:
            raise tgsetup.bridge.Unreachable("is not running")
        return {"result": {}}

    def patches(self, settings):
        conf = mock.MagicMock(return_value=settings)
        return [mock.patch.object(tgsetup, name, value) for name, value in {
            "conf": conf, "LAUNCH_AGENTS": self.agents, "launchctl": self.launchctl,
            "claude": self.claude, "claude_cli": lambda: "claude", "code_servers": lambda: dict(self.code),
            "kc_exists": lambda n: n in self.keychain, "kc_read": self.keychain.get,
            "kc_write": self.keychain.__setitem__, "kc_delete": lambda n: self.keychain.pop(n, None),
            "answers": self.answers, "pause": lambda: None,
        }.items()]


class HostingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        home = Path(self.tmp.name)
        (home / "bots.txt").write_text("123   # @somebot\n")
        (home / "settings.json").write_text(json.dumps({"state": str(home / "state"), "connectors": {
            "tg_main": {"role": "read", "clients": ["desktop", "code"]},
            "tg_saved": {"role": "saved"},
            "tg_bots": {"role": "bots", "clients": ["desktop", "code"]},
            "tg_reader": {"role": "reader", "clients": []},
        }}))
        self.mac = FakeMac(home)
        self.mac.code["tg_main"] = {"type": "stdio", "command": L, "args": ["tg_main"], "env": {}}
        self.settings = connectors.load(home / "settings.json")

    def tearDown(self):
        self.tmp.cleanup()

    def host(self, action, forget_tokens=False):
        out = io.StringIO()
        with contextlib.ExitStack() as stack:
            for p in self.mac.patches(self.settings):
                stack.enter_context(p)
            stack.enter_context(contextlib.redirect_stdout(out))
            result = tgsetup.cmd_host(argparse.Namespace(action=action, forget_tokens=forget_tokens))
        self.settings = connectors.load(self.settings.home / "settings.json")
        return result, out.getvalue()

    def restart(self, names):
        out = io.StringIO()
        with contextlib.ExitStack() as stack:
            for p in self.mac.patches(self.settings):
                stack.enter_context(p)
            stack.enter_context(contextlib.redirect_stdout(out))
            failed = tgsetup.restart_hosted(self.settings, names)
        return failed, out.getvalue()

    def label(self, name):
        return tgsetup.agent_label(self.settings, self.settings.connector(name))

    def agent(self, name):
        return self.mac.agents / f"{tgsetup.agent_label(self.settings, self.settings.connector(name))}.plist"

    def test_plist_runs_the_launcher_in_host_mode_and_holds_no_token(self):
        self.host("on")
        data = self.agent("tg_main").read_bytes()
        job = plistlib.loads(data)
        self.assertEqual(job["ProgramArguments"], [L, "tg_main", "--host"])
        self.assertTrue(job["RunAtLoad"])
        self.assertEqual((job["KeepAlive"], job["ThrottleInterval"]), ({"SuccessfulExit": False}, 60))
        self.assertTrue(job["StandardErrorPath"].startswith(str(self.settings.state / "logs")))
        self.assertNotIn(self.mac.keychain["http/tg_main"].encode(), data)

    def test_host_on_gives_ports_tokens_agents_and_http_entries(self):
        self.host("on")
        hosted = [self.settings.connector(n) for n in ("tg_main", "tg_saved", "tg_bots")]
        self.assertTrue(self.settings.hosted)
        self.assertEqual(len({c.port for c in hosted}), 3)
        self.assertEqual(self.settings.connector("tg_reader").port, 0)
        self.assertEqual(set(self.mac.keychain), {"http/tg_main", "http/tg_saved", "http/tg_bots"})
        self.assertEqual(len(self.mac.loaded), 3)
        self.assertEqual({n: e["type"] for n, e in self.mac.code.items()}, {"tg_main": "http", "tg_bots": "http"})

    def test_host_off_leaves_stdio_settings_no_agents_and_no_http_entries(self):
        self.host("on")
        self.host("off")
        self.assertFalse(self.settings.hosted)
        self.assertEqual(list(self.mac.agents.glob("*.plist")), [])
        self.assertEqual(self.mac.loaded, set())
        self.assertEqual({e["type"] for e in self.mac.code.values()}, {"stdio"})
        self.assertEqual(len(self.mac.keychain), 3)

    def test_uninstall_after_host_on_leaves_no_agents_and_no_tokens(self):
        self.host("on")
        self.host("off", forget_tokens=True)
        self.assertEqual(list(self.mac.agents.glob("*.plist")), [])
        self.assertEqual(self.mac.keychain, {})

    def test_host_on_again_restarts_running_agents(self):
        self.host("on")
        self.mac.lag = 3
        result, out = self.host("on")
        self.assertEqual(result, 0)
        self.assertEqual(len(self.mac.loaded), 3)
        self.assertNotIn("✗", out)

    def test_host_on_reports_agents_launchd_refuses_and_fails(self):
        self.mac.refuse = True
        result, out = self.host("on")
        self.assertEqual(result, 3)
        self.assertIn("launchd refused", out)
        self.assertNotIn("Hosting is on", out)

    def test_host_on_reports_a_connector_that_does_not_answer(self):
        self.mac.silent.add("tg_saved")
        result, out = self.host("on")
        self.assertEqual(result, 1)
        self.assertIn("tg_saved", out.split("✗", 1)[1])
        self.assertNotIn("Hosting is on", out)

    def test_restart_starts_again_only_the_named_hosted_connectors(self):
        self.host("on")
        self.mac.boots.clear()
        self.mac.lag = 2
        failed, _ = self.restart({"tg_main", "tg_reader"})
        self.assertEqual(failed, [])
        self.assertEqual(self.mac.boots, [self.label("tg_main")])
        self.assertEqual(len(self.mac.loaded), 3)

    def test_restart_does_nothing_with_hosting_off(self):
        self.assertEqual(self.restart({"tg_main"}), ([], ""))
        self.assertEqual(self.mac.boots, [])

    def test_host_restart_restarts_every_hosted_connector_and_changes_nothing_else(self):
        self.host("on")
        code = dict(self.mac.code)
        self.mac.boots.clear()
        self.mac.lag = 2
        result, _ = self.host("restart")
        self.assertEqual(result, 0)
        self.assertEqual(sorted(self.mac.boots), sorted(self.label(n) for n in ("tg_main", "tg_saved", "tg_bots")))
        self.assertTrue(self.settings.hosted)
        self.assertEqual(self.mac.code, code)

    def test_host_restart_with_hosting_off_does_not_turn_it_on(self):
        self.assertEqual(self.host("restart"), (0, ""))
        self.assertEqual(self.mac.boots, [])
        self.assertFalse(self.settings.hosted)

    def test_host_restart_fails_when_a_connector_does_not_come_back(self):
        self.host("on")
        self.mac.silent.add("tg_saved")
        result, out = self.host("restart")
        self.assertEqual(result, 1)
        self.assertIn("tg_saved", out.split("✗ Not running", 1)[1])

    def test_restart_follows_the_bots_list_from_empty_to_listed_and_back(self):
        bots = self.settings.home / "bots.txt"
        bots.write_text("# none yet\n")
        self.host("on")
        bots.write_text("123   # @somebot\n")
        self.restart({"tg_bots"})
        self.assertIn(self.label("tg_bots"), self.mac.loaded)
        bots.write_text("# none yet\n")
        self.restart({"tg_bots"})
        self.assertNotIn(self.label("tg_bots"), self.mac.loaded)
        self.assertFalse(self.agent("tg_bots").exists())

    def test_adding_a_bot_restarts_the_connectors_that_read_bots_txt(self):
        self.mac.keychain["session"] = "s"

        class Client:
            async def connect(self): pass
            async def disconnect(self): pass
            async def get_entity(self, name): return types.SimpleNamespace(bot=True, id=456, username=name)

        with contextlib.ExitStack() as stack:
            for p in self.mac.patches(self.settings) + [
                    mock.patch.object(tgsetup, "BOTS", self.settings.home / "bots.txt"),
                    mock.patch.object(tgsetup, "api_credentials", lambda prompt=True: (1, "h")),
                    mock.patch.object(tgsetup, "client_for", lambda *a: Client()),
                    mock.patch.object(tgsetup, "restart_hosted")]:
                stack.enter_context(p)
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            asyncio.run(tgsetup.cmd_bots(argparse.Namespace(names=["OtherBot"])))
            tgsetup.restart_hosted.assert_called_once_with(mock.ANY, {"tg_bots"})
        self.assertIn("456", (self.settings.home / "bots.txt").read_text())

    def test_a_new_login_restarts_the_connectors_that_use_it_and_reports_failures(self):
        self.mac.keychain["session"] = "old"
        me = types.SimpleNamespace(id=7)

        class Client:
            session = types.SimpleNamespace(save=lambda: "new")
            async def connect(self): pass
            async def get_me(self): return me
            def is_connected(self): return False

        with contextlib.ExitStack() as stack:
            for p in self.mac.patches(self.settings) + [
                    mock.patch.object(tgsetup, "api_credentials", lambda prompt=True: (1, "h")),
                    mock.patch.object(tgsetup, "client_for", lambda *a: Client()),
                    mock.patch.object(tgsetup, "interactive_login", mock.AsyncMock()),
                    mock.patch.object(tgsetup, "whoami", mock.AsyncMock(return_value=me)),
                    mock.patch.object(tgsetup, "end_login", mock.AsyncMock(return_value=True)),
                    mock.patch.object(tgsetup, "label", lambda m: "someone"),
                    mock.patch.object(tgsetup, "restart_hosted", mock.MagicMock(return_value=["tg_main"]))]:
                stack.enter_context(p)
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            result = asyncio.run(tgsetup.cmd_login(argparse.Namespace(again=True, phone=False)))
            names = tgsetup.restart_hosted.call_args.args[1]
        self.assertEqual(self.mac.keychain["session"], "new")
        self.assertEqual(names, {"tg_main", "tg_saved", "tg_bots", "tg_reader"})
        self.assertEqual(result, 1)

    def test_register_marks_the_change_says_to_quit_a_running_desktop_and_drops_stale_code_entries(self):
        path = self.settings.home / "settings.json"
        raw = json.loads(path.read_text())
        for spec in raw["connectors"].values():
            spec["clients"] = ["desktop"] if spec.get("clients", ["desktop"]) else []
        path.write_text(json.dumps(raw))
        self.settings = connectors.load(path)
        config = self.settings.home / "claude_desktop_config.json"
        config.write_text("{}")
        out = io.StringIO()
        with contextlib.ExitStack() as stack:
            for p in self.mac.patches(self.settings) + [mock.patch.object(tgsetup, "DESKTOP_CONFIG", config),
                                                        mock.patch.object(tgsetup, "claude_started", lambda: 1)]:
                stack.enter_context(p)
            stack.enter_context(contextlib.redirect_stdout(out))
            tgsetup.cmd_register(argparse.Namespace(code=False))
        self.assertEqual(json.loads(config.read_text())["mcpServers"]["tg_main"], {"command": L, "args": ["tg_main"]})
        self.assertTrue(tgsetup.desktop_stamp(self.settings).exists())
        self.assertIn("quit it now", out.getvalue())
        self.assertNotIn("tg_main", self.mac.code)

    def test_connector_without_bots_is_not_hosted(self):
        (self.settings.home / "bots.txt").write_text("# none yet\n")
        self.host("on")
        self.assertFalse(self.agent("tg_bots").exists())
        self.assertNotIn("tg_bots", self.mac.code)


class PortTest(unittest.TestCase):
    def test_port_picking_skips_a_bound_port(self):
        with socket.socket() as held:
            held.bind(("127.0.0.1", 0))
            busy = held.getsockname()[1]
            picked = tgsetup.pick_port(set(), start=busy)
        self.assertNotEqual(picked, busy)
        self.assertNotEqual(tgsetup.pick_port({busy + 1}, start=busy + 1), busy + 1)


if __name__ == "__main__":
    unittest.main()
