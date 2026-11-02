import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "telegram-setup" / "kit"))
import connectors  # noqa: E402
import tgsetup  # noqa: E402

L = str(tgsetup.LAUNCHER)
HELPER = f"{L} --headers"


def settings(hosting="http"):
    folder = tempfile.mkdtemp()
    path = Path(folder) / "settings.json"
    path.write_text(json.dumps({"hosting": hosting, "connectors": {
        "tg_main": {"role": "read", "clients": ["desktop", "code"], "port": 47601},
        "tg_bots": {"role": "bots", "clients": ["desktop", "code"], "port": 47603},
        "tg_send": {"role": "send", "clients": ["code"], "port": 47604},
    }}))
    return connectors.load(path)


def stdio(*args):
    return {"type": "stdio", "command": L, "args": list(args), "env": {}}


def http(port):
    return {"type": "http", "url": f"http://127.0.0.1:{port}/mcp", "headersHelper": HELPER}


class ClashTest(unittest.TestCase):
    def test_todays_split_registration_is_reported(self):
        problems = tgsetup.clash_problems(settings("stdio"),
                                          {"tg_main": {"command": L, "args": ["tg_main", "desktop"]}},
                                          {"tg_main": stdio("tg_main", "cc")})
        self.assertEqual(len(problems), 1)
        self.assertIn("tg_main", problems[0])
        self.assertIn("stale", problems[0])

    def test_desktop_launcher_and_code_http_on_the_same_port_agree(self):
        self.assertEqual(tgsetup.clash_problems(settings(), {"tg_main": {"command": L, "args": ["tg_main"]}},
                                                {"tg_main": http(47601)}), [])

    def test_same_stdio_launcher_in_both_agrees(self):
        self.assertEqual(tgsetup.clash_problems(settings("stdio"), {"tg_main": {"command": L, "args": ["tg_main"]}},
                                                {"tg_main": stdio("tg_main")}), [])

    def test_code_http_entry_on_another_connectors_port_is_reported(self):
        problems = tgsetup.clash_problems(settings(), {"tg_main": {"command": L, "args": ["tg_main"]}},
                                          {"tg_main": http(47603)})
        self.assertEqual(len(problems), 1)
        self.assertIn("tg_main", problems[0])

    def test_someone_elses_connector_under_one_of_our_names_is_reported(self):
        problems = tgsetup.clash_problems(settings(), {"tg_main": {"command": "/opt/other/server", "args": []}},
                                          {"tg_main": http(47601)})
        self.assertEqual(len(problems), 1)

    def test_names_that_are_not_ours_are_left_alone(self):
        self.assertEqual(tgsetup.clash_problems(settings(), {"things": {"command": "/a/things", "args": []}},
                                                {"things": {"type": "stdio", "command": "/b/things", "args": []}}), [])

    def test_desktop_only_entry_passes(self):
        self.assertEqual(tgsetup.clash_problems(settings(), {"tg_main": {"command": L, "args": ["tg_main"]}}, {}), [])


class AskRulesTest(unittest.TestCase):
    def test_write_tool_without_its_ask_rule_is_named(self):
        s = settings()
        rules = [f"mcp__tg_send__{t}" for t in connectors.ROLES["send"].asks if t != "send_file"]
        self.assertEqual(tgsetup.missing_asks(s, {"tg_send": http(47604)}, rules), ["mcp__tg_send__send_file"])

    def test_a_server_wide_rule_covers_every_tool(self):
        self.assertEqual(tgsetup.missing_asks(settings(), {"tg_send": http(47604)}, ["mcp__tg_send"]), [])

    def test_connector_not_in_claude_code_needs_no_rule(self):
        self.assertEqual(tgsetup.missing_asks(settings(), {}, []), [])

    def test_read_role_has_nothing_to_ask(self):
        self.assertEqual(tgsetup.missing_asks(settings(), {"tg_main": http(47601)}, []), [])


class EntriesTest(unittest.TestCase):
    def test_code_entry_follows_hosting(self):
        self.assertEqual(tgsetup.code_entry(settings(), settings().connector("tg_main")), http(47601))
        s = settings("stdio")
        self.assertEqual(tgsetup.code_entry(s, s.connector("tg_main")), stdio("tg_main"))

    def test_ours_recognises_both_shapes_and_nothing_else(self):
        self.assertTrue(tgsetup.ours(http(47601)))
        self.assertTrue(tgsetup.ours({"command": L, "args": ["tg_main"]}))
        self.assertFalse(tgsetup.ours({"type": "http", "url": "http://127.0.0.1:47601/mcp",
                                       "headersHelper": "/somewhere/else --headers"}))
        self.assertFalse(tgsetup.ours({"command": "/somewhere/else"}))


class StaleAllowlistTest(unittest.TestCase):
    def test_an_allowlist_edited_after_its_server_started_is_reported(self):
        s = settings()
        bots = s.home / "bots.txt"
        bots.write_text("123\n")
        edited = datetime.fromtimestamp(bots.stat().st_mtime)
        tg_bots = s.connector("tg_bots")
        self.assertTrue(tgsetup.allowlist_newer(s, tg_bots, started=lambda port: edited - timedelta(minutes=1)))
        self.assertFalse(tgsetup.allowlist_newer(s, tg_bots, started=lambda port: edited))
        self.assertFalse(tgsetup.allowlist_newer(s, tg_bots, started=lambda port: None))
        self.assertFalse(tgsetup.allowlist_newer(s, s.connector("tg_main"), started=lambda port: edited))


class DesktopStartTest(unittest.TestCase):
    def test_start_time_is_read_whatever_the_users_locale(self):
        def ps(args, env=None, **kwargs):
            # ps prints its dates in the locale it is given; this user's own puts the day first.
            c = (env or os.environ).get("LC_ALL") == "C"
            date = "Mon Nov  2 16:56:42 2026" if c else "Mon  2 Nov 16:56:42 2026"
            return subprocess.CompletedProcess(args, 0, f"{date}     /Applications/Claude.app/Contents/MacOS/Claude\n", "")
        with mock.patch.dict(os.environ, {"LANG": "en_GB.UTF-8", "LC_ALL": ""}), \
                mock.patch.object(tgsetup.subprocess, "run", ps):
            self.assertEqual(tgsetup.claude_started(), datetime(2026, 11, 2, 16, 56, 42))


if __name__ == "__main__":
    unittest.main()
