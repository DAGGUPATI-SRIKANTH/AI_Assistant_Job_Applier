#!/bin/bash
# Build "Job Applier.app" -- the double-click installer and launcher for a Mac -- and a zip of it.
#
#   devops/mac/build_app.sh [REF] [OUT_DIR]
#
# REF is a commit or a tree (default HEAD): the app carries exactly the files git has for it, made
# with `git archive`. Never the working folder, which also holds this person's own resume, details,
# board list and database. OUT_DIR defaults to dist/.
#
# The app is signed ad hoc, not by a registered developer, so the first time it is opened macOS
# asks the person to allow it (System Settings > Privacy & Security > Open Anyway); docs/mac-install.md.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REF="${1:-HEAD}"
OUT="${2:-$ROOT/dist}"
cd "$ROOT"

KIND="$(git cat-file -t "$REF")"
if [ "$KIND" = commit ]; then
  VERSION="$(TZ=UTC git show -s --format=%cd --date=format-local:%Y.%-m.%-d.%H%M "$REF")"
else
  VERSION="$(date -u +%Y.%-m.%-d.%H%M)"          # a tree has no date of its own
fi
BUILD="$(git rev-parse --short "$REF")"
APP="$OUT/Job Applier.app"
ZIP="$OUT/Job-Applier-mac-$VERSION.zip"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

git archive --format=tar.gz -o "$WORK/payload.tar.gz" "$REF"
git show "$REF:devops/mac/install.sh" > "$WORK/install.sh"
git show "$REF:devops/mac/Job Applier.applescript" > "$WORK/app.applescript"

mkdir -p "$OUT"
rm -rf "$APP"
osacompile -o "$APP" "$WORK/app.applescript"
RES="$APP/Contents/Resources"
cp "$WORK/payload.tar.gz" "$RES/payload.tar.gz"
install -m 755 "$WORK/install.sh" "$RES/install.sh"
echo "$VERSION" > "$RES/version.txt"
echo "$BUILD" > "$RES/build.txt"

# The icon, when the tree has one (devops/mac/make_icon.py writes it).
if git cat-file -e "$REF:devops/mac/applet.icns" 2>/dev/null; then
  git show "$REF:devops/mac/applet.icns" > "$RES/applet.icns"
fi

PLIST="$APP/Contents/Info.plist"
plutil -replace CFBundleIdentifier -string "org.jobapplier.app" "$PLIST"
plutil -replace CFBundleName -string "Job Applier" "$PLIST"
plutil -replace CFBundleShortVersionString -string "$VERSION" "$PLIST"
plutil -replace CFBundleVersion -string "$BUILD" "$PLIST"
plutil -replace LSMinimumSystemVersion -string "13.0" "$PLIST"
plutil -replace NSHumanReadableCopyright -string "Job Applier $VERSION ($BUILD)" "$PLIST"

# Every file changed above breaks the seal osacompile put on the bundle; a broken seal on a
# downloaded app reads as "damaged" rather than "unidentified developer". Seal it again, ad hoc.
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"

rm -f "$ZIP"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"
echo "built $APP"
echo "      $ZIP ($(du -h "$ZIP" | cut -f1 | tr -d ' '), version $VERSION, from $BUILD)"
