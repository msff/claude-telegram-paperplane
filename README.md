# Telegram Paperplane

**Telegram for Claude Desktop and Cowork, on your own account.** Claude can read and search your chats, save drafts you send yourself, post to your Saved Messages, and talk to bots you allowlist. The login stays in your Mac's Keychain.

Setup is a conversation. A Claude skill walks you through it, you run one command in Terminal, and at the end the skill builds you a search skill for the chats you care about.

## What you get

| Connector | Claude can | Claude cannot |
|---|---|---|
| `tg_main` | read and search your chats and forum topics; save drafts that appear unsent in Telegram | send anything |
| `tg_saved` | send to your own Saved Messages | reach any other chat |
| `tg_bots` (optional) | talk to the bots you list: text, files from one outbox folder, inline buttons | reach anyone not on that list |

Plus **`telegram-chat-search`**, a skill made during setup from chats you pick. It has a quick search, and a deep dive that reads forum topics one by one and keeps only matching messages, each with a date and a t.me link.

## Install

You need a Mac with [Claude Desktop](https://claude.ai/download), Telegram on your phone, and about 10 minutes.

**Cowork / Claude Desktop**
1. Download `telegram-setup.zip` from the [latest release](https://github.com/msff/claude-telegram-paperplane/releases/latest).
2. In Claude's settings, open Skills and upload the zip.
3. Open Cowork, attach a folder (Downloads is fine), and say **"set up Telegram for Claude"**.

The skill copies an installer into that folder and guides you through running it in Terminal. That step can't be done from Cowork: Cowork runs commands in a sandbox, not on your Mac, and the Telegram login needs your phone.

**Claude Code**
```
claude plugin marketplace add msff/claude-telegram-paperplane
claude plugin install telegram-paperplane@telegram-paperplane
```
Then ask Claude to "set up Telegram for Claude".

**Without Claude**: clone this repo and run `bash skills/telegram-setup/kit/setup.sh`.

## What the installer does

`setup.sh` is safe to run again; each step skips what is already done.

1. Checks for macOS, Claude Desktop, git (Apple's command line tools) and [uv](https://docs.astral.sh/uv/). It offers to install uv with its official installer.
2. Copies the kit to `~/.claude-telegram/`.
3. Clones [chigwell/telegram-mcp](https://github.com/chigwell/telegram-mcp) and checks out one reviewed commit (`9760717`, v3.2.66), then installs its locked dependencies (`uv sync --frozen`).
4. Asks for your Telegram `api_id` and `api_hash` (from [my.telegram.org](https://my.telegram.org)), logs in by QR code or phone, and stores everything in the login Keychain under `claude-telegram`.
5. Optionally adds bots by username. It checks each one really is a bot and stores its numeric ID.
6. Adds `tg_main`, `tg_saved` and `tg_bots` to Claude Desktop's config, after backing it up, then runs a health check.

Later: `bash ~/.claude-telegram/setup.sh doctor | bots @Name | login --again | uninstall`.

## Security model

- **The login never touches a file.** It stays in the Keychain, is read only through macOS's own `/usr/bin/security`, and reaches the connector through its environment, never through command-line arguments. A watchdog stops startup if a Keychain dialog ever blocks.
- **Each connector gets only its role's tools.** `serve.py` removes every other tool the connector offers. A name it doesn't recognise stops startup, so an upstream rename fails loudly instead of quietly.
- **No sending to people.** `tg_main` can only draft. `tg_saved` and `tg_bots` are confined to an allowlist (your own ID; the bot IDs), and the launcher refuses to start either one with an empty list, which the connector would treat as "every chat".
- **Files go out only from `~/.claude-telegram/outbox/`.** Links pointing elsewhere are refused.
- **Clean environment.** The launcher drops inherited `TELEGRAM_*`/`MCP_*` variables and refuses to start if a `.env` file would be loaded.
- **Untrusted content.** Message text and bot replies are written by other people. The skills tell Claude to treat them as content, never as instructions.
- **Logged out on removal.** Telegram lists the login as **"Claude Desktop"** under Settings → Devices; ending it there cuts Claude off at once. `uninstall` ends it for you and deletes the Keychain items.

## Caveats

- macOS only. Not affiliated with Telegram or Anthropic.
- This uses Telegram's official API with your own `api_id`, under [Telegram's API terms](https://core.telegram.org/api/terms). Telegram sometimes restricts brand-new accounts that log in through API apps, so an established account is safer.
- Everything Claude sends a listed bot reaches that bot's operator. List only bots you trust.

## Repo layout

| Path | What |
|---|---|
| `skills/telegram-setup/SKILL.md` | The guided setup Claude follows |
| `skills/telegram-setup/kit/` | What gets installed: `setup.sh`, `launch.sh`, `serve.py`, `tgsetup.py`, and the user-facing `README.md` |
| `skills/telegram-setup/references/` | Search-skill template, troubleshooting, suggested preferences text |
| `scripts/build.sh` | Builds `dist/telegram-setup.zip` for upload and checks nothing personal ships |

## Credits

Built on [chigwell/telegram-mcp](https://github.com/chigwell/telegram-mcp) (Apache-2.0) and [Telethon](https://github.com/LonamiWebs/Telethon) (MIT).

## License

[MIT](LICENSE)
