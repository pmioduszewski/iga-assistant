#!/usr/bin/env bash
# =============================================================================
# setup-iga-mcp.sh — one-command IgaMCP installer (idempotent, safe to re-run)
#
# Fresh clone → working IgaMCP. Creates the venv, installs the package, and
# registers the server with whichever MCP clients you actually have:
#   • Claude Code  (user scope, via `claude mcp add`)
#   • Codex CLI    (user scope, via `codex mcp add`), plus the `iga` admin
#                  skill linked into this clone's .agents/skills
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

# --- 0. Local privacy guard -------------------------------------------------
# git does NOT auto-enable a hooks directory, so a fresh clone runs with NO
# guard until someone reads the README and sets this by hand. That is how a
# private agent-session URL reached the public repo on 2026-08-07. Wire it here
# so "fresh clone → set up" cannot leave the guard off. Cheap and idempotent.
# The server-side check (.github/workflows/privacy-guard.yml) is the backstop
# for anyone who never runs this script at all.
if [ -d "$REPO_ROOT/.githooks" ] && git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  current="$(git -C "$REPO_ROOT" config --get core.hooksPath || true)"
  if [ "$current" != ".githooks" ]; then
    say "Enabling the local privacy guard (core.hooksPath=.githooks)"
    run "git -C '$REPO_ROOT' config core.hooksPath .githooks"
  fi
fi

# --- 1. Python venv + editable install -------------------------------------
PY="$VENV/bin/python"
if [ ! -x "$PY" ]; then
  say "Creating venv at $VENV"
  run "python3 -m venv '$VENV'"
else
  say "venv already present: $VENV"
fi
say "Installing iga_mcp (editable) + mcp into the venv"
# A venv made by `uv venv` ships without pip, so `bin/pip` is not a given.
# Use whichever installer this venv can actually run; bootstrap pip last.
if "$PY" -m pip --version >/dev/null 2>&1; then
  run "'$PY' -m pip -q install -e '$REPO_ROOT/iga_mcp'"
elif command -v uv >/dev/null 2>&1; then
  run "uv pip install -q --python '$PY' -e '$REPO_ROOT/iga_mcp'"
else
  run "'$PY' -m ensurepip -q && '$PY' -m pip -q install -e '$REPO_ROOT/iga_mcp'"
fi

BIN="$VENV/bin/iga-mcp"
if [ "$DRY" = 0 ] && [ ! -x "$BIN" ]; then
  echo "ERROR: console script not found at $BIN after install" >&2; exit 1
fi
say "IgaMCP entrypoint: $BIN"

# --- IgaMemory detection (the warm memory server — a SEPARATE process by
#     design; see iga_mcp/README.md "topology"). Path derived from IGA_HOME
#     (default ~/Iga) — never hardcoded; skipped cleanly if absent. ------------
IGA_HOME_DIR="${IGA_HOME:-$HOME/Iga}"
MEM_BIN=""
for cand in "$IGA_HOME_DIR/mempalace/.venv/bin/mempalace-mcp" \
            "$REPO_ROOT/mempalace/.venv/bin/mempalace-mcp"; do
  [ -x "$cand" ] && { MEM_BIN="$cand"; break; }
done
if [ -n "$MEM_BIN" ]; then
  MEM_PALACE="$(dirname "$(dirname "$(dirname "$MEM_BIN")")")/.mempalace/palace"
  say "MemPalace detected: $MEM_BIN"
else
  say "MemPalace not found under \$IGA_HOME — IgaMemory steps skipped"
fi

# --- 2. Claude Code (user scope): register iga, and IgaMemory if present ----
reg_claude() { # name, then command + args
  local id="$1"; shift
  if ! command -v claude >/dev/null 2>&1; then
    say "Claude Code CLI not found — skipping '$id'"; return 0
  fi
  if claude mcp list 2>/dev/null | grep -q "^$id:"; then
    say "Claude Code: '$id' already registered — skipping"; return 0
  fi
  ask "Register '$id' with Claude Code at USER scope (all sessions)?" \
    || { say "Claude Code: '$id' skipped"; return 0; }
  run "claude mcp remove $id >/dev/null 2>&1 || true"
  run "claude mcp add -s user $id $*"
  say "Claude Code: '$id' registered (restart sessions to connect)"
}
reg_claude iga "'$BIN'"
[ -n "$MEM_BIN" ] && reg_claude IgaMemory "'$MEM_BIN' -- --palace '$MEM_PALACE'"

