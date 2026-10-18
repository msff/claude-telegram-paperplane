"""Telegram for Claude Desktop: login, bots, Desktop registration, health check.

Run by setup.sh with the connector's own Python (it has Telethon and qrcode).
Secrets go only to the login Keychain, through /usr/bin/security on stdin, so
they never appear in a process list; nothing here prints them.
"""
import argparse
import asyncio
import getpass
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVICE = "claude-telegram"
DEVICE = "Claude Desktop"
DESKTOP_CONFIG = Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
PIN = "9760717c2bb6a5caae90267b0fc3f7e8a12c95ce"
SECURITY = "/usr/bin/security"
LAUNCHER = HERE / "launch.sh"
BOTS = HERE / "bots.txt"
OUTBOX = HERE / "outbox"
EXPECTED_TOOLS = {"tg_main": 14, "tg_saved": 1, "tg_bots": 6}
SECRETS = ("api_id", "api_hash", "session", "user_id")
_SAFE = re.compile(r"^[A-Za-z0-9_=+/.\-]+$")

API_HELP = """
Telegram needs an "app" registered to your account (free, two minutes):
  1. Open https://my.telegram.org and log in with your phone number
     (the code arrives in the Telegram app).
  2. API development tools -> fill in App title "Claude Desktop", Short name
     "claudedesktop", Platform "Desktop" -> Create application.
  3. Copy "App api_id" and "App api_hash" from that page into the prompts below.
They go straight into your Mac's Keychain. Never paste them into a chat.
"""

BOTS_HEADER = """# Bots the tg_bots connector may talk to: one numeric ID per line, @username after '#'.
# Add with: bash ~/.claude-telegram/setup.sh bots   (it checks that each one is a bot)
# Everything Claude sends here reaches the bot's operator. List only bots you trust.
"""


# ---------- Keychain ----------

def kc_read(name):
    done = subprocess.run([SECURITY, "find-generic-password", "-s", SERVICE, "-a", name, "-w"],
                          capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
    value = done.stdout.rstrip("\n")
    return value if done.returncode == 0 and value else None


def kc_exists(name):
    """Existence only: without -w, security prints attributes, never the secret."""
    return subprocess.run([SECURITY, "find-generic-password", "-s", SERVICE, "-a", name],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          stdin=subprocess.DEVNULL, timeout=10).returncode == 0


def kc_write(name, value):
    # `security -i` splits its input on whitespace and quotes; these values never contain either.
    if not (_SAFE.match(name) and _SAFE.match(value)):
        sys.exit(f"✗ refusing to store '{name}': unexpected characters")
    subprocess.run([SECURITY, "-i"], capture_output=True, text=True, timeout=10,
                   input=f"add-generic-password -U -s {SERVICE} -a {name} -l {SERVICE} -w {value}\n")
    if kc_read(name) != value:
        sys.exit(f"✗ could not store '{name}' in the Keychain")


def kc_delete(name):
    subprocess.run([SECURITY, "delete-generic-password", "-s", SERVICE, "-a", name],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)


# ---------- Telegram ----------

def api_credentials(prompt=True):
    api_id, api_hash = kc_read("api_id"), kc_read("api_hash")
    if api_id and api_hash:
        return int(api_id), api_hash
    if not prompt:
        sys.exit("✗ no Telegram API credentials stored; run: bash ~/.claude-telegram/setup.sh login")
    print(API_HELP)
    while not (api_id := input("api_id (a number): ").strip()).isdigit():
        print("  api_id is digits only, e.g. 1234567")
    while not re.fullmatch(r"[0-9a-f]{32}", api_hash := getpass.getpass(
            "api_hash (32 characters; hidden while you paste, then press Enter): ").strip().lower()):
        print("  api_hash is 32 characters of 0-9 and a-f; paste it again")
    kc_write("api_id", api_id)
    kc_write("api_hash", api_hash)
    print("✓ Stored api_id and api_hash in the Keychain.")
    return int(api_id), api_hash


def client_for(session, api_id, api_hash):
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    return TelegramClient(StringSession(session), api_id, api_hash, device_model=DEVICE)


async def whoami(session, api_id, api_hash):
    """The logged-in user for a stored session, or None if it no longer works."""
    client = client_for(session, api_id, api_hash)
    await client.connect()
    try:
        return await client.get_me() if await client.is_user_authorized() else None
    finally:
        await client.disconnect()


async def end_login(session, api_id, api_hash):
    client = client_for(session, api_id, api_hash)
    await client.connect()
    try:
        return await client.is_user_authorized() and await client.log_out()
    finally:
        if client.is_connected():
            await client.disconnect()


def label(me):
    name = " ".join(p for p in (me.first_name, me.last_name) if p) or "your account"
    return f"{name} (@{me.username})" if me.username else name


def print_qr(url):
    import qrcode
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=1, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    # Terminal's default profile is dark when macOS is in dark mode: invert so the code stays dark-on-light.
    dark = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"],
                          capture_output=True, text=True).stdout.strip() == "Dark"
    qr.print_ascii(invert=dark)


