import re
import subprocess
import sys
import unittest
from pathlib import Path

SETUP = Path(__file__).resolve().parents[1] / "skills" / "telegram-setup" / "kit" / "setup.sh"


@unittest.skipUnless(sys.platform == "darwin", "setup.sh runs on macOS only")
class UsageTest(unittest.TestCase):
    def test_an_unknown_command_lists_every_command(self):
        text = SETUP.read_text()
        cases = text.split('case "${1:-install}" in', 1)[1].split("\nesac", 1)[0]
        commands = {c for line in re.findall(r"^  ([a-z|]+)\)", cases, re.M) for c in line.split("|")}
        done = subprocess.run(["bash", str(SETUP), "no-such-command"], capture_output=True, text=True, timeout=10)
        self.assertEqual(done.returncode, 1)
        for command in commands:
            self.assertRegex(done.stdout, rf"bash setup\.sh\s+{command}\b")


if __name__ == "__main__":
    unittest.main()
