import argparse
import contextlib
import io
import json
import plistlib
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "telegram-setup" / "kit"))
import connectors  # noqa: E402
import tgsetup  # noqa: E402

L = str(tgsetup.LAUNCHER)


class FakeMac:
    """The Keychain, launchd and Claude Code's user config, held in memory."""

    def __init__(self, folder):
        self.keychain, self.code, self.loaded = {}, {}, set()
        self.agents = Path(folder) / "LaunchAgents"

    def claude(self, *args):
        if args[:2] == ("mcp", "add-json"):
            self.code[args[2]] = json.loads(args[3])
        elif args[:2] == ("mcp", "remove"):
            self.code.pop(args[2], None)
        return subprocess.CompletedProcess(args, 0, "", "")

    def launchctl(self, *args):
        if args[0] == "bootstrap":
            self.loaded.add(plistlib.loads(Path(args[2]).read_bytes())["Label"])
        elif args[0] == "bootout":
            self.loaded.discard(args[1].rsplit("/", 1)[1])
        return subprocess.CompletedProcess(args, 0, "", "")

    def patches(self, settings):
        conf = mock.MagicMock(return_value=settings)
        return [mock.patch.object(tgsetup, name, value) for name, value in {
            "conf": conf, "LAUNCH_AGENTS": self.agents, "launchctl": self.launchctl,
            "claude": self.claude, "claude_cli": lambda: "claude", "code_servers": lambda: dict(self.code),
            "kc_exists": lambda n: n in self.keychain, "kc_read": self.keychain.get,
            "kc_write": self.keychain.__setitem__, "kc_delete": lambda n: self.keychain.pop(n, None),
            "answers": lambda s, c, wait=0: {"result": {}},
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
        with contextlib.ExitStack() as stack:
            for p in self.mac.patches(self.settings):
                stack.enter_context(p)
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            tgsetup.cmd_host(argparse.Namespace(action=action, forget_tokens=forget_tokens))
        self.settings = connectors.load(self.settings.home / "settings.json")

    def agent(self, name):
        return self.mac.agents / f"{tgsetup.agent_label(self.settings, self.settings.connector(name))}.plist"

    def test_plist_runs_the_launcher_in_host_mode_and_holds_no_token(self):
        self.host("on")
        data = self.agent("tg_main").read_bytes()
        job = plistlib.loads(data)
        self.assertEqual(job["ProgramArguments"], [L, "tg_main", "--host"])
        self.assertTrue(job["RunAtLoad"] and job["KeepAlive"])
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
