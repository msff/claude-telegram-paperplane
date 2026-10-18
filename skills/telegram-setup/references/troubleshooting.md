# Troubleshooting

Match the user's ✗ line or symptom, then give the fix one step at a time. Every command runs in Terminal on their Mac.
Re-running `bash ~/.claude-telegram/setup.sh` is always safe: each step skips what is already done.
Before the kit is installed, use the copy in their folder (`bash ~/Downloads/telegram-setup/setup.sh` or wherever it is).

## During setup

| Symptom | Fix |
|---|---|
| "Claude Desktop is not in Applications" | Install it from claude.ai/download, open it once, run setup again. |
| "git is missing" | Run `xcode-select --install`, finish Apple's installer (a few minutes), run setup again. |
| uv install fails | Usually network: a company proxy or VPN. Try another network, or install uv another way from astral.sh, then run setup again. |
| my.telegram.org says "ERROR" on Create application | A known Telegram quirk. Turn off any VPN, try another browser, make the short name unique (letters and digits, 5–32), or try again later. |
| "api_hash is 32 characters…" keeps repeating | They copied extra text. Copy only the hash value; it is invisible while pasting, so paste once and press Enter. |
| QR code won't scan | The Terminal colours are unusual. Ctrl-C, then `bash ~/.claude-telegram/setup.sh login --phone`. |
| Asks for "Two-step verification password" | Their Telegram cloud password (Settings → Privacy and Security → Two-Step Verification), not the phone code. |
| "FloodWait" or "wait N seconds" | Telegram rate limit: wait that long, then run setup again. |
| "the connector checkout is not at the reviewed commit" | Run `rm -rf ~/.claude-telegram/chigwell`, then setup again. |
| "is not valid JSON" (Desktop config) | Claude → Settings → Developer → Edit Config; fix or empty the file (`{}`), then run setup again. A `.bak-…` copy sits next to it if setup wrote it before. |
| "already has a different connector named tg_…" | They have another Telegram connector by that name. Answering `n` keeps theirs, and setup stops. To use this one, rename or remove theirs in the config. |

## After setup

| Symptom | Fix |
|---|---|
| No `tg_…` tools in Claude | Claude must be quit with ⌘Q, not just closed, and reopened. Then `bash ~/.claude-telegram/setup.sh doctor`. In Claude → Settings → Developer, each `tg_…` entry shows its error. |
| Doctor: "Keychain item … missing" / "login stored" ✗ | `bash ~/.claude-telegram/setup.sh login` |
| Doctor: "Telegram refused the login", or the login was ended under Devices | `bash ~/.claude-telegram/setup.sh login --again` |
| Doctor: "the config changed after Claude Desktop started" | ⌘Q and reopen Claude. Harmless if they already did. |
| `tg_bots` missing | No bots listed: `bash ~/.claude-telegram/setup.sh bots @BotName`, then ⌘Q and reopen. |
| "bots.txt: '…' is not a numeric bot ID" | A line was edited by hand. Fix or delete it in `~/.claude-telegram/bots.txt` (IDs only, `# @name` after). |
| `send_file` refused | The file must sit in `~/.claude-telegram/outbox/` as a real file (not an alias or link). Pass just its name. |
| A Keychain dialog asks to allow access | Shouldn't happen: the kit reads only through macOS's own `security` tool. Click Deny, then `bash ~/.claude-telegram/setup.sh login --again` to rewrite the items. |
| Search finds nothing in a chat they can see | Check the `chat_id` with `get_chat`. If it's a forum and topic IDs changed, refresh the topic map with `list_topics` and rebuild the search skill. |

If none of this fits, ask for the full doctor output. It never prints secrets, only names, counts and ✓/✗ lines.
