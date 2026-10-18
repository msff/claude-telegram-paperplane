#!/bin/bash
# Starts one Telegram connector for Claude Desktop. Installed by setup.sh;
# Claude Desktop runs it once per connector listed in its config.
#
# Usage: launch.sh <tg_main|tg_saved|tg_bots>
#
# The Telegram login lives only in the login Keychain (service "claude-telegram")
# and reaches the server through its environment, which other local users cannot
# read. It never appears in argv or in a config file.
set -euo pipefail

KEY="${1:-}"
SVC="claude-telegram"
DEVICE="Claude Desktop"
HERE="$(cd "$(dirname "$0")" && pwd)"
CONNECTOR="$HERE/chigwell"
STATE="$HOME/.local/state/claude-telegram"

die() { echo "claude-telegram launcher: $*" >&2; exit 1; }

# Tools per connector. TOOLS is chigwell's own exposure filter; ALLOW (applied
# by serve.py) then keeps only the tools each connector's role needs.
READS="get_me,list_chats,get_chat,search_messages,list_messages,get_message_context,get_message_link,get_pinned_messages,list_topics,list_topic_messages"
ALLOW_CHATS=""
case "$KEY" in
  tg_main)  TOOLS="read-only+save_draft"
            ALLOW="$READS,get_drafts,search_contacts,get_direct_chat_by_contact,save_draft" ;;
  tg_saved) TOOLS="read-only+send_message"
            ALLOW="send_message" ;;
  tg_bots)  TOOLS="read-only+send_message,press_inline_button,send_file"
            ALLOW="send_message,list_messages,get_message_context,list_inline_buttons,press_inline_button,send_file" ;;
  *) die "unknown connector '$KEY' (expected tg_main, tg_saved or tg_bots)" ;;
esac

# tg_bots may reach only the bots in bots.txt: one numeric ID per line, '#'
# starts a comment. Numeric IDs only, because a username can later belong to
# someone else. chigwell treats an empty allowlist as "every chat", so an empty
# or malformed list refuses to start.
if [ "$KEY" = tg_bots ]; then
  BOTS_FILE="$HERE/bots.txt"
  [ -f "$BOTS_FILE" ] || die "missing $BOTS_FILE (add bots with: bash $HERE/setup.sh bots)"
  while IFS= read -r line || [ -n "$line" ]; do
    id="${line%%#*}"; id="${id//[[:space:]]/}"
    [ -z "$id" ] && continue
    [[ "$id" =~ ^[0-9]+$ ]] || die "bots.txt: '$id' is not a numeric bot ID"
    ALLOW_CHATS="${ALLOW_CHATS:+$ALLOW_CHATS,}$id"
  done < "$BOTS_FILE"
  [ -n "$ALLOW_CHATS" ] || die "bots.txt lists no bots; refusing to start an unconfined send_message"
fi

# 1. Drop every inherited Telegram, Groq and MCP variable, so nothing from the
#    environment can add a second account or change behavior.
for v in $(compgen -e); do
  case "$v" in TELEGRAM_*|GROQ_*|MCP_*) unset "$v" ;; esac
done

# 2. chigwell calls load_dotenv() at import, searching upward from its package
#    directory. A .env anywhere on that path would re-add what step 1 removed.
d="$CONNECTOR/telegram_mcp"
while :; do
  [ -e "$d/.env" ] && die "refusing to start: $d/.env would be loaded by chigwell"
  [ "$d" = "/" ] && break
  d="$(dirname "$d")"
done

# 3. Read Keychain items through /usr/bin/security only (the program that wrote
#    them reads silently). A watchdog fails closed if a dialog ever blocks.
( sleep 15; kill -TERM $$ 2>/dev/null ) &
WATCHDOG=$!
kc() {
  /usr/bin/security find-generic-password -s "$SVC" -a "$1" -w 2>/dev/null \
    || { kill "$WATCHDOG" 2>/dev/null; die "Keychain item '$SVC/$1' missing; run: bash $HERE/setup.sh login"; }
}
TELEGRAM_API_ID="$(kc api_id)"
TELEGRAM_API_HASH="$(kc api_hash)"
TELEGRAM_SESSION_STRING="$(kc session)"
[ "$KEY" = tg_saved ] && ALLOW_CHATS="$(kc user_id)"
kill "$WATCHDOG" 2>/dev/null || true

# 4. Behavior, set explicitly rather than inherited.
umask 077
# File tools may touch only one directory. tg_bots sends files only from the
# outbox: a file has to be copied there first (symlinks pointing elsewhere are
# refused).
ROOTS="$STATE/files"
[ "$KEY" = tg_bots ] && ROOTS="$HERE/outbox"
mkdir -p "$STATE/tmp" "$ROOTS"
chmod 700 "$STATE" "$STATE/tmp" "$ROOTS"
export TELEGRAM_API_ID TELEGRAM_API_HASH TELEGRAM_SESSION_STRING
export TELEGRAM_EXPOSED_TOOLS="$TOOLS"
export TG_TOOLS_ALLOW="$ALLOW"
export TELEGRAM_SESSION_LOCK=shared
export TELEGRAM_TRANSCRIBE=off
export TELEGRAM_EVENT_FEED=0
export TELEGRAM_ALIASES_FILE="$STATE/aliases.json"
export TELEGRAM_ALLOWED_ROOTS="$ROOTS"
export TELEGRAM_SERVER_ROOTS_ONLY=1
export TELEGRAM_DEVICE_MODEL="$DEVICE"
export MCP_TRANSPORT=stdio
# chigwell's session lock files live under tempfile.gettempdir(); keep them in
# a 0700 per-user directory rather than a shared /tmp.
export TMPDIR="$STATE/tmp"
# A sending connector confined to chosen chats must never start unconfined.
case "$KEY" in
  tg_saved|tg_bots) [ -n "$ALLOW_CHATS" ] || die "$KEY has an empty chat allowlist; refusing to start" ;;
esac
[ -n "$ALLOW_CHATS" ] && export TELEGRAM_ALLOWED_CHAT_IDS="$ALLOW_CHATS"

# 5. Start from a neutral directory and hand over the process.
cd "$STATE"
exec "$CONNECTOR/.venv/bin/python" "$HERE/serve.py"
