"""Which Telegram connectors this install runs, and what each one may do.

Every entry point reads this: serve.py (started by launch.sh), the bridge and
setup.sh's helper. An optional settings.json beside the install describes the
connectors; without it the install runs the three Claude Desktop connectors it
always has, so an update changes nothing for an existing install.

Standard library only: the bridge and the headers helper import it on every
start and must stay light.
"""
import copy
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
SETTINGS = HERE / "settings.json"
SECURITY = "/usr/bin/security"

READS = ("get_me", "list_chats", "get_chat", "search_messages", "list_messages", "get_message_context",
         "get_message_link", "get_pinned_messages", "list_topics", "list_topic_messages")
_WRITES = ("send_message", "reply_to_message", "edit_message", "pin_message", "unpin_message",
           "schedule_topic_message", "delete_scheduled_message", "send_file")


@dataclass(frozen=True)
class Role:
    exposed: str            # chigwell's own exposure filter (TELEGRAM_EXPOSED_TOOLS)
    tools: tuple            # what serve.py keeps after that filter
    asks: tuple = ()        # writes that must carry an ask rule wherever Claude Code loads them
    chats: str = ""         # "own": only the user's own chat; "file": numeric IDs from a file; "one": exactly one ID
    files: str = "state"    # where file tools may read: "state" (<data>/files) or "outbox" (<install>/outbox)
    whoami: bool = False    # the doctor checks the login with get_me


ROLES = {
    "read": Role("read-only+save_draft",
                 READS + ("get_drafts", "search_contacts", "get_direct_chat_by_contact", "save_draft"),
                 whoami=True),
    "saved": Role("read-only+send_message", ("send_message",), chats="own"),
    "bots": Role("read-only+send_message,press_inline_button,send_file",
                 ("send_message", "list_messages", "get_message_context", "list_inline_buttons",
                  "press_inline_button", "send_file"),
                 chats="file", files="outbox"),
    "send": Role("read-only+" + ",".join(_WRITES), _WRITES + ("get_scheduled_messages",), asks=_WRITES),
    "chat": Role("read-only+send_message,edit_message",
                 ("send_message", "edit_message", "list_messages", "get_message_link"), chats="one"),
    "reader": Role("read-only",
                   READS + ("get_full_chat", "resolve_username", "search_public_chats", "search_global"),
                   whoami=True),
}

CLIENTS = ("desktop", "code")
DEFAULTS = {
    "service": "claude-telegram",
    "state": "~/.local/state/claude-telegram",
    "hosting": "stdio",
    "label": "local.claude-telegram",
    "connectors": {"tg_main": {"role": "read"}, "tg_saved": {"role": "saved"}, "tg_bots": {"role": "bots"}},
}
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
ITEM = re.compile(r"^[A-Za-z0-9_=+/.\-]+$")
_TEXT = ("session", "device", "user_id", "expected_username", "data", "allow_file", "files")
_KEYS = {"role", "clients", "port", *_TEXT}


class SettingsError(Exception):
    pass


@dataclass(frozen=True)
class Connector:
    name: str
    role: Role
    session: str = "session"            # Keychain item holding the Telegram login
    device: str = "Claude Desktop"      # name shown under Telegram → Settings → Devices
    user_id: str = "user_id"            # Keychain item holding the user's own ID (role "saved")
    expected_username: str = ""         # Keychain item the reader account must match (role "reader")
    data: str = ""                      # subfolder of the state folder for files and aliases
    allow_file: str = ""                # chat allowlist file, relative to the install
    files: str = ""                     # file-tool folder, overriding the role's default
    clients: tuple = ("desktop",)
    port: int = 0


@dataclass
class Settings:
    service: str
    state: Path
    hosting: str
    label: str
    connectors: dict
    home: Path = HERE
    raw: dict = field(default_factory=dict)

    @property
    def hosted(self):
        return self.hosting == "http"

    def connector(self, name):
        try:
            return self.connectors[name]
        except KeyError:
            raise SettingsError(f"unknown connector '{name}' (this install has {', '.join(self.connectors)})") from None

    def serves(self, c):
        """Whether this connector runs as a hosted HTTP server."""
        return self.hosted and bool(c.port)

    def url(self, c):
        return f"http://127.0.0.1:{c.port}/mcp"

    def data_dir(self, c):
        return self.state / c.data if c.data else self.state

    def files_dir(self, c):
        if c.files:
            return self._local(c.files)
        return self.home / "outbox" if c.role.files == "outbox" else self.data_dir(c) / "files"

    def allow_path(self, c):
        if c.allow_file:
            return self._local(c.allow_file)
        return self.home / "bots.txt" if c.role.chats == "file" else None

    def token_item(self, c):
        return f"http/{c.name}"

    def _local(self, value):
        p = Path(value).expanduser()
        return p if p.is_absolute() else self.home / p


