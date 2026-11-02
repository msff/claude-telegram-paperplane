"""Telegram for Claude: login, bots, registration, hosting, health check.

Run by setup.sh with the connector's own Python (it has Telethon and qrcode).
Secrets go only to the login Keychain, through /usr/bin/security on stdin, so
they never appear in a process list; nothing here prints them. Which
connectors exist, and who may load them, comes from connectors.py.
"""
import argparse
import asyncio
import copy
import getpass
import io
import json
import os
import plistlib
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import bridge
import connectors

HERE = Path(__file__).resolve().parent
DEVICE = "Claude Desktop"
DESKTOP_CONFIG = Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
CODE_CONFIG = Path.home() / ".claude.json"
CODE_SETTINGS = Path.home() / ".claude" / "settings.json"
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
PIN = "9760717c2bb6a5caae90267b0fc3f7e8a12c95ce"
SECURITY = "/usr/bin/security"
LAUNCHER = HERE / "launch.sh"
BOTS = HERE / "bots.txt"
OUTBOX = HERE / "outbox"
SECRETS = ("api_id", "api_hash", "session", "user_id")
FIRST_PORT = 47601
READY_SECONDS = 90      # a hosted server opens its port only after it reaches Telegram
_SAFE = re.compile(r"^[A-Za-z0-9_=+/.\-]+$")
_INIT = {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "tg-doctor", "version": "1"}}

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


@lru_cache(maxsize=None)
def conf():
    try:
        return connectors.load()
    except connectors.SettingsError as e:
        sys.exit(f"✗ {e}")


# ---------- Keychain ----------

def kc_read(name):
    return connectors.kc_read(conf().service, name, timeout=10)


def kc_exists(name):
    """Existence only: without -w, security prints attributes, never the secret."""
    return subprocess.run([SECURITY, "find-generic-password", "-s", conf().service, "-a", name],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          stdin=subprocess.DEVNULL, timeout=10).returncode == 0


def kc_write(name, value):
    # `security -i` splits its input on whitespace and quotes; these values never contain either.
    service = conf().service
    if not (_SAFE.match(name) and _SAFE.match(value)):
        sys.exit(f"✗ refusing to store '{name}': unexpected characters")
    subprocess.run([SECURITY, "-i"], capture_output=True, text=True, timeout=10,
                   input=f"add-generic-password -U -s {service} -a {name} -l {service} -w {value}\n")
    if kc_read(name) != value:
        sys.exit(f"✗ could not store '{name}' in the Keychain")