async def interactive_login(client, phone):
    from telethon.errors import SessionPasswordNeededError
    try:
        if phone:
            number = input("Phone number with country code (e.g. +381...): ").strip()
            await client.send_code_request(number)
            code = input("Code from the Telegram app (type it only here): ").strip()
            await client.sign_in(phone=number, code=code)
            return
        qr = await client.qr_login()
        for _ in range(5):
            print("\nOn your phone: Telegram → Settings → Devices → Link Desktop Device, then scan:\n")
            print_qr(qr.url)
            print("(Won't scan? Press Ctrl-C and run: bash ~/.claude-telegram/setup.sh login --phone)")
            try:
                await qr.wait(timeout=60)
                return
            except asyncio.TimeoutError:
                await qr.recreate()
        sys.exit("✗ the QR code was not scanned; run setup.sh again")
    except SessionPasswordNeededError:
        await client.sign_in(password=getpass.getpass("Two-step verification password (hidden): "))


async def cmd_login(args):
    api_id, api_hash = api_credentials()
    old = kc_read("session")
    if old and not args.again:
        me = await whoami(old, api_id, api_hash)
        if me:
            kc_write("user_id", str(me.id))
            print(f"✓ Logged in to Telegram as {label(me)}.")
            return
        print("The stored Telegram login no longer works (ended under Devices?). Starting a new one.")
    client = client_for(None, api_id, api_hash)
    await client.connect()
    try:
        await interactive_login(client, args.phone)
        me = await client.get_me()
        session = client.session.save()
        kc_write("session", session)
        kc_write("user_id", str(me.id))
    except BaseException:
        # A login that could not be stored must not stay behind as an orphan device.
        try:
            if await client.is_user_authorized():
                await client.log_out()
        except Exception:
            pass
        raise
    finally:
        if client.is_connected():
            await client.disconnect()
    check = await whoami(kc_read("session"), api_id, api_hash)
    if not check or check.id != me.id:
        sys.exit("✗ the stored login does not connect; run: bash ~/.claude-telegram/setup.sh login --again")
    if old and old != session and await end_login(old, api_id, api_hash):
        print("Ended the previous Claude Desktop login.")
    print(f"✓ Logged in to Telegram as {label(me)}. It appears as \"{DEVICE}\" under Settings → Devices.")


def bot_ids():
    if not BOTS.exists():
        return []
    ids = []
    for line in BOTS.read_text().splitlines():
        value = line.split("#", 1)[0].strip()
        if value:
            ids.append(value)
    return ids


async def cmd_bots(args):
    names = args.names
    if not names:
        print("\ntg_bots lets Claude talk to bots you choose (send text and files, press their buttons).")
        names = input("Bot usernames, separated by spaces (Enter to skip): ").split()
    if not names:
        print("No bots added. Add some later with: bash ~/.claude-telegram/setup.sh bots")
        return
    api_id, api_hash = api_credentials(prompt=False)
    session = kc_read("session") or sys.exit("✗ not logged in; run: bash ~/.claude-telegram/setup.sh login")
    known = set(bot_ids())
    added = []
    client = client_for(session, api_id, api_hash)
    await client.connect()
    try:
        for raw in names:
            name = raw.strip().lstrip("@")
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", name):
                print(f"✗ '{raw}' is not a Telegram username")
                continue
            try:
                entity = await client.get_entity(name)
            except Exception:
                print(f"✗ @{name}: not found")
                continue
            if not getattr(entity, "bot", False):
                print(f"✗ @{name} is not a bot; tg_bots talks only to bots")
                continue
            if str(entity.id) in known:
                print(f"✓ @{name} is already listed")
                continue
            known.add(str(entity.id))
            added.append(f"{entity.id}   # @{entity.username or name}")
            print(f"✓ @{name} added ({entity.id})")
    finally:
        await client.disconnect()
    if added:
        text = BOTS.read_text() if BOTS.exists() else BOTS_HEADER
        BOTS.write_text(text.rstrip("\n") + "\n" + "\n".join(added) + "\n")
        os.chmod(BOTS, 0o600)


# ---------- Claude Desktop config ----------

def load_config():
    if not DESKTOP_CONFIG.exists():
        return {}
    try:
        return json.loads(DESKTOP_CONFIG.read_text(encoding="utf-8") or "{}")
    except ValueError:
        sys.exit(f"✗ {DESKTOP_CONFIG} is not valid JSON; fix it (Claude → Settings → Developer → Edit Config) and run again")


