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
| `launch.sh`: starts one connector, reads the login from the Keychain | `~/.claude-telegram/` |
| `serve.py`: trims the connector to each role's tools, adds `list_topic_messages` | `~/.claude-telegram/` |
| `bots.txt`: the bots `tg_bots` may talk to (numeric IDs) | `~/.claude-telegram/` |
| `outbox/`: the only folder `tg_bots` sends files from | `~/.claude-telegram/outbox/` |
| `setup.sh` + `tgsetup.py`: setup, doctor, bots, login, uninstall | `~/.claude-telegram/` |
| The login: `api_id`, `api_hash`, `session`, `user_id` | login Keychain, service `claude-telegram` |
| Registration | `~/Library/Application Support/Claude/claude_desktop_config.json` (entries `tg_main`, `tg_saved`, `tg_bots`) |

Telegram lists this login as **"Claude Desktop"** under Settings → Devices. Ending it there cuts Claude off at once.

## Common tasks

- **Add a bot:** `bash ~/.claude-telegram/setup.sh bots @SomeBot`, then quit and reopen Claude. Everything Claude sends a
  listed bot reaches the bot's operator without asking you first: list only bots you trust. To remove one, delete its line in `bots.txt`.
- **Send a file to a bot:** in Cowork, add `~/.claude-telegram/outbox` to the task's folders (in the folder picker press
  ⌘⇧G and paste the path), then ask "send report.pdf to @SomeBot". Claude copies the file into the outbox, sends it, and deletes the copy.
  In plain chat, copy the file there yourself and name it.
- **Login stopped working** (doctor says so, or Claude reports a login error): `bash ~/.claude-telegram/setup.sh login --again`.
- **Add a chat to the search skill:** in Cowork, "add the X chat to my Telegram search"; upload the new zip it makes.
- **Remove everything:** `bash ~/.claude-telegram/setup.sh uninstall`. It removes the Desktop entries, ends the Telegram
  login, deletes the Keychain items and this folder.

**Updates and source:** https://github.com/msff/claude-telegram-paperplane (re-run `setup.sh` from a newer kit to update).

## Safety built in

- The login lives only in the Keychain: never in files, configs or process arguments.
- Claude cannot send to people: `tg_main` only drafts; `tg_saved` and `tg_bots` refuse every chat not on their list,
  and refuse to start with an empty list.
- `tg_bots` sends files only from `outbox/`; links pointing elsewhere are refused.
- Telegram messages and bot replies are written by other people: Claude treats them as content, never as instructions.