# --- 2b. Codex CLI (user scope): same two servers, plus the `iga` skill ------
# Codex has no slash command files, so the admin commands ship as a skill
# (`/iga` in the Codex desktop app, `$iga` in Codex CLI).
# Both .agents/ and .codex/ are gitignored here (they hold machine config), so
# the tracked source lives in scripts/codex/ and is symlinked in: repo scope
# only, and `git pull` keeps it current.
reg_codex() { # name, then command + args
  local id="$1"; shift
  if ! command -v codex >/dev/null 2>&1; then
    say "Codex CLI not found, skipping '$id'"; return 0
  fi
  if codex mcp get "$id" >/dev/null 2>&1; then
    say "Codex: '$id' already registered, skipping"; return 0
  fi
  ask "Register '$id' with Codex CLI at USER scope (all sessions)?" \
    || { say "Codex: '$id' skipped"; return 0; }
  run "codex mcp add $id -- $*"
  say "Codex: '$id' registered (restart Codex sessions to connect)"
}
link_codex_skill() {
  command -v codex >/dev/null 2>&1 || return 0
  local src="$REPO_ROOT/scripts/codex/skills/iga" dst="$REPO_ROOT/.agents/skills/iga"
  [ -f "$src/SKILL.md" ] || return 0
  if [ -L "$dst" ] && [ "$(readlink "$dst")" = "$src" ]; then
    say "Codex: 'iga' skill already linked, skipping"; return 0
  fi
  if [ -e "$dst" ] || [ -L "$dst" ]; then
    say "Codex: $dst exists and is not this script's link, leaving it alone"; return 0
  fi
  ask "Link the 'iga' admin skill into this clone for Codex (.agents/skills/iga)?" \
    || { say "Codex: 'iga' skill skipped"; return 0; }
  run "mkdir -p '$REPO_ROOT/.agents/skills' && ln -s '$src' '$dst'"
  say "Codex: 'iga' skill linked (new Codex session in this clone: /iga status in the app, \$iga status in the CLI)"
}
reg_codex iga "'$BIN'"
[ -n "$MEM_BIN" ] && reg_codex IgaMemory "'$MEM_BIN' --palace '$MEM_PALACE'"
link_codex_skill

# --- 3. VS Code / Cursor (user-level mcp.json) -----------------------------
# Merge-only writer: adds/updates ONLY the named server, preserves the rest.
patch_client() {
  local name="$1" base="$2" id="$3" cmd="$4" palace="${5:-}"
  [ -d "$base" ] || return 0
  local cfg
  cfg="$(/bin/ls -t "$base"/profiles/*/mcp.json "$base"/mcp.json 2>/dev/null | head -1 || true)"
  [ -z "$cfg" ] && cfg="$base/mcp.json"
  if [ "$id" = "IgaMemory" ]; then
    say "$name: '$id' is your PERSONAL memory — only add it to coding clients you actually want it in."
    ask "Add user-level 'IgaMemory' to $name? (default: no)" || { say "$name: IgaMemory skipped"; return 0; }
  else
    say "$name detected — config: $cfg"
    ask "Add/refresh user-level '$id' MCP for $name?" || { say "$name: $id skipped"; return 0; }
  fi
  if [ "$DRY" = 1 ]; then echo "  [dry-run] merge $id -> $cfg"; return 0; fi
  ID="$id" CMD="$cmd" PALACE="$palace" CFG="$cfg" python3 - <<'PY'
import json, os, pathlib
cfg = pathlib.Path(os.environ["CFG"]); _id = os.environ["ID"]
cmd = os.environ["CMD"]; palace = os.environ.get("PALACE", "")
cfg.parent.mkdir(parents=True, exist_ok=True)
try:
    data = json.loads(cfg.read_text()) if cfg.exists() and cfg.read_text().strip() else {}
except Exception:
    print(f"  ! {cfg} is not clean JSON — not touching it; add '{_id}' manually"); raise SystemExit(0)
entry = {"type": "stdio", "command": cmd}
if palace:
    entry["args"] = ["--palace", palace]
data.setdefault("servers", {})[_id] = entry
cfg.write_text(json.dumps(data, indent=2) + "\n")
print(f"  wrote '{_id}' -> {cfg}")
PY
  say "$name: '$id' done (restart $name / 'MCP: Restart' to connect)"
}

wire_clients() { # base dir, display name
  patch_client "$2" "$1" iga "$BIN"
  [ -n "$MEM_BIN" ] && patch_client "$2" "$1" IgaMemory "$MEM_BIN" "$MEM_PALACE"
}
case "$(uname -s)" in
  Darwin)
    wire_clients "$HOME/Library/Application Support/Code/User" "VS Code"
    wire_clients "$HOME/Library/Application Support/Code - Insiders/User" "VS Code Insiders"
    wire_clients "$HOME/Library/Application Support/Cursor/User" "Cursor"
    ;;
  Linux)
    wire_clients "$HOME/.config/Code/User" "VS Code"
    wire_clients "$HOME/.config/Cursor/User" "Cursor"
    ;;
esac

say "Done. iga + IgaMemory wired (two servers, by design). Restart your MCP clients to connect."
