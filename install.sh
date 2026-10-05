#!/usr/bin/env bash
# free-web-search installer — copies (or symlinks) this skill into an AI agent's
# skill directory, installs the Python deps, and runs the health check.
#
#   ./install.sh                       # auto-detect an agent skill root
#   ./install.sh --all                 # install into every agent root found
#   ./install.sh --dest ~/.claude/skills
#   ./install.sh --project             # <repo>/.agents/skills/free-web-search
#   ./install.sh --link --deps         # symlink + pip install
#
# No API key, no account, no network install of the skill itself: the only
# network use is pip (optional) and the search engines at runtime.
set -euo pipefail

SKILL_NAME="free-web-search"
SRC_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || PY=python

DESTS=()
MODE=auto          # auto | explicit | project | all
LINK=0
DEPS=0
CHECK=1
DRY=0

usage() {
  sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  cat <<'EOF'

Options
  --dest DIR      install into DIR/free-web-search (repeatable)
  --project [DIR] install into DIR/.agents/skills/free-web-search (default: repo)
  --all           install into every recognised agent skill root that exists
  --link          symlink instead of copy (edit the clone, agents see it live)
  --deps          python -m pip install -r requirements.txt
  --no-check      skip the post-install health check
  --dry-run       print what would happen
  -h, --help      this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dest)    DESTS+=("$2"); shift 2; MODE=explicit ;;
    --project) MODE=project
               if [[ ${2:-} && ${2:-} != --* ]]; then PROJ="$2"; shift 2; else PROJ="$SRC_DIR"; shift; fi ;;
    --all)     MODE=all; shift ;;
    --link)    LINK=1; shift ;;
    --deps)    DEPS=1; shift ;;
    --no-check) CHECK=0; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

# Recognised skill roots, newest convention first. Installers only create the
# ones you ask for; --all uses roots that already exist on this machine.
ROOTS=(
  "$HOME/.agents/skills"            # agentskills.dev convention (Claude, Codex, OpenCode, DSH all read it)
  "$HOME/.dsh/skills"               # DeepSeek Harness
  "$HOME/.claude/skills"            # Claude Code (personal)
  "$HOME/.codex/skills"             # OpenAI Codex CLI
  "$HOME/.config/opencode/skills"   # OpenCode
  "$HOME/.hermes/skills"            # Hermes Agent (category subdirs allowed)
  "$HOME/.cursor/skills"            # Cursor
  "$HOME/.openclaw/skills"          # OpenClaw
)

target_root() {
  local r
  for r in "${ROOTS[@]}"; do
    if [[ -d "$r" ]]; then printf '%s\n' "$r"; return 0; fi
  done
  return 1
}

install_into() {
  local root="$1"
  # Hermes stores skills under a category: ~/.hermes/skills/<category>/<skill>
  case "$root" in
    */.hermes/skills) root="$root/tools" ;;
  esac
  local dest="$root/$SKILL_NAME"
  echo "-> $dest"
  if [[ $DRY -eq 1 ]]; then return 0; fi
  mkdir -p "$root"
  if [[ -e "$dest" || -L "$dest" ]]; then
    rm -rf -- "$dest"
  fi
  if [[ $LINK -eq 1 ]]; then
    ln -s "$SRC_DIR" "$dest"
  else
    mkdir -p "$dest"
    cp -R "$SRC_DIR/SKILL.md" "$dest/"
    cp -R "$SRC_DIR/scripts" "$dest/"
    find "$dest" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
    find "$dest" -name '*.pyc' -delete 2>/dev/null || true
  fi
}

if [[ $MODE == explicit ]]; then
  for d in "${DESTS[@]}"; do install_into "${d%/}"; done
elif [[ $MODE == project ]]; then
  install_into "$PROJ/.agents/skills"
elif [[ $MODE == all ]]; then
  found=0
  for r in "${ROOTS[@]}"; do
    if [[ -d "$r" ]]; then install_into "$r"; found=1; fi
  done
  [[ $found -eq 1 ]] || { echo "no recognised agent skill root found; use --dest DIR" >&2; exit 1; }
else
  if root="$(target_root)"; then
    install_into "$root"
  else
    echo "no recognised agent skill root found; defaulting to ~/.agents/skills" >&2
    install_into "$HOME/.agents/skills"
  fi
fi

if [[ $DEPS -eq 1 && $DRY -eq 0 ]]; then
  echo "-> installing python dependencies"
  "$PY" -m pip install --disable-pip-version-check -r "$SRC_DIR/requirements.txt"
fi

if [[ $CHECK -eq 1 && $DRY -eq 0 ]]; then
  # Verify the installed copy, not the clone, so a broken install is caught here.
  dest="$SRC_DIR"
  if [[ $MODE == explicit && ${#DESTS[@]} -gt 0 ]]; then
    dest="${DESTS[0]%/}/$SKILL_NAME"
  elif [[ $MODE == project ]]; then
    dest="$PROJ/.agents/skills/$SKILL_NAME"
  elif [[ $MODE == all ]]; then
    for r in "${ROOTS[@]}"; do
      if [[ -d "$r" ]]; then
        case "$r" in */.hermes/skills) r="$r/tools" ;; esac
        dest="$r/$SKILL_NAME"; break
      fi
    done
  elif root="$(target_root)"; then
    dest="$root/$SKILL_NAME"
  fi
  [[ -f "$dest/scripts/fws.py" ]] || dest="$SRC_DIR"
  echo "-> health check"
  "$PY" "$dest/scripts/fws.py" check || true
  echo "-> smoke test"
  "$PY" "$dest/scripts/fws.py" search "what is a SearXNG instance" --max-results 3 || true
fi

echo
echo "Installed. Restart the agent (or start a new session) so it re-scans its skill roots."
