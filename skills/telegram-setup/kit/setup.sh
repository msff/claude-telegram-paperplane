#!/bin/bash
# Telegram for Claude Desktop: the one setup command.
#
#   bash setup.sh             install, or repair: every step skips what is already done
#   bash setup.sh doctor      health check
#   bash setup.sh bots        add bots the tg_bots connector may talk to
#   bash setup.sh login       check the Telegram login (--again: new login, --phone: by phone number)
#   bash setup.sh uninstall   remove everything this installed
#
# Installs into ~/.claude-telegram: the Telegram connector chigwell/telegram-mcp,
# pinned to a reviewed commit, plus this kit's launcher. The Telegram login goes
# into the macOS login Keychain (service "claude-telegram"), never into a file.
set -euo pipefail

DEST="$HOME/.claude-telegram"
STATE="$HOME/.local/state/claude-telegram"
REPO="https://github.com/chigwell/telegram-mcp.git"
PIN="9760717c2bb6a5caae90267b0fc3f7e8a12c95ce"   # v3.2.66
UV_VERSION="0.9.5"
SRC="$(cd "$(dirname "$0")" && pwd)"
PY="$DEST/chigwell/.venv/bin/python"

step() { printf '\n\033[1m%s\033[0m\n' "$*"; }
die()  { printf '✗ %s\n' "$*" >&2; exit 1; }
yes_no() { local a; read -r -p "$1 " a; case "$a" in [Yy]*) return 0 ;; [Nn]*) return 1 ;; *) [ "${2:-n}" = y ] ;; esac; }
helper() { "$PY" "$DEST/tgsetup.py" "$@"; }

[ "$(uname -s)" = Darwin ] || die "this setup is for macOS"

case "${1:-install}" in
  install) ;;
  doctor|bots|login|register)
    [ -x "$PY" ] || die "not installed yet: run  bash setup.sh  from the setup folder first"
    cmd="$1"; shift
    helper "$cmd" "$@"
    [ "$cmd" = bots ] && helper register
    exit 0 ;;
  uninstall)
    yes_no "Remove Telegram for Claude from this Mac? [y/N]" || exit 0
    if [ -x "$PY" ]; then
      helper unregister
      if yes_no "Also end the Telegram login (recommended)? [Y/n]" y; then
        helper logout
      else
        helper forget
        echo "The login stays under Telegram → Settings → Devices as \"Claude Desktop\" until you end it there."
      fi
    fi
    case "$DEST" in "$HOME"/?*) rm -rf "$DEST" ;; esac
    case "$STATE" in "$HOME"/?*) rm -rf "$STATE" ;; esac
    step "Removed. Quit Claude (⌘Q) and reopen it."
    echo "Skills you uploaded stay in Claude's settings (Skills) until you delete them there."
    exit 0 ;;
  *) sed -n '3,8p' "$0"; exit 1 ;;
esac

step "1/6  Checking this Mac"
[ -d /Applications/Claude.app ] || die "Claude Desktop is not in Applications: install it from claude.ai/download, open it once, then run this again"
xcode-select -p >/dev/null 2>&1 || die "git is missing: run  xcode-select --install , finish Apple's installer, then run this again"
UV="$(command -v uv || true)"
[ -z "$UV" ] && [ -x "$HOME/.local/bin/uv" ] && UV="$HOME/.local/bin/uv"
if [ -z "$UV" ]; then
  echo "This needs uv, a Python package manager from Astral (astral.sh), to set up the connector's Python."
  yes_no "Install uv $UV_VERSION now with its official installer? [y/N]" || die "uv is required; stopped"
  curl -LsSf "https://astral.sh/uv/$UV_VERSION/install.sh" | sh
  UV="$HOME/.local/bin/uv"
  [ -x "$UV" ] || die "uv did not install; see the messages above"
fi
echo "✓ macOS, Claude Desktop, git and uv are here"

step "2/6  Copying the kit to ~/.claude-telegram"
mkdir -p "$DEST/outbox"
chmod 700 "$DEST" "$DEST/outbox"
if [ "$SRC" != "$DEST" ]; then
  for f in setup.sh launch.sh; do install -m 700 "$SRC/$f" "$DEST/$f"; done
  for f in serve.py tgsetup.py README.md; do install -m 600 "$SRC/$f" "$DEST/$f"; done
fi
echo "✓ files in place"

step "3/6  Fetching the Telegram connector (chigwell/telegram-mcp, pinned)"
if [ ! -d "$DEST/chigwell/.git" ]; then
  rm -rf "$DEST/chigwell"
  git clone --quiet "$REPO" "$DEST/chigwell"
fi
if [ "$(git -C "$DEST/chigwell" rev-parse HEAD)" != "$PIN" ]; then
  # A newer kit pins a newer commit: move the checkout there.
  git -C "$DEST/chigwell" fetch --quiet origin
  git -C "$DEST/chigwell" -c advice.detachedHead=false checkout --quiet "$PIN" 2>/dev/null || true
fi
[ "$(git -C "$DEST/chigwell" rev-parse HEAD)" = "$PIN" ] \
  || die "the connector checkout is not at the reviewed commit; delete ~/.claude-telegram/chigwell and run this again"
(cd "$DEST/chigwell" && "$UV" sync --frozen --no-dev --quiet)
[ -x "$PY" ] || die "the connector's Python environment was not created; see the messages above"
echo "✓ connector ready"

step "4/6  Logging in to Telegram"
helper login

step "5/6  Bots (optional)"
if [ -s "$DEST/bots.txt" ] && grep -qE '^[[:space:]]*[0-9]' "$DEST/bots.txt"; then
  echo "✓ bots already listed (add more: bash ~/.claude-telegram/setup.sh bots)"
else
  helper bots
fi

step "6/6  Adding the connectors to Claude Desktop"
helper register

step "Health check"
helper doctor || true

step "Done. Next:"
echo "  1. Quit Claude with ⌘Q (closing the window is not enough) and open it again."
echo "  2. Back in Cowork, say:  continue Telegram setup"
echo "Map of what was installed:  ~/.claude-telegram/README.md"
