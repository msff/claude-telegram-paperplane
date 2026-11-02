#!/bin/bash
# Starts one Telegram connector. Installed by setup.sh; Claude Desktop runs it
# once per connector in its config, launchd runs it for hosted connectors, and
# Claude Code runs it as the headers helper of a hosted connector.
#
# Usage: launch.sh <connector> [--host] | launch.sh --headers
#
# Everything else lives in serve.py, which reads the install's settings. The
# Telegram login lives only in the login Keychain and reaches the server
# through its environment; it never appears in argv or in a config file.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
exec "$HERE/chigwell/.venv/bin/python" "$HERE/serve.py" "$@"