def kc_delete(name):
    subprocess.run([SECURITY, "delete-generic-password", "-s", conf().service, "-a", name],
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


# ---------- Settings ----------

def save_settings(s, raw):
    """Write the install's settings.json atomically and return the settings it now holds."""
    path = s.home / "settings.json"
    fd, tmp = tempfile.mkstemp(dir=s.home, prefix=".settings.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(raw, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    conf.cache_clear()
    return connectors.load(path)


def startable(s, c):
    """Why this connector cannot start yet, or None. A confined sender needs its allowlist."""
    path = s.allow_path(c)
    if path is None:
        return None
    if not path.exists():
        return f"{path.name} is missing"
    try:
        return None if connectors.read_ids(path, one=c.role.chats == "one") else f"{path.name} lists no chats"
    except connectors.SettingsError as e:
        return str(e)


def wanted(s, client):
    return [c for c in s.connectors.values() if client in c.clients and startable(s, c) is None]


# ---------- Claude Desktop and Claude Code entries ----------

def load_json(path, what):
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8") or "{}")
    except ValueError:
        sys.exit(f"✗ {path} is not valid JSON; fix {what} and run again")


def load_config():
    return load_json(DESKTOP_CONFIG, "it (Claude → Settings → Developer → Edit Config)")


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


def code_servers():
    """Claude Code's user-scope servers. Read only: running sessions rewrite this file, so writes go through `claude mcp`."""
    return load_json(CODE_CONFIG, "it").get("mcpServers", {})


def ask_rules():
    return load_json(CODE_SETTINGS, "it").get("permissions", {}).get("ask", [])


def desktop_entry(c):
    return {"command": str(LAUNCHER), "args": [c.name]}


def code_entry(s, c):
    if s.hosted and c.port:
        return {"type": "http", "url": s.url(c), "headersHelper": f"{shlex.quote(str(LAUNCHER))} --headers"}
    return {"type": "stdio", "command": str(LAUNCHER), "args": [c.name], "env": {}}


def ours(entry):
    """An entry that runs this install's launcher, whether as a stdio command or as an HTTP headers helper."""
    if not isinstance(entry, dict):
        return False
    if entry.get("type") == "http":
        try:
            return shlex.split(entry.get("headersHelper") or "")[:1] == [str(LAUNCHER)]
        except ValueError:
            return False
    return entry.get("command") == str(LAUNCHER)


def same(have, want):
    defaults = {"type": "stdio", "env": {}}
    return isinstance(have, dict) and all(have.get(k, defaults.get(k)) == v for k, v in want.items())


def claude_cli():
    found = shutil.which("claude")
    for p in ([found] if found else []) + [str(Path.home() / ".local" / "bin" / "claude"),
                                           str(Path.home() / ".claude" / "local" / "claude")]:
        if p and os.access(p, os.X_OK):
            return p
    return None


def claude(*args):
    cli = claude_cli()
    if not cli:
        return subprocess.CompletedProcess(args, 127, "", "the claude command is not installed")
    return subprocess.run([cli, *args], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)


def confirm(question):
    if not sys.stdin.isatty():
        return False
    return input(f"{question} [y/N] ").strip().lower().startswith("y")


def sync_code(s, names):
    """Make Claude Code's user entries for our connectors match: `names` registered, other ones of ours removed."""
    current = code_servers()
    changed = []
    for c in s.connectors.values():
        have = current.get(c.name)
        if c.name in names:
            want = code_entry(s, c)
            if same(have, want):
                continue
            if have is not None and not ours(have) and not confirm(
                    f"Claude Code already has a different connector named '{c.name}'. Replace it?"):
                print(f"  ! left Claude Code's '{c.name}' alone")
                continue
            if have is not None:
                claude("mcp", "remove", c.name, "--scope", "user")
            done = claude("mcp", "add-json", c.name, json.dumps(want), "--scope", "user")
            if done.returncode != 0:
                sys.exit(f"✗ Claude Code did not take '{c.name}': {(done.stderr or done.stdout).strip()[:200]}")
            changed.append(f"{c.name} ({want['type']})")
        elif ours(have):
            claude("mcp", "remove", c.name, "--scope", "user")
            changed.append(f"removed {c.name}")
    return changed


def cmd_register(args):
    s = conf()
    if args.code:
        raw = copy.deepcopy(s.raw)
        for spec in raw["connectors"].values():
            clients = spec.setdefault("clients", ["desktop"])
            if "desktop" in clients and "code" not in clients:
                clients.append("code")
        s = save_settings(s, raw)
    cfg = load_config()
    servers = cfg.setdefault("mcpServers", {})
    want = {c.name for c in wanted(s, "desktop")}
    changed = []
    for c in s.connectors.values():
        entry = desktop_entry(c)
        if c.name in want and servers.get(c.name) != entry:
            if c.name in servers and not ours(servers[c.name]):
                if not confirm(f"Claude Desktop already has a different connector named '{c.name}'. Replace it?"):
                    sys.exit(f"✗ left '{c.name}' alone; nothing changed")
            servers[c.name] = entry
            changed.append(f"added {c.name}")
        elif c.name not in want and ours(servers.get(c.name)):
            del servers[c.name]
            changed.append(f"removed {c.name}")
    if changed:
        save_config(cfg)
        print(f"✓ Claude Desktop config: {', '.join(changed)}. Quit Claude (⌘Q) and reopen it to load this.")
    else:
        print(f"✓ Claude Desktop already lists {', '.join(sorted(want)) or 'nothing of ours'}.")
    code = {c.name for c in wanted(s, "code")}
    if code:
        if not claude_cli():
            print("  ! Claude Code is not installed here, so it was not registered")
        else:
            done = sync_code(s, code)
            print(f"✓ Claude Code: {', '.join(done)}." if done else f"✓ Claude Code already lists {', '.join(sorted(code))}.")


def cmd_unregister(args):
    s = conf()
    cfg = load_config()
    servers = cfg.get("mcpServers", {})
    gone = [n for n in s.connectors if ours(servers.get(n))]
    for name in gone:
        del servers[name]
    if gone:
        save_config(cfg)
    print(f"✓ Removed from Claude Desktop: {', '.join(gone) or 'nothing to remove'}.")
    if claude_cli():
        done = sync_code(s, set())
        print(f"✓ Removed from Claude Code: {', '.join(n.replace('removed ', '') for n in done) or 'nothing to remove'}.")


async def cmd_logout(args):
    session = kc_read("session")
    if session:
        api_id, api_hash = api_credentials(prompt=False)
        ended = await end_login(session, api_id, api_hash)
        print("✓ Ended the Telegram login." if ended else "The Telegram login had already ended.")
    cmd_forget(args)


def cmd_forget(args):
    s = conf()
    for name in SECRETS + tuple(s.token_item(c) for c in s.connectors.values()):
        kc_delete(name)
    print(f"✓ Deleted the Keychain items (service \"{s.service}\").")


# ---------- Hosting: one server per connector under launchd ----------

def agent_label(s, c):
    return f"{s.label}.{c.name}"


def agent_path(s, c):
    return LAUNCH_AGENTS / f"{agent_label(s, c)}.plist"


def agent_plist(s, c):
    """The launchd job for one hosted connector. It holds no secret: the server reads the Keychain itself."""
    log = str(s.state / "logs" / f"{c.name}.log")
    return {"Label": agent_label(s, c), "ProgramArguments": [str(LAUNCHER), c.name, "--host"],
            "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 10,
            "WorkingDirectory": str(s.state), "StandardOutPath": log, "StandardErrorPath": log}


def launchctl(*args):
    return subprocess.run(["/bin/launchctl", *args], capture_output=True, text=True, timeout=30)


def agent_loaded(label):
    return launchctl("print", f"gui/{os.getuid()}/{label}").returncode == 0


def port_free(port):
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def pick_port(taken, start=FIRST_PORT):
    for port in range(start, 65536):
        if port not in taken and port_free(port):
            return port
    sys.exit("✗ no free port above 47601")


def answers(s, c, wait=READY_SECONDS):
    """The hosted connector's initialize reply, waiting while it starts; raises bridge.Unreachable."""
    token = kc_read(s.token_item(c))
    probe = bridge.Bridge(c.name, s.url(c), token, out=io.BytesIO(), retry=wait)
    reply = probe.post(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": _INIT}).encode())
    if not isinstance(reply, dict) or "result" not in reply:
        raise bridge.Unreachable(f"answered initialize with {str(reply)[:120]}")
    return reply


def host_on(s, wait=READY_SECONDS):
    raw = copy.deepcopy(s.raw)
    taken = {c.port for c in s.connectors.values() if c.port}
    for spec in raw["connectors"].values():
        if spec.get("clients", ["desktop"]) and not spec.get("port"):
            spec["port"] = pick_port(taken)
            taken.add(spec["port"])
    raw["hosting"] = "http"
    s = save_settings(s, raw)
    hosted = [c for c in s.connectors.values() if c.clients and c.port]
    LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)
    (s.state / "logs").mkdir(parents=True, exist_ok=True)
    os.chmod(s.state / "logs", 0o700)
    for c in hosted:
        why = startable(s, c)
        if why:
            launchctl("bootout", f"gui/{os.getuid()}/{agent_label(s, c)}")
            agent_path(s, c).unlink(missing_ok=True)
            print(f"  - {c.name} not hosted: {why}")
            continue
        if not kc_exists(s.token_item(c)):
            kc_write(s.token_item(c), secrets.token_urlsafe(32))
        agent_path(s, c).write_bytes(plistlib.dumps(agent_plist(s, c)))
        launchctl("bootout", f"gui/{os.getuid()}/{agent_label(s, c)}")
        done = launchctl("bootstrap", f"gui/{os.getuid()}", str(agent_path(s, c)))
        if done.returncode != 0:
            print(f"  ✗ {c.name}: launchd refused the agent ({done.stderr.strip()[:120]})")
    for c in hosted:
        if startable(s, c) is None:
            try:
                answers(s, c, wait)
                print(f"  ✓ {c.name} answers on 127.0.0.1:{c.port}")
            except bridge.Unreachable as e:
                print(f"  ✗ {c.name}: {e} (log: {s.state / 'logs' / (c.name + '.log')})")
    return s


def host_off(s, forget_tokens=False):
    for c in s.connectors.values():
        launchctl("bootout", f"gui/{os.getuid()}/{agent_label(s, c)}")
        agent_path(s, c).unlink(missing_ok=True)
        if forget_tokens:
            kc_delete(s.token_item(c))
    raw = copy.deepcopy(s.raw)
    raw["hosting"] = "stdio"
    return save_settings(s, raw)


def code_names(s):
    """Our connectors Claude Code should list: those whose clients include it, plus any of ours it already lists."""
    current = code_servers()
    return {c.name for c in wanted(s, "code")} | {n for n in s.connectors if ours(current.get(n))}


def cmd_host(args):
    s = conf()
    if args.action == "status":
        print(f"Hosting is {'on' if s.hosted else 'off'} ({s.home / 'settings.json'})")
        for c in s.connectors.values():
            if not (c.clients and c.port):
                continue
            loaded = agent_loaded(agent_label(s, c))
            owned = bridge.listener_owned(c.port)
            print(f"  {'✓' if loaded and owned else '✗'} {c.name}: port {c.port}, agent "
                  f"{'loaded' if loaded else 'not loaded'}, {'listening' if owned else 'not listening'}")
        return 0
    names = code_names(s)
    s = host_on(s) if args.action == "on" else host_off(s, args.forget_tokens)
    if names and claude_cli():
        done = sync_code(s, names)
        if done:
            print(f"✓ Claude Code: {', '.join(done)}")
    print(f"✓ Hosting is {'on' if s.hosted else 'off'}. Quit Claude Desktop (⌘Q) and reopen it to switch over.")
    return 0


# ---------- Health check ----------

def clash_problems(s, desktop, code):
    """Names that Claude Desktop and Claude Code define differently, which makes Code-tab tool lists flip.

    Desktop passes its servers into Code-tab sessions under their plain names,
    and Claude Code keeps one server per name. Two entries agree only when both
    run our launcher with exactly that connector name, or Claude Code reaches
    that connector's hosted port through our headers helper. Only our names
    are checked.
    """
    problems = []
    for name in sorted(set(desktop) & set(code)):
        d, cc = desktop[name], code[name]
        c = s.connectors.get(name)
        if c is None and not (ours(d) or ours(cc)):
            continue
        stdio = {"command": str(LAUNCHER), "args": [name]}
        d_ok = same(d, stdio)
        cc_ok = same(cc, {"type": "stdio", **stdio}) or (
            c is not None and s.hosted and c.port and cc.get("type") == "http" and ours(cc)
            and cc.get("url") == s.url(c))
        if d_ok and cc_ok:
            continue
        legacy = [x for x in (d, cc) if ours(x) and x.get("args", [None])[1:] != []]
        why = "a stale split registration (it still passes desktop or cc)" if legacy else "different servers"
        problems.append(f"{name}: Claude Desktop and Claude Code define it as {why}; "
                        "Code-tab sessions flip between them")
    return problems


def missing_asks(s, code, rules):
    """Write tools of our Claude Code connectors that would run without a prompt."""
    missing = []
    for c in s.connectors.values():
        if not c.role.asks or not ours(code.get(c.name)):
            continue
        covered = {f"mcp__{c.name}", f"mcp__{c.name}__*"} & set(rules)
        missing += [f"mcp__{c.name}__{t}" for t in c.role.asks
                    if not covered and f"mcp__{c.name}__{t}" not in rules]
    return missing


async def probe(s, c):
    """Start one connector the way Claude Desktop does; return (tool count, get_me record, allowlist refuses)."""
    with tempfile.TemporaryFile() as err:
        proc = await asyncio.create_subprocess_exec(str(LAUNCHER), c.name, stdin=asyncio.subprocess.PIPE,
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
                line = await asyncio.wait_for(proc.stdout.readline(), 120 if s.hosted else 45)
                if not line:
                    raise RuntimeError("exited")
                reply = json.loads(line)
                if reply.get("id") == seq:
                    if "error" in reply:
                        raise RuntimeError(str(reply["error"].get("message", ""))[:200])
                    return reply

        def text(reply):
            return "".join(x.get("text", "") for x in reply.get("result", {}).get("content", []))

        try:
            await call("initialize", _INIT)
            await call("notifications/initialized", notify=True)
            tools = {t["name"] for t in (await call("tools/list"))["result"]["tools"]}
            me = refuses = None
            if c.role.whoami and "get_me" in tools:
                body = text(await call("tools/call", {"name": "get_me", "arguments": {}}))
                try:
                    me = json.loads(body)
                except ValueError:
                    me = {"error": body[:200]}
            if c.role.chats and "send_message" in tools:
                # Chat 1 is never on an allowlist: chigwell refuses it locally, before any network call.
                body = text(await call("tools/call", {"name": "send_message", "arguments": {
                    "chat_id": 1, "message": "allowlist check"}}))
                refuses = "TELEGRAM_ALLOWED_CHAT_IDS" in body
            return tools, me, refuses
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


def headers_released(s, c):
    """Whether the headers helper hands Claude Code a token for this connector (never printed)."""
    env = {**os.environ, "CLAUDE_CODE_MCP_SERVER_NAME": c.name, "CLAUDE_CODE_MCP_SERVER_URL": s.url(c)}
    done = subprocess.run([str(LAUNCHER), "--headers"], capture_output=True, text=True, env=env, timeout=30,
                          stdin=subprocess.DEVNULL)
    try:
        return done.returncode == 0 and json.loads(done.stdout).get("Authorization", "").startswith("Bearer ")
    except ValueError:
        return False


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
    s = conf()
    problems = 0

    def ok(msg):
        print(f"  ✓ {msg}")

    def bad(msg):
        nonlocal problems
        problems += 1
        print(f"  ✗ {msg}")

    print(f"Files ({HERE})")
    head = subprocess.run(["git", "-C", str(HERE / "chigwell"), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    ok("connector pinned at reviewed commit") if head == PIN else bad(f"connector checkout is at '{head or 'missing'}', expected {PIN[:7]}: run setup.sh again")
    venv = HERE / "chigwell" / ".venv" / "bin" / "python"
    ok("connector environment") if venv.exists() else bad("connector environment missing: run setup.sh again")
    missing = [f for f in ("launch.sh", "serve.py", "bridge.py", "guard.py", "connectors.py") if not (HERE / f).exists()]
    ok("launcher files") if not missing else bad(f"missing {', '.join(missing)}: run setup.sh again from the setup folder")
    leftovers = [p.name for p in OUTBOX.iterdir() if not p.name.startswith(".")] if OUTBOX.exists() else []
    ok("outbox empty") if not leftovers else print(f"  ! outbox still holds {len(leftovers)} file(s): delete them once sent")

    active = [c for c in s.connectors.values() if c.clients]
    print(f"Keychain (service \"{s.service}\")")
    items = {"api_id", "api_hash"} | {c.session for c in active} | {c.user_id for c in active if c.role.chats == "own"}
    if s.hosted:
        items |= {s.token_item(c) for c in active if c.port}
    gone = sorted(n for n in items if not kc_exists(n))
    ok(f"{len(items)} item(s) present") if not gone else bad(f"missing {', '.join(gone)}: run setup.sh login (tokens: setup.sh host on)")

    print("Allowlists")
    for c in active:
        why = startable(s, c)
        if c.role.chats not in ("file", "one"):
            continue
        if why is None:
            n = len(connectors.read_ids(s.allow_path(c), one=c.role.chats == "one"))
            ok(f"{c.name}: {n} chat(s) in {s.allow_path(c).name}")
        elif c.role.chats == "file" and why.endswith(("is missing", "lists no chats")):
            print(f"  - {c.name} is off: {why} (setup.sh bots adds some)")
        else:
            bad(f"{c.name}: {why}")

    desktop = load_config().get("mcpServers", {})
    code = code_servers()
    print("Claude Desktop")
    for c in wanted(s, "desktop"):
        entry = desktop.get(c.name)
        if same(entry, desktop_entry(c)):
            ok(f"{c.name}")
        elif ours(entry):
            bad(f"{c.name} still passes {entry.get('args', [])[1:]}: run setup.sh register")
        else:
            bad(f"{c.name} is not registered as ours: run setup.sh register")
    started = claude_started()
    if started is None:
        print("  - Claude Desktop is not running: open it to load the connectors")
    elif DESKTOP_CONFIG.exists() and datetime.fromtimestamp(DESKTOP_CONFIG.stat().st_mtime) > started:
        print("  ! the config changed after Claude Desktop started: quit it with ⌘Q and reopen, if you haven't since")

    print("Claude Code")
    want_code = wanted(s, "code")
    for c in want_code:
        entry = code.get(c.name)
        ok(f"{c.name} ({entry.get('type', 'stdio')})") if same(entry, code_entry(s, c)) else bad(
            f"{c.name} is missing or out of date: run setup.sh register")
    stale = [n for n in s.connectors if ours(code.get(n)) and n not in {c.name for c in want_code}]
    for name in stale:
        bad(f"{name} is registered but not meant for Claude Code: run setup.sh register")
    if not want_code and not stale:
        print("  - none of ours (setup.sh register --code adds them)")
    clashes, unasked = clash_problems(s, desktop, code), missing_asks(s, code, ask_rules())
    for p in clashes:
        bad(p)
    for tool in unasked:
        bad(f"{tool} runs without asking: add it to permissions.ask in {CODE_SETTINGS}")
    if not clashes and not unasked:
        ok("Claude Desktop and Claude Code agree on every shared name; every write tool asks")

    if s.hosted:
        print("Hosting (one server per connector on 127.0.0.1)")
        for c in active:
            if not c.port or startable(s, c):
                continue
            if not agent_loaded(agent_label(s, c)):
                bad(f"{c.name}: launchd agent not loaded: run setup.sh host on")
            elif not bridge.listener_owned(c.port):
                bad(f"{c.name}: nothing of yours listens on port {c.port} (log: {s.state / 'logs' / (c.name + '.log')})")
            elif "code" in c.clients and not headers_released(s, c):
                bad(f"{c.name}: the headers helper gives Claude Code no token")
            else:
                ok(f"{c.name}: port {c.port}")

    print("Connectors (started the way Claude Desktop starts them)")
    for c in active:
        if startable(s, c):
            continue
        try:
            tools, me, refuses = await probe(s, c)
        except RuntimeError as e:
            bad(f"{c.name} does not start: {e}")
            continue
        if tools != set(c.role.tools):
            bad(f"{c.name}: {len(tools)} tools, expected {len(c.role.tools)}")
        elif me is not None and (me.get("error") or "name" not in me):
            bad(f"{c.name}: started, but Telegram refused the login ({str(me.get('error', ''))[:80]}): run setup.sh login --again")
        elif refuses is False:
            bad(f"{c.name}: its chat allowlist is not enforced")
        else:
            who = f", logged in as {me['name']}" + (f" (@{me['username']})" if me.get("username") else "") if me else ""
            held = ", allowlist holds" if refuses else ""
            ok(f"{c.name}: {len(tools)} tool(s){who}{held}")

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
    register = sub.add_parser("register")
    register.add_argument("--code", action="store_true", help="also register the connectors in Claude Code")
    host = sub.add_parser("host")
    host.add_argument("action", choices=("on", "off", "status"))
    host.add_argument("--forget-tokens", action="store_true", help="with off: also delete the hosting tokens")
    for name in ("unregister", "logout", "forget", "doctor"):
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