def save_config(cfg):
    """Back up, then replace atomically. Never prints the config: it may hold other connectors' tokens."""
    DESKTOP_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    if DESKTOP_CONFIG.exists():
        backup = DESKTOP_CONFIG.with_name(f"{DESKTOP_CONFIG.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
        backup.write_bytes(DESKTOP_CONFIG.read_bytes())
        os.chmod(backup, 0o600)
    fd, tmp = tempfile.mkstemp(dir=DESKTOP_CONFIG.parent, prefix=".claude_desktop_config.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, DESKTOP_CONFIG)


def ours(entry):
    return isinstance(entry, dict) and entry.get("command") == str(LAUNCHER)


def cmd_register(args):
    cfg = load_config()
    servers = cfg.setdefault("mcpServers", {})
    want = {"tg_main", "tg_saved"} | ({"tg_bots"} if bot_ids() else set())
    changed = []
    for name in EXPECTED_TOOLS:
        entry = {"command": str(LAUNCHER), "args": [name]}
        if name in want and servers.get(name) != entry:
            if name in servers and not ours(servers[name]):
                answer = input(f"Claude Desktop already has a different connector named '{name}'. Replace it? [y/N] ")
                if not answer.strip().lower().startswith("y"):
                    sys.exit(f"✗ left '{name}' alone; nothing changed")
            servers[name] = entry
            changed.append(f"added {name}")
        elif name not in want and ours(servers.get(name)):
            del servers[name]
            changed.append(f"removed {name} (no bots listed)")
    if changed:
        save_config(cfg)
        print(f"✓ Claude Desktop config: {', '.join(changed)}. Quit Claude (⌘Q) and reopen it to load this.")
    else:
        print(f"✓ Claude Desktop already lists {', '.join(n for n in EXPECTED_TOOLS if n in want)}.")


def cmd_unregister(args):
    cfg = load_config()
    servers = cfg.get("mcpServers", {})
    gone = [n for n in EXPECTED_TOOLS if ours(servers.get(n))]
    for name in gone:
        del servers[name]
    if gone:
        save_config(cfg)
    print(f"✓ Removed from Claude Desktop: {', '.join(gone) or 'nothing to remove'}.")


async def cmd_logout(args):
    session = kc_read("session")
    if session:
        api_id, api_hash = api_credentials(prompt=False)
        ended = await end_login(session, api_id, api_hash)
        print("✓ Ended the Telegram login." if ended else "The Telegram login had already ended.")
    cmd_forget(args)


def cmd_forget(args):
    for name in SECRETS:
        kc_delete(name)
    print(f"✓ Deleted the Keychain items (service \"{SERVICE}\").")


# ---------- Health check ----------

async def probe(name):
    """Start one connector the way Claude Desktop does; return (tool count, get_me record or None)."""
    with tempfile.TemporaryFile() as err:
        proc = await asyncio.create_subprocess_exec(str(LAUNCHER), name, stdin=asyncio.subprocess.PIPE,
                                                    stdout=asyncio.subprocess.PIPE, stderr=err)
        seq = 0

        async def call(method, params=None, notify=False):
            nonlocal seq
            msg = {"jsonrpc": "2.0", "method": method, **({"params": params} if params is not None else {})}
            if not notify:
                seq += 1
                msg["id"] = seq
            proc.stdin.write((json.dumps(msg) + "\n").encode())
            await proc.stdin.drain()
            while not notify:
                line = await asyncio.wait_for(proc.stdout.readline(), 45)
                if not line:
                    raise RuntimeError("exited")
                reply = json.loads(line)
                if reply.get("id") == seq:
                    return reply

        try:
            await call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                      "clientInfo": {"name": "tg-doctor", "version": "1"}})
            await call("notifications/initialized", notify=True)
            tools = (await call("tools/list"))["result"]["tools"]
            me = None
            if name == "tg_main":
                reply = await call("tools/call", {"name": "get_me", "arguments": {}})
                text = "".join(c.get("text", "") for c in reply.get("result", {}).get("content", []))
                try:
                    me = json.loads(text)
                except ValueError:
                    me = {"error": text[:200]}
            return len(tools), me
        except (RuntimeError, asyncio.TimeoutError, KeyError, ValueError) as e:
            err.seek(0)
            tail = [l for l in err.read().decode(errors="replace").splitlines() if l.strip()][-2:]
            raise RuntimeError(" / ".join(tail) or str(e) or type(e).__name__) from None
        finally:
            if proc.returncode is None:
                proc.stdin.close()
                try:
                    await asyncio.wait_for(proc.wait(), 10)
                except asyncio.TimeoutError:
                    proc.kill()


