#!/bin/bash
# The first step of installing Job Applier on a Mac, run by the Mac app from its Resources folder
# (devops/mac/build_app.sh puts this file, payload.tar.gz and version.txt there).
#
# A new Mac has no Python to run the installer with. This fetches uv -- one pinned version,
# checked against its published sha256 -- and has it install a Python 3.12 for Job Applier alone,
# inside its own folder. Then devops/mac/install.py, from the payload, does the rest.
#
#   JOB_APPLIER_HOME   where it goes (default ~/Library/Application Support/Job Applier)
#   JOB_APPLIER_LABEL  its launchd agents' prefix (default com.jobapplier)
set -uo pipefail

RES="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOME_DIR="${JOB_APPLIER_HOME:-$HOME/Library/Application Support/Job Applier}"
UV_VERSION="0.12.22"
UV_SHA_arm64="5d714de09501a59393ceca78f4bc232a50478729640d251907160299b2a93ddd"
UV_SHA_x86_64="1b8a5b316883df2daf20fb9a446e5b230e01d947d57aba2694977c5ac5a7e98c"

mkdir -p "$HOME_DIR/runtime" || exit 1
STATUS="$HOME_DIR/status.txt"
LOG="$HOME_DIR/install.log"

status() {
  printf '%s\n' "$@" > "$STATUS.tmp" && mv -f "$STATUS.tmp" "$STATUS"
}
say() {
  status "state=running" "step=1" "total=6" "text=$1"
  printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" >> "$LOG"
}
fail() {
  status "state=failed" "error=$1"
  printf '%s  FAILED: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" >> "$LOG"
  exit 1
}

# One install at a time: opening the app twice must not start two.
LOCK="$HOME_DIR/install.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  if kill -0 "$(cat "$LOCK/pid" 2>/dev/null)" 2>/dev/null; then
    exit 3
  fi
  rm -rf "$LOCK" && mkdir "$LOCK" || fail "Could not start: $LOCK is in the way."
fi
echo $$ > "$LOCK/pid"
WORK="$(mktemp -d)"
trap 'rm -rf "$LOCK" "$WORK"' EXIT

printf '\n%s  === %s, from %s ===\n' "$(date '+%Y-%m-%d %H:%M:%S')" \
  "installing Job Applier $(cat "$RES/version.txt" 2>/dev/null)" "$RES" >> "$LOG"
say "Getting Python ready…"

case "$(uname -m)" in
  arm64)  ARCH=aarch64; SHA="$UV_SHA_arm64" ;;
  x86_64) ARCH=x86_64;  SHA="$UV_SHA_x86_64" ;;
  *) fail "Job Applier runs on Macs with Apple or Intel chips, not $(uname -m)." ;;
esac

UV="$HOME_DIR/runtime/uv"
if ! "$UV" --version 2>/dev/null | grep -q "^uv $UV_VERSION "; then
  curl -fsSL --retry 3 --connect-timeout 20 --max-time 600 -o "$WORK/uv.tar.gz" \
    "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$ARCH-apple-darwin.tar.gz" >> "$LOG" 2>&1 \
    || fail "Could not download Python's installer. Check the internet connection, then open Job Applier again."
  [ "$(shasum -a 256 "$WORK/uv.tar.gz" | cut -d' ' -f1)" = "$SHA" ] \
    || fail "The downloaded installer was not the expected file, so nothing was installed."
  tar -xzf "$WORK/uv.tar.gz" -C "$WORK" && mv -f "$WORK/uv-$ARCH-apple-darwin/uv" "$UV" \
    || fail "Could not unpack Python's installer."
fi

export UV_PYTHON_INSTALL_DIR="$HOME_DIR/runtime/python" UV_CACHE_DIR="$HOME_DIR/runtime/cache"
export UV_NO_CONFIG=1 UV_MANAGED_PYTHON=1
"$UV" python install 3.12 >> "$LOG" 2>&1 \
  || fail "Could not install Python. Check the internet connection, then open Job Applier again."
PY="$("$UV" python find 3.12 2>> "$LOG")"
[ -x "$PY" ] || fail "Python was installed but could not be found."

say "Unpacking Job Applier…"
PAYLOAD="$WORK/payload"
mkdir -p "$PAYLOAD" && tar -xzf "$RES/payload.tar.gz" -C "$PAYLOAD" \
  || fail "Could not unpack Job Applier's files. Download the app again."

"$PY" -I "$PAYLOAD/devops/mac/install.py" --payload "$PAYLOAD" --home "$HOME_DIR" --uv "$UV" \
  --version "$(cat "$RES/version.txt")" >> "$LOG" 2>&1
CODE=$?
# install.py says how it ended. If it could not (killed, or Python itself failed), say so here.
if [ "$CODE" -ne 0 ] && ! grep -q '^state=failed' "$STATUS" 2>/dev/null; then
  fail "Setup stopped before it finished. Open Job Applier again to carry on; install.log has the details."
fi
exit "$CODE"
