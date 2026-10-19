#!/bin/bash
# The exact packages a Mac install gets (devops/mac/install.py), one list per kind of Mac chip:
# the versions this project's own .venv runs every day, every file checked by its sha256, and
# only ready-built wheels -- a new Mac has no compiler, and a source build would stop the install
# or ask for Xcode's tools. dkimpy is the one exception: pure Python, published only as source.
#
#   UV=/path/to/uv devops/mac/make_locks.sh
#
# Run it after changing requirements.txt or upgrading .venv, then commit both lock files.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
UV="${UV:-uv}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

"$ROOT/.venv/bin/python" -m pip freeze --exclude-editable | grep -v ' @ ' > "$TMP/venv.txt"

for arch in arm64 x86_64; do
  if [ "$arch" = arm64 ]; then
    platform=aarch64-apple-darwin
    cp "$TMP/venv.txt" "$TMP/constraints.txt"
    note=""
  else
    platform=x86_64-apple-darwin
    # cryptography stopped publishing Intel Mac wheels at 49.0.0 (June 2026).
    grep -v -i '^cryptography==' "$TMP/venv.txt" > "$TMP/constraints.txt"
    echo 'cryptography<49' >> "$TMP/constraints.txt"
    note="# cryptography stays below 49 here: its Intel Mac wheels stopped at 48.0.1 (June 2026)."
  fi
  "$UV" pip compile "$ROOT/requirements.txt" -c "$TMP/constraints.txt" --python-version 3.12 \
    --python-platform "$platform" --only-binary :all: --no-binary dkimpy --generate-hashes \
    --no-header --no-annotate --quiet -o "$TMP/lock.txt"
  {
    echo "# The packages a Mac with an $arch chip gets, made by devops/mac/make_locks.sh from"
    echo "# requirements.txt and the versions in this project's .venv. Installed with --require-hashes."
    [ -n "$note" ] && echo "$note"
    cat "$TMP/lock.txt"
  } > "$ROOT/devops/mac/requirements-$arch.lock"
  echo "devops/mac/requirements-$arch.lock: $(grep -c '^[a-z]' "$ROOT/devops/mac/requirements-$arch.lock") packages"
done
