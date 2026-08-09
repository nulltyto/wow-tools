#!/usr/bin/env sh
# Bootstrap for macOS, Linux, and any POSIX shell (Git Bash, WSL, MSYS).
#
#   ./install.sh                          interactive
#   ./install.sh --harness codex --yes    non-interactive
#
# The installer itself is standard-library Python, so this only has to find an
# interpreter. uv is tried last rather than first: when a usable python is
# already on PATH, downloading a second one to run a stdlib script would be a
# strange thing to do to someone who just cloned a repo.

set -eu

REPO_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$REPO_DIR"

MIN="3.9"

usable() {
    [ -n "${1:-}" ] || return 1
    command -v "$1" >/dev/null 2>&1 || return 1
    "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1
}

for candidate in python3 python; do
    if usable "$candidate"; then
        exec "$candidate" -m wow_tools "$@"
    fi
done

if command -v uv >/dev/null 2>&1; then
    echo "No Python $MIN+ found on PATH; using uv to provide one." >&2
    exec uv run --no-project --python "$MIN" python -m wow_tools "$@"
fi

cat >&2 <<EOF
error: this installer needs Python $MIN or newer, and none was found.

Install one of:
  uv       https://docs.astral.sh/uv/getting-started/installation/
  Python   https://www.python.org/downloads/

Then re-run: $0 $*
EOF
exit 1
