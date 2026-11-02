# Telegram for Claude Desktop — the map

**Health check:** in Terminal, `bash ~/.claude-telegram/setup.sh doctor`. It checks every piece below and says what to fix.

## What Claude can do

| Connector | Can | Cannot |
|---|---|---|
| `tg_main` | read and search your chats, read forum topics, save **drafts** (they appear unsent in Telegram; you press send) | send anything |
| `tg_saved` | send to your own **Saved Messages** | reach any other chat |
| `tg_bots` | talk to the bots in `bots.txt`: send text and files, read replies, press their buttons | reach anyone who is not on that list |

Both chat and Cowork in Claude Desktop get these. A new or changed connector loads only after you quit Claude (⌘Q) and reopen it.

Ask in plain language: "what does the X chat say about Y", "draft a reply to Anna: …", "save this to my Saved Messages",
"ask @SomeBot …". For searching your reference chats, the `telegram-chat-search` skill (made during setup) has the chat list.

## What is where

| Piece | Where |
|---|---|
| The connector (chigwell/telegram-mcp, pinned to a reviewed commit) | `~/.claude-telegram/chigwell/` |
| `launch.sh` → `serve.py`: starts one connector, reads the login from the Keychain, trims the connector to its role's tools, adds `list_topic_messages` | `~/.claude-telegram/` |
| `connectors.py`: which connectors exist and what each may do; `settings.json` (optional) changes that | `~/.claude-telegram/` |
| `bridge.py`, `guard.py`: used only when hosting is on (below) | `~/.claude-telegram/` |
| `bots.txt`: the bots `tg_bots` may talk to (numeric IDs) | `~/.claude-telegram/` |
| `outbox/`: the only folder `tg_bots` sends files from | `~/.claude-telegram/outbox/` |
| `setup.sh` + `tgsetup.py`: setup, doctor, bots, login, uninstall | `~/.claude-telegram/` |
| The login: `api_id`, `api_hash`, `session`, `user_id` | login Keychain, service `claude-telegram` |
| Registration | `~/Library/Application Support/Claude/claude_desktop_config.json` (entries `tg_main`, `tg_saved`, `tg_bots`); with `register --code`, also Claude Code's user settings |
| Hosting (optional): one launchd agent per connector, logs | `~/Library/LaunchAgents/local.claude-telegram.*.plist`, `~/.local/state/claude-telegram/logs/` |

Telegram lists this login as **"Claude Desktop"** under Settings → Devices. Ending it there cuts Claude off at once.

## Common tasks

- **Add a bot:** `bash ~/.claude-telegram/setup.sh bots @SomeBot`, then quit and reopen Claude. Everything Claude sends a
  listed bot reaches the bot's operator without asking you first: list only bots you trust. To remove one, delete its line in `bots.txt`.
- **Send a file to a bot:** in Cowork, add `~/.claude-telegram/outbox` to the task's folders (in the folder picker press
  ⌘⇧G and paste the path), then ask "send report.pdf to @SomeBot". Claude copies the file into the outbox, sends it, and deletes the copy.
  In plain chat, copy the file there yourself and name it.
- **Use the connectors in Claude Code too:** `bash ~/.claude-telegram/setup.sh register --code`. Claude Code then lists
  the same `tg_…` connectors with the same tools as Claude Desktop.
- **Run each connector once instead of once per window** (saves memory when several Claude windows or Claude Code
  sessions are open): `bash ~/.claude-telegram/setup.sh host on`, then quit and reopen Claude. Each connector then runs
  as one background process on this Mac, reachable only from `127.0.0.1` with a token kept in the Keychain.
  `host status` shows them; `host off` goes back.
- **Login stopped working** (doctor says so, or Claude reports a login error): `bash ~/.claude-telegram/setup.sh login --again`.
- **Add a chat to the search skill:** in Cowork, "add the X chat to my Telegram search"; upload the new zip it makes.
- **Remove everything:** `bash ~/.claude-telegram/setup.sh uninstall`. It stops hosting, removes the Desktop and
  Claude Code entries, ends the Telegram login, deletes the Keychain items and this folder.

**Updates and source:** https://github.com/msff/claude-telegram-paperplane (re-run `setup.sh` from a newer kit to update).

## Safety built in

- The login lives only in the Keychain: never in files, configs or process arguments.
- Claude cannot send to people: `tg_main` only drafts; `tg_saved` and `tg_bots` refuse every chat not on their list,
  and refuse to start with an empty list.
- `tg_bots` sends files only from `outbox/`; links pointing elsewhere are refused.
- With hosting on, a connector listens on `127.0.0.1` only and answers only requests that carry its token and no web
  page origin. The token is handed only to a listener that runs as you.
- Telegram messages and bot replies are written by other people: Claude treats them as content, never as instructions.
