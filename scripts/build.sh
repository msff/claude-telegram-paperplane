#!/bin/bash
# Builds dist/telegram-setup.zip (the skill people upload in Claude's settings → Skills)
# and checks that nothing personal is in the repo or in the zip.
#
#   scripts/build.sh           check, then build
#   scripts/build.sh --check   check only (run by the pre-commit hook)
#
# Checks: shell and Python syntax, the unit tests under tests/, and personal data.
#
# Generic checks: home-folder paths and Telegram chat IDs. Your own names, usernames
# and IDs go in a file outside the repo, one extended regex per line, so the list
# itself is never published:
#   ${PAPERPLANE_PRIVATE_PATTERNS:-~/.config/telegram-paperplane/private-patterns.txt}
set -euo pipefail
cd "$(dirname "$0")/.."
SKILL=skills/telegram-setup
PRIVATE="${PAPERPLANE_PRIVATE_PATTERNS:-$HOME/.config/telegram-paperplane/private-patterns.txt}"
trap 'rm -f dist/telegram-setup.zip.new' EXIT

bash -n "$SKILL/kit/setup.sh"
bash -n "$SKILL/kit/launch.sh"
python3 -c 'import ast, json, sys
for f in sys.argv[1:]:
    text = open(f).read()
    json.loads(text) if f.endswith(".json") else ast.parse(text, f)' \
  "$SKILL"/kit/*.py .claude-plugin/plugin.json .claude-plugin/marketplace.json
tests="$(python3 -B -m unittest discover -s tests 2>&1)" || { echo "$tests"; echo "✗ tests failed" >&2; exit 1; }
echo "✓ tests pass ($(echo "$tests" | grep -o 'Ran [0-9]* tests'))"

scan() {
  python3 - "$PRIVATE" "$@" <<'EOF'
import re, sys, zipfile
from pathlib import Path

private, *targets = sys.argv[1:]
patterns = [r"/Users/[^/\s]+", r"-100\d{10}"]
if Path(private).exists():
    patterns += [l.strip() for l in Path(private).read_text().splitlines() if l.strip() and not l.startswith("#")]
else:
    print(f"! no private pattern file at {private}: only the generic checks ran", file=sys.stderr)
rx = re.compile("|".join(f"(?:{p})" for p in patterns), re.I)

def check(name, data, hits):
    for n, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
        if rx.search(line):
            hits.append(f"{name}:{n}")

hits = []
for target in targets:
    if target.endswith(".zip"):
        with zipfile.ZipFile(target) as z:
            for info in z.infolist():
                if not info.is_dir():
                    check(f"{target}!{info.filename}", z.read(info), hits)
    else:
        check(target, Path(target).read_bytes(), hits)
if hits:
    sys.exit("✗ personal data in: " + ", ".join(hits))
EOF
}

# Every file that would be committed, except this script (it holds the generic patterns).
files=()
while IFS= read -r f; do files+=("$f"); done < <(git ls-files --cached --others --exclude-standard | grep -v '^scripts/build.sh$')
scan "${files[@]}"
echo "✓ no personal data in ${#files[@]} repo files"
[ "${1:-}" = --check ] && exit 0

mkdir -p dist
(cd skills && zip -rqX ../dist/telegram-setup.zip.new telegram-setup -x '*.DS_Store' '*__pycache__*')
scan dist/telegram-setup.zip.new
mv dist/telegram-setup.zip.new dist/telegram-setup.zip
unzip -l dist/telegram-setup.zip | sed -n '4,$p' | grep -v '/$' | grep -v -- '-----' | awk 'NF>=4 {print "  " $1 "  " $4}'
echo "✓ built dist/telegram-setup.zip (version $(python3 -c 'import json; print(json.load(open(".claude-plugin/plugin.json"))["version"])'))"