def claude_started():
    """Start time of the running Claude Desktop, or None."""
    out = subprocess.run(["ps", "-axo", "lstart=,comm="], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if line.rstrip().endswith("/Claude.app/Contents/MacOS/Claude"):
            try:
                return datetime.strptime(" ".join(line.split()[:5]), "%a %b %d %H:%M:%S %Y")
            except ValueError:
                return None
    return None


async def cmd_doctor(args):
    problems = 0

    def ok(msg):
        print(f"  ✓ {msg}")

    def bad(msg):
        nonlocal problems
        problems += 1
        print(f"  ✗ {msg}")

    print("Files")
    head = subprocess.run(["git", "-C", str(HERE / "chigwell"), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    ok("connector pinned at reviewed commit") if head == PIN else bad(f"connector checkout is at '{head or 'missing'}', expected {PIN[:7]}: run setup.sh again")
    venv = HERE / "chigwell" / ".venv" / "bin" / "python"
    ok("connector environment") if venv.exists() else bad("connector environment missing: run setup.sh again")
    for f in ("launch.sh", "serve.py"):
        ok(f) if (HERE / f).exists() else bad(f"{f} missing: run setup.sh again from the setup folder")
    leftovers = [p.name for p in OUTBOX.iterdir() if not p.name.startswith(".")] if OUTBOX.exists() else []
    ok("outbox empty") if not leftovers else print(f"  ! outbox still holds {len(leftovers)} file(s): delete them once sent")

    print("Keychain")
    missing = [n for n in SECRETS if not kc_exists(n)]
    ok(f"login stored (service \"{SERVICE}\")") if not missing else bad(f"missing {', '.join(missing)}: run setup.sh login")

    print("Bots")
    try:
        ids = bot_ids()
        bad_ids = [i for i in ids if not i.isdigit()]
        if bad_ids:
            bad(f"bots.txt has non-numeric lines: {', '.join(bad_ids)}")
        else:
            ok(f"{len(ids)} bot(s) listed") if ids else print("  - no bots listed, so tg_bots is off (setup.sh bots adds some)")
    except OSError as e:
        bad(f"bots.txt unreadable: {e}")

    print("Claude Desktop")
    servers = load_config().get("mcpServers", {})
    want = ["tg_main", "tg_saved"] + (["tg_bots"] if bot_ids() else [])
    unregistered = [n for n in want if not ours(servers.get(n))]
    ok(f"config lists {', '.join(want)}") if not unregistered else bad(f"config is missing {', '.join(unregistered)}: run setup.sh again")
    started = claude_started()
    if started is None:
        print("  - Claude Desktop is not running: open it to load the connectors")
    elif DESKTOP_CONFIG.exists() and datetime.fromtimestamp(DESKTOP_CONFIG.stat().st_mtime) > started:
        print("  ! the config changed after Claude Desktop started: quit it with ⌘Q and reopen, if you haven't since")

    print("Connectors (started the way Claude Desktop starts them)")
    for name in want:
        try:
            count, me = await probe(name)
        except RuntimeError as e:
            bad(f"{name} does not start: {e}")
            continue
        if count != EXPECTED_TOOLS[name]:
            bad(f"{name}: {count} tools, expected {EXPECTED_TOOLS[name]}")
        elif me is not None and (me.get("error") or "name" not in me):
            bad(f"{name}: started, but Telegram refused the login ({str(me.get('error', ''))[:80]}): run setup.sh login --again")
        elif me is not None:
            who = me["name"] + (f" (@{me['username']})" if me.get("username") else "")
            ok(f"{name}: {count} tools, logged in as {who}")
        else:
            ok(f"{name}: {count} tool(s)")

    print("\nAll good." if not problems else f"\n{problems} problem(s) above.")
    return problems


def main():
    parser = argparse.ArgumentParser(prog="setup.sh")
    sub = parser.add_subparsers(dest="command", required=True)
    login = sub.add_parser("login")
    login.add_argument("--again", action="store_true", help="make a new login even if the stored one works")
    login.add_argument("--phone", action="store_true", help="log in with phone number + code instead of QR")
    bots = sub.add_parser("bots")
    bots.add_argument("names", nargs="*")
    for name in ("register", "unregister", "logout", "forget", "doctor"):
        sub.add_parser(name)
    args = parser.parse_args()
    handler = globals()[f"cmd_{args.command}"]
    try:
        result = asyncio.run(handler(args)) if asyncio.iscoroutinefunction(handler) else handler(args)
    except KeyboardInterrupt:
        sys.exit("\nStopped.")
    sys.exit(1 if result else 0)


if __name__ == "__main__":
    main()
