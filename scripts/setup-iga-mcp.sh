#!/usr/bin/env bash
# =============================================================================
# setup-iga-mcp.sh — one-command IgaMCP installer (idempotent, safe to re-run)
#
# Fresh clone → working IgaMCP. Creates the venv, installs the package, and
# registers the server with whichever MCP clients you actually have:
#   • Claude Code  (user scope, via `claude mcp add`)
#   • VS Code      (user-level mcp.json — detected; you are asked first)
#   • Cursor       (user-level mcp.json — detected; you are asked first)
#
# It NEVER overwrites an existing server entry without asking, NEVER touches
# personal data, and supports --dry-run and --yes. The venv + the per-client
# config are the *personal layer* — this script just wires them; nothing it
# writes is committed to the repo.
#
# Usage:
#   scripts/setup-iga-mcp.sh [--dry-run] [--yes] [--venv DIR]
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${IGA_MCP_VENV:-$HOME/.venvs/iga-mcp}"
DRY=0
ASSUME_YES=0

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --yes|-y)  ASSUME_YES=1 ;;
    --venv)    VENV="$2"; shift ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done

say()  { printf '\033[1m==>\033[0m %s\n' "$*"; }
run()  { if [ "$DRY" = 1 ]; then echo "  [dry-run] $*"; else eval "$*"; fi; }
ask()  { # ask "question" -> returns 0 for yes
  [ "$ASSUME_YES" = 1 ] && return 0
  printf '%s [y/N] ' "$1" >&2; read -r a < /dev/tty || return 1
  case "$a" in y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
}

# --- 1. Python venv + editable install -------------------------------------
PY="$VENV/bin/python"
if [ ! -x "$PY" ]; then
  say "Creating venv at $VENV"
  run "python3 -m venv '$VENV'"
else
  say "venv already present: $VENV"
fi
say "Installing iga_mcp (editable) + mcp into the venv"
run "'$VENV/bin/pip' -q install -e '$REPO_ROOT/iga_mcp'"

BIN="$VENV/bin/iga-mcp"
if [ "$DRY" = 0 ] && [ ! -x "$BIN" ]; then
  echo "ERROR: console script not found at $BIN after install" >&2; exit 1
fi
say "IgaMCP entrypoint: $BIN"

# --- 2. Claude Code (user scope) -------------------------------------------
if command -v claude >/dev/null 2>&1; then
  if claude mcp list 2>/dev/null | grep -q '^iga:'; then
    say "Claude Code: 'iga' already registered — skipping"
  elif ask "Register 'iga' with Claude Code at USER scope (all sessions)?"; then
    run "claude mcp remove iga >/dev/null 2>&1 || true"
    run "claude mcp add -s user iga '$BIN'"
    say "Claude Code: registered (restart sessions to connect)"
  fi
else
  say "Claude Code CLI not found — skipping (install it, then re-run)"
fi

# --- 3. VS Code / Cursor (user-level mcp.json) -----------------------------
# Merge-only writer: adds/updates ONLY the "iga" server, preserves the rest.
patch_client() {
  local name="$1" base="$2"
  [ -d "$base" ] || { return 0; }
  # active profile mcp.json, else the top-level User/mcp.json
  local cfg
  cfg="$(/bin/ls -t "$base"/profiles/*/mcp.json "$base"/mcp.json 2>/dev/null | head -1 || true)"
  [ -z "$cfg" ] && cfg="$base/mcp.json"
  say "$name detected — config: $cfg"
  ask "Add/refresh user-level 'iga' MCP for $name?" || { say "$name: skipped"; return 0; }
  if [ "$DRY" = 1 ]; then echo "  [dry-run] merge iga -> $cfg"; return 0; fi
  BIN="$BIN" CFG="$cfg" python3 - <<'PY'
import json, os, pathlib
cfg = pathlib.Path(os.environ["CFG"]); bin_ = os.environ["BIN"]
cfg.parent.mkdir(parents=True, exist_ok=True)
try:
    data = json.loads(cfg.read_text()) if cfg.exists() and cfg.read_text().strip() else {}
except Exception:
    print(f"  ! {cfg} is not clean JSON — not touching it; add 'iga' manually"); raise SystemExit(0)
servers = data.setdefault("servers", {})
servers["iga"] = {"type": "stdio", "command": bin_}
cfg.write_text(json.dumps(data, indent=2) + "\n")
print(f"  wrote 'iga' -> {cfg}")
PY
  say "$name: done (restart $name / 'MCP: Restart' to connect)"
}

case "$(uname -s)" in
  Darwin)
    patch_client "VS Code" "$HOME/Library/Application Support/Code/User"
    patch_client "VS Code Insiders" "$HOME/Library/Application Support/Code - Insiders/User"
    patch_client "Cursor" "$HOME/Library/Application Support/Cursor/User"
    ;;
  Linux)
    patch_client "VS Code" "$HOME/.config/Code/User"
    patch_client "Cursor" "$HOME/.config/Cursor/User"
    ;;
esac

say "Done. IgaMCP wired. Restart your MCP clients to connect."
