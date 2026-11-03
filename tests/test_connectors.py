import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "telegram-setup" / "kit"))
import connectors  # noqa: E402


def write_settings(folder, data):
    path = Path(folder) / "settings.json"
    path.write_text(json.dumps(data) if not isinstance(data, str) else data)
    return path


ALL_ROLES = {
    "hosting": "http",
    "connectors": {
        "tg_main": {"role": "read", "clients": ["desktop", "code"], "port": 47601},
        "tg_saved": {"role": "saved", "port": 47602},
        "tg_bots": {"role": "bots", "clients": ["desktop", "code"], "port": 47603},
        "tg_send": {"role": "send", "clients": ["code"], "port": 47604},
        "tg_one": {"role": "chat", "clients": ["code"], "allow_file": "one.txt", "port": 47605},
        "tg_reader": {"role": "reader", "clients": []},
    },
}


class DefaultsTest(unittest.TestCase):
    def test_without_settings_file_the_install_runs_the_three_desktop_connectors(self):
        with tempfile.TemporaryDirectory() as d:
            s = connectors.load(Path(d) / "settings.json")
        self.assertEqual(list(s.connectors), ["tg_main", "tg_saved", "tg_bots"])
        self.assertEqual([len(c.role.tools) for c in s.connectors.values()], [14, 1, 6])
        self.assertEqual(s.service, "claude-telegram")
        self.assertFalse(s.hosted)
        self.assertTrue(all(c.clients == ("desktop",) for c in s.connectors.values()))
        self.assertTrue(all(c.session == "session" and c.device == "Claude Desktop" for c in s.connectors.values()))

    def test_default_file_roots_match_the_old_launcher(self):
        with tempfile.TemporaryDirectory() as d:
            s = connectors.load(Path(d) / "settings.json")
        self.assertEqual(s.files_dir(s.connector("tg_main")), s.state / "files")
        self.assertEqual(s.files_dir(s.connector("tg_bots")), Path(d) / "outbox")
        self.assertEqual(s.allow_path(s.connector("tg_bots")), Path(d) / "bots.txt")


class RolesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = connectors.load(write_settings(self.tmp.name, ALL_ROLES))

    def tearDown(self):
        self.tmp.cleanup()

    def test_send_role_has_the_eight_writes_plus_the_scheduled_list(self):
        send = self.settings.connector("tg_send").role
        self.assertEqual(set(send.tools), {"send_message", "reply_to_message", "edit_message", "pin_message",
                                           "unpin_message", "schedule_topic_message", "get_scheduled_messages",
                                           "delete_scheduled_message", "send_file"})
        self.assertEqual(set(send.asks), set(send.tools) - {"get_scheduled_messages"})

    def test_one_chat_role_has_exactly_four_tools_and_asks_nothing(self):
        one = self.settings.connector("tg_one").role
        self.assertEqual(set(one.tools), {"send_message", "edit_message", "list_messages", "get_message_link"})
        self.assertEqual(one.asks, ())

    def test_read_role_cannot_send(self):
        tools = set(self.settings.connector("tg_main").role.tools)
        self.assertFalse(tools & {"send_message", "reply_to_message", "edit_message", "send_file"})
        self.assertIn("save_draft", tools)

    def test_url_uses_the_literal_loopback_address(self):
        self.assertEqual(self.settings.url(self.settings.connector("tg_main")), "http://127.0.0.1:47601/mcp")


class RefusalTest(unittest.TestCase):
    def refused(self, data):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(connectors.SettingsError) as cm:
                connectors.load(write_settings(d, data))
        return str(cm.exception)

    def test_unknown_role_names_the_connector(self):
        msg = self.refused({"connectors": {"tg_x": {"role": "admin"}}})
        self.assertIn("tg_x", msg)

    def test_duplicate_port_names_the_connector(self):
        msg = self.refused({"connectors": {"tg_a": {"role": "read", "port": 47601},
                                           "tg_b": {"role": "read", "port": 47601}}})
        self.assertIn("tg_b", msg)

    def test_port_outside_range_names_the_connector(self):
        for port in (80, 70000, "47601"):
            self.assertIn("tg_a", self.refused({"connectors": {"tg_a": {"role": "read", "port": port}}}))

    def test_hosted_connector_without_port_is_refused(self):
        self.assertIn("tg_a", self.refused({"hosting": "http", "connectors": {"tg_a": {"role": "read"}}}))

    def test_one_chat_connector_without_allowlist_file_is_refused(self):
        self.assertIn("tg_a", self.refused({"connectors": {"tg_a": {"role": "chat"}}}))

    def test_invalid_json_is_refused_not_replaced_by_defaults(self):
        self.assertIn("not valid JSON", self.refused("{not json"))


class AllowlistTest(unittest.TestCase):
    def ids(self, text, one=False):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "list.txt"
            p.write_text(text)
            return connectors.read_ids(p, one=one)

    def test_comments_and_blank_lines_are_skipped(self):
        self.assertEqual(self.ids("# header\n123   # @somebot\n\n456\n"), ["123", "456"])

    def test_usernames_are_refused(self):
        with self.assertRaises(connectors.SettingsError):
            self.ids("@somebot\n")

    def test_one_chat_allows_a_group_id_but_only_one(self):
        self.assertEqual(self.ids("-42\n", one=True), ["-42"])
        with self.assertRaises(connectors.SettingsError):
            self.ids("-42\n-43\n", one=True)
        with self.assertRaises(connectors.SettingsError):
            self.ids("# none\n", one=True)


if __name__ == "__main__":
    unittest.main()