def load(path=None):
    path = Path(path) if path else SETTINGS
    raw = copy.deepcopy(DEFAULTS)
    if path.exists():
        try:
            given = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as e:
            raise SettingsError(f"{path} is not valid JSON ({e})") from None
        if not isinstance(given, dict):
            raise SettingsError(f"{path} must hold a JSON object")
        raw.update(given)
    return parse(raw, home=path.parent)


def parse(raw, home=HERE):
    hosting = raw.get("hosting")
    if hosting not in ("stdio", "http"):
        raise SettingsError(f"hosting must be 'stdio' or 'http', not {hosting!r}")
    service = raw.get("service")
    if not isinstance(service, str) or not ITEM.match(service):
        raise SettingsError(f"service {service!r} is not a usable Keychain service name")
    label = raw.get("label")
    if not isinstance(label, str) or not re.fullmatch(r"[A-Za-z0-9.\-]+", label):
        raise SettingsError(f"label {label!r} is not a usable launchd label prefix")
    entries = raw.get("connectors")
    if not isinstance(entries, dict) or not entries:
        raise SettingsError("connectors must name at least one connector")
    connectors, ports = {}, {}
    for name, spec in entries.items():
        connectors[name] = _connector(name, spec, hosting, ports)
    return Settings(service=service, state=Path(str(raw.get("state"))).expanduser(), hosting=hosting,
                    label=label, connectors=connectors, home=Path(home), raw=raw)


def _connector(name, spec, hosting, ports):
    def bad(why):
        raise SettingsError(f"connector '{name}': {why}")

    if not _NAME.match(name):
        bad("names are lowercase letters, digits and underscores")
    if not isinstance(spec, dict):
        bad("must be a JSON object")
    unknown = set(spec) - _KEYS
    if unknown:
        bad(f"unknown setting(s) {', '.join(sorted(unknown))}")
    role = spec.get("role")
    if role not in ROLES:
        bad(f"unknown role {role!r} (one of {', '.join(ROLES)})")
    values = {k: spec[k] for k in _TEXT if k in spec}
    for k, v in values.items():
        if not isinstance(v, str):
            bad(f"{k} must be text")
    for k in ("session", "user_id", "expected_username"):
        if values.get(k) and not ITEM.match(values[k]):
            bad(f"{k} {values[k]!r} is not a usable Keychain item name")
    if ROLES[role].chats == "one" and not values.get("allow_file"):
        bad("a one-chat connector needs allow_file")
    clients = spec.get("clients", ["desktop"])
    if not isinstance(clients, list) or not set(clients) <= set(CLIENTS):
        bad(f"clients must be a list drawn from {', '.join(CLIENTS)}")
    port = spec.get("port", 0)
    if port:
        if type(port) is not int or not 1024 <= port <= 65535:
            bad(f"port {port!r} is outside 1024-65535")
        if port in ports:
            bad(f"port {port} is already used by '{ports[port]}'")
        ports[port] = name
    elif hosting == "http" and clients:
        bad("hosting is on, so it needs a port")
    return Connector(name=name, role=ROLES[role], clients=tuple(clients), port=port, **values)


def read_ids(path, one=False):
    """Numeric chat IDs from an allowlist file: one per line, '#' starts a comment.

    Numeric only, because a username can later belong to someone else. A single
    chat (one=True) may be a group, so negative IDs are allowed there.
    """
    path = Path(path)
    pattern = r"-?[0-9]+" if one else r"[0-9]+"
    ids = []
    for line in path.read_text().splitlines():
        value = line.split("#", 1)[0].strip()
        if not value:
            continue
        if not re.fullmatch(pattern, value):
            raise SettingsError(f"{path.name}: '{value}' is not a numeric {'chat' if one else 'bot'} ID")
        ids.append(value)
    if one and len(ids) != 1:
        raise SettingsError(f"{path.name} must list exactly one chat, not {len(ids)}")
    return ids


def kc_read(service, name, timeout=15):
    """One Keychain secret through /usr/bin/security (the program that stored it reads silently).

    The timeout fails closed if a Keychain dialog ever blocks.
    """
    try:
        done = subprocess.run([SECURITY, "find-generic-password", "-s", service, "-a", name, "-w"],
                              capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return None
    value = done.stdout.rstrip("\n")
    return value if done.returncode == 0 and value else None


def kc_write(service, name, value, timeout=15):
    """Store one Keychain secret through /usr/bin/security; True once it reads back.

    The value goes in on stdin (`security -i`), never on a command line. That
    input is split on whitespace and quotes, so name and value must match ITEM.
    """
    if not (ITEM.match(name) and ITEM.match(value)):
        return False
    try:
        subprocess.run([SECURITY, "-i"], capture_output=True, text=True, timeout=timeout,
                       input=f"add-generic-password -U -s {service} -a {name} -l {service} -w {value}\n")
    except subprocess.TimeoutExpired:
        return False
    return kc_read(service, name, timeout) == value
