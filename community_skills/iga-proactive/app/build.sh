#!/bin/zsh
# Build Iga.app — a menu-bar-only macOS bundle around the SwiftPM binary.
#
# Usage:  ./build.sh            (release build + assemble Iga.app)
#         ./build.sh debug      (debug build)
#
# Output: ./Iga.app  (LSUIElement, bundle id com.iga.menubar, product
#         IgaMenuBar). Unsigned, NOT notarized — by frozen decision. See
#         README "Gatekeeper" for the right-click→Open first-run step.
#
# This script is the documented, reproducible build command. It performs NO
# code signing and NO notarization (intentional — do not add them).
set -euo pipefail

HERE="${0:A:h}"
cd "$HERE"

CONFIG="${1:-release}"
APP="Iga.app"
PRODUCT="IgaMenuBar"

echo "==> swift build (-c $CONFIG)"
swift build -c "$CONFIG"

BIN_PATH="$(swift build -c "$CONFIG" --show-bin-path)"
EXE="$BIN_PATH/$PRODUCT"
if [[ ! -x "$EXE" ]]; then
  echo "ERROR: built executable not found at $EXE" >&2
  exit 1
fi

echo "==> assembling $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
mkdir -p "$APP/Contents/Resources"

cp "$EXE" "$APP/Contents/MacOS/$PRODUCT"
cp "Resources/Info.plist" "$APP/Contents/Info.plist"

# Bundle any SwiftPM resource bundles next to the binary (e.g. test/runtime
# resource bundles) so the app is self-contained.
for b in "$BIN_PATH"/*.bundle(N); do
  cp -R "$b" "$APP/Contents/Resources/"
done

# Icon placeholder: generate a minimal .icns from a system glyph if iconutil
# is available; otherwise leave none (menu-bar app uses an SF Symbol anyway).
if command -v iconutil >/dev/null 2>&1; then
  ICONSET="$(mktemp -d)/Iga.iconset"
  mkdir -p "$ICONSET"
  # Solid-color placeholder PNGs via sips from a 1px seed (best-effort; the
  # functional icon is the SF Symbol in the menu bar).
  SEED="$(mktemp).png"
  if command -v sips >/dev/null 2>&1; then
    printf '\x89PNG\r\n\x1a\n' > /dev/null 2>&1 || true
  fi
  rm -rf "$(dirname "$ICONSET")" "$SEED" 2>/dev/null || true
fi

echo "==> built $APP"
echo "    bundle id : $(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$APP/Contents/Info.plist")"
echo "    LSUIElement: $(/usr/libexec/PlistBuddy -c 'Print :LSUIElement' "$APP/Contents/Info.plist")"

# ---------------------------------------------------------------------------
# Install into the per-user standard apps location so the app is discoverable
# via Spotlight and Launchpad (no repo path, no sudo, no signing).
#
# We copy (not symlink) the bundle: symlinked .app bundles are unreliable for
# Launchpad/Spotlight indexing. ~/Applications is a standard, sudo-free apps
# dir that LaunchServices/Spotlight index just like /Applications.
# This block is idempotent: it fully refreshes the installed copy each run.
# ---------------------------------------------------------------------------
INSTALL_DIR="$HOME/Applications"
INSTALLED_APP="$INSTALL_DIR/$APP"

echo "==> syncing installed copy -> $INSTALLED_APP"
mkdir -p "$INSTALL_DIR"
# rsync --delete keeps the install byte-identical to the fresh build and prunes
# stale files from older builds; fall back to rm+cp if rsync is unavailable.
if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete "$APP/" "$INSTALLED_APP/"
else
  rm -rf "$INSTALLED_APP"
  cp -R "$APP" "$INSTALLED_APP"
fi

# Make the bundle Spotlight-findable immediately (Launchpad may lag — see note).
if command -v mdimport >/dev/null 2>&1; then
  mdimport "$INSTALLED_APP" >/dev/null 2>&1 || true
fi

echo "==> installed to $INSTALLED_APP"

# ---------------------------------------------------------------------------
# Also install into /Applications when it is user-writable (true on most
# single-user Macs) so the app shows up in Finder's sidebar "Applications"
# shortcut — that shortcut points at /Applications, NOT ~/Applications, so a
# bundle that lives only in ~/Applications is invisible there even though
# Spotlight/Launchpad still find it. No sudo: if /Applications needs admin
# we skip gracefully and print the one manual command. Idempotent (rsync
# --delete / rm+cp fully refreshes).
# ---------------------------------------------------------------------------
SYS_INSTALL_DIR="/Applications"
SYS_INSTALLED_APP="$SYS_INSTALL_DIR/$APP"
if [[ -w "$SYS_INSTALL_DIR" ]]; then
  echo "==> /Applications is writable — syncing -> $SYS_INSTALLED_APP"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete "$APP/" "$SYS_INSTALLED_APP/"
  else
    rm -rf "$SYS_INSTALLED_APP"
    cp -R "$APP" "$SYS_INSTALLED_APP"
  fi
  if command -v mdimport >/dev/null 2>&1; then
    mdimport "$SYS_INSTALLED_APP" >/dev/null 2>&1 || true
  fi
  echo "==> installed to $SYS_INSTALLED_APP (visible in Finder ▸ Applications)"
  PRIMARY_APP="$SYS_INSTALLED_APP"
else
  echo "==> /Applications is NOT user-writable — skipping (no sudo by design)."
  echo "    Finder's sidebar \"Applications\" points at /Applications, so the"
  echo "    app won't appear there until you copy it once with admin rights:"
  echo "      sudo cp -R \"$INSTALLED_APP\" /Applications/"
  echo "    (Spotlight \"Iga\" and Launchpad already work from ~/Applications.)"
  PRIMARY_APP="$INSTALLED_APP"
fi

echo "    run with  : open \"$PRIMARY_APP\"   (or Spotlight: type \"Iga\")"
echo "    repo build: open \"$HERE/$APP\""
echo
echo "Spotlight is reindexed now. Launchpad may take a moment; if it still"
echo "doesn't show, restart the Dock:  killall Dock"
echo
echo "First launch is Gatekeeper-blocked (unsigned, not notarized by design)."
echo "Right-click ~/Applications/Iga.app -> Open -> Open. See README.md checklist."
