#!/usr/bin/env bash
# Validate a staged unit against this checkout without installing or starting it.
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
venv=${BUSY_CAFE_CHECK_VENV:-$repo/backend/.venv-linux}
command -v systemd-analyze >/dev/null
test -x "$venv/bin/python"
test -x "$venv/bin/alembic"
temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT
# Only validation paths change. The committed production candidate is untouched.
# Reject sed replacement metacharacters rather than interpreting caller paths.
case "$repo$venv" in *'&'*|*'|'*|*'\'*) exit 2 ;; esac
sed \
  -e "s|%h/.local/share/busy-cafe/current/backend/.venv|$venv|g" \
  -e "s|%h/.local/share/busy-cafe/current/backend|$repo/backend|g" \
  "$repo/deploy/systemd/busy-cafe-worker.service" \
  > "$temporary/busy-cafe-worker.service"
systemd-analyze --user verify "$temporary/busy-cafe-worker.service"
printf '%s\n' 'PASS staged systemd unit; no installation, enable, or start performed'
