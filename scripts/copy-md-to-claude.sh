#!/usr/bin/env bash
# Copy Markdown outputs from the Engineering Hub workspace into a Claude Code folder.
#
# Chat-friendly — exact paths are optional. Prefer project + recency filters:
#   "copy the most recent LVT outputs to Claude"
#     →  ./scripts/copy-md-to-claude.sh --project LVT --recent 5
#     →  ./scripts/copy-md-to-claude.sh -q "most recent LVT"
#     →  ./scripts/copy-md-to-claude.sh LVT
#
# Named projects and destination folders live in a YAML config (optional):
#   config/copy-md-to-claude.example.yaml  → copy to one of the search paths below
#
# Defaults (override with config, env, or flags):
#   SOURCE: ~/org-roam/engineering-hub/outputs
#   DEST:   ~/Claude/engineering-hub-outputs

set -euo pipefail

SCRIPT_NAME="$(basename "$0")"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Prefer project venv Python (PyYAML), then python3.
if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
  PYTHON="$REPO_ROOT/.venv/bin/python"
else
  PYTHON="${PYTHON:-python3}"
fi

DEFAULT_SOURCE="${EH_OUTPUTS_DIR:-$HOME/org-roam/engineering-hub/outputs}"
DEFAULT_DEST="${EH_CLAUDE_DEST:-$HOME/Claude/engineering-hub-outputs}"
DEFAULT_RECENT="${EH_COPY_RECENT:-5}"

SOURCE_DIR=""
DEST_DIR=""
MODE="auto"          # auto | list | all | list-projects | list-destinations
DRY_RUN=0
FLAT=0
FORCE=0
VERBOSE=1

PROJECT=""           # resolved project key or free-text match token
CONTAINS=""
SUBDIR=""
RECENT=""
DAYS=""
PHRASE=""
DEST_ALIAS=""        # named destination from config (--dest / phrase)
CONFIG_PATH="${EH_COPY_CONFIG:-}"
CONFIG_LOADED=""

# Populated from config when a named project resolves:
PROJECT_MATCHES=""   # newline-separated filename needles (OR)
PROJECT_PATHS=""     # newline-separated subdirs under source
FROM_CONFIG_PROJECT=0

usage() {
  cat <<EOF
${SCRIPT_NAME} — copy Engineering Hub Markdown outputs to a Claude Code folder

Fuzzy / chat-friendly usage (no exact path needed):
  ${SCRIPT_NAME} LVT
  ${SCRIPT_NAME} --project LVT --recent 5
  ${SCRIPT_NAME} --project LVT --dest shared
  ${SCRIPT_NAME} -q "most recent LVT outputs to shared"
  ${SCRIPT_NAME} --list --project LVT --recent 10

Config (named projects + destination folders):
  ${SCRIPT_NAME} --list-projects
  ${SCRIPT_NAME} --list-destinations
  ${SCRIPT_NAME} --config PATH ...
  Copy example:  cp ${REPO_ROOT}/config/copy-md-to-claude.example.yaml \\
                   ~/org-roam/engineering-hub/copy-md-to-claude.yaml

Exact path usage (still supported):
  ${SCRIPT_NAME} docs/project-LVT-draft-an-executive-summary-of.md

Options:
  --config PATH    YAML config (else auto-discover; see below)
  --from DIR       Source outputs root
  --to DIR         Destination directory (absolute/relative path)
  --dest NAME      Named destination from config (e.g. shared, claude)
  -p, --project X  Project key, alias, or filename substring
  -c, --contains X Extra filename substring filter
  -s, --subdir DIR Limit to outputs/DIR (docs, research, ...)
  -n, --recent N   Copy/list the N most recently modified matches
  -d, --days N     Only files modified in the last N days
  -q, --phrase STR Parse a short English request
  --list [DIR]     List matches instead of copying
  --all [DIR]      Copy all matches
  --list-projects  Show projects from config
  --list-destinations  Show destination aliases from config
  --flat           Place files directly in DEST (no relative subdirs)
  --force          Overwrite existing destination files
  --dry-run        Print actions without writing
  --quiet          Less chatter
  -h, --help       Show this help

Config search order (first file found wins):
  1. --config / EH_COPY_CONFIG
  2. ./copy-md-to-claude.yaml
  3. <repo>/config/copy-md-to-claude.yaml
  4. ~/org-roam/engineering-hub/copy-md-to-claude.yaml
  5. ~/.config/engineering-hub/copy-md-to-claude.yaml

Environment:
  EH_OUTPUTS_DIR   Default source root
  EH_CLAUDE_DEST   Default destination root
  EH_COPY_RECENT   Default --recent when filtering
  EH_COPY_CONFIG   Path to YAML config

Exit codes:
  0 success
  1 usage / resolution error
  2 nothing copied / no matches
EOF
}

logv() {
  if (( VERBOSE )); then
    echo "$*" >&2
  fi
}

expand_path() {
  local p="$1"
  if [[ "$p" == "~" ]]; then
    p="$HOME"
  elif [[ "$p" == "~/"* ]]; then
    p="$HOME/${p#~/}"
  fi
  if [[ -e "$p" ]] && command -v realpath >/dev/null 2>&1; then
    realpath "$p" 2>/dev/null || printf '%s\n' "$p"
  else
    printf '%s\n' "$p"
  fi
}

tolower() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]'
}

is_markdown() {
  local f="$1"
  local base ext
  base="${f##*/}"
  case "$base" in
    *.*) ext="${base##*.}" ;;
    *) return 1 ;;
  esac
  case "$(tolower "$ext")" in
    md|markdown) return 0 ;;
    *) return 1 ;;
  esac
}

strip_outputs_prefix() {
  local p="$1"
  p="${p#/}"
  case "$p" in
    outputs/*) printf '%s\n' "${p#outputs/}" ;;
    *) printf '%s\n' "$p" ;;
  esac
}

file_mtime() {
  local f="$1"
  if stat -f '%m' "$f" >/dev/null 2>&1; then
    stat -f '%m' "$f"
  else
    stat -c '%Y' "$f"
  fi
}

format_mtime() {
  local epoch="$1"
  if date -r "$epoch" '+%Y-%m-%d %H:%M' >/dev/null 2>&1; then
    date -r "$epoch" '+%Y-%m-%d %H:%M'
  else
    date -d "@$epoch" '+%Y-%m-%d %H:%M' 2>/dev/null || printf '%s' "$epoch"
  fi
}

looks_like_path_arg() {
  local arg="$1"
  case "$arg" in
    /*|~/*|./*|../*|*/*|*.md|*.markdown|*.MD) return 0 ;;
    *) return 1 ;;
  esac
}

# ── config discovery + Python YAML helper ─────────────────────────────

discover_config() {
  local c
  if [[ -n "$CONFIG_PATH" ]]; then
    expand_path "$CONFIG_PATH"
    return
  fi
  for c in \
    "$(pwd)/copy-md-to-claude.yaml" \
    "$REPO_ROOT/config/copy-md-to-claude.yaml" \
    "$HOME/org-roam/engineering-hub/copy-md-to-claude.yaml" \
    "$HOME/.config/engineering-hub/copy-md-to-claude.yaml"
  do
    if [[ -f "$c" ]]; then
      expand_path "$c"
      return
    fi
  done
  printf '\n'
}

config_py() {
  # Usage: config_py <command> [args...]
  # Commands talk to stdout as KEY=value lines (safe for eval) or plain text lists.
  local cfg="${CONFIG_LOADED:-}"
  "$PYTHON" - "$cfg" "$@" <<'PY'
import os, sys

try:
    import yaml
except ImportError:
    sys.stderr.write(
        "error: PyYAML required for config support (pip install pyyaml / use .venv)\n"
    )
    sys.exit(1)

cfg_path = sys.argv[1] or ""
cmd = sys.argv[2] if len(sys.argv) > 2 else ""
args = sys.argv[3:]

def load():
    if not cfg_path:
        return {}
    with open(cfg_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        sys.stderr.write(f"error: config root must be a mapping: {cfg_path}\n")
        sys.exit(1)
    return data

def expand(p):
    if p is None:
        return ""
    return os.path.expanduser(str(p).strip())

def as_list(val):
    if val is None:
        return []
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    s = str(val).strip()
    return [s] if s else []

def shell_escape(s):
    return "'" + str(s).replace("'", "'\"'\"'") + "'"

def emit(**kwargs):
    for k, v in kwargs.items():
        if v is None:
            v = ""
        # Keep values single-line so bash can `eval` each KEY=value row.
        v = str(v).replace("\n", ",").replace("\r", "")
        print(f"{k}={shell_escape(v)}")

def projects(data):
    raw = data.get("projects") or {}
    return raw if isinstance(raw, dict) else {}

def destinations(data):
    raw = data.get("destinations") or {}
    return raw if isinstance(raw, dict) else {}

def normalize_project_entry(key, entry):
    if entry is None:
        entry = {}
    if not isinstance(entry, dict):
        # allow: LVT: "LVT_alert"  or LVT: [LVT, ...]
        if isinstance(entry, list):
            return {
                "aliases": [],
                "match": [str(x) for x in entry],
                "paths": [],
                "dest": "",
                "recent": "",
                "source_dir": "",
            }
        return {
            "aliases": [],
            "match": [str(entry)],
            "paths": [],
            "dest": "",
            "recent": "",
            "source_dir": "",
        }
    match = as_list(entry.get("match"))
    if not match:
        match = [str(key)]
    return {
        "aliases": as_list(entry.get("aliases")),
        "match": match,
        "paths": as_list(entry.get("paths")),
        "dest": str(entry.get("dest") or "").strip(),
        "recent": str(entry.get("recent") if entry.get("recent") is not None else "").strip(),
        "source_dir": str(entry.get("source_dir") or "").strip(),
    }

def find_project(data, token):
    """Return (key, entry) for key/alias match; case-insensitive; longest alias wins."""
    token_l = (token or "").strip().lower()
    if not token_l:
        return None, None
    best = None  # (score, key, entry)
    for key, raw in projects(data).items():
        entry = normalize_project_entry(key, raw)
        candidates = [str(key)] + entry["aliases"] + entry["match"]
        for c in candidates:
            cl = c.lower()
            if token_l == cl:
                score = 1000 + len(cl)
            elif token_l in cl or cl in token_l:
                score = len(cl)
            else:
                continue
            if best is None or score > best[0]:
                best = (score, str(key), entry)
    if best:
        return best[1], best[2]
    return None, None

def find_project_in_text(data, text):
    text_l = (text or "").lower()
    best = None
    for key, raw in projects(data).items():
        entry = normalize_project_entry(key, raw)
        for c in [str(key)] + entry["aliases"] + entry["match"]:
            cl = c.lower()
            if not cl:
                continue
            if cl in text_l:
                score = len(cl)
                if best is None or score > best[0]:
                    best = (score, str(key), entry)
    if best:
        return best[1], best[2]
    return None, None

def resolve_dest(data, name):
    name = (name or "").strip()
    if not name:
        return ""
    dests = destinations(data)
    # exact then case-insensitive
    if name in dests:
        return expand(dests[name])
    for k, v in dests.items():
        if k.lower() == name.lower():
            return expand(v)
    # allow path-like values directly
    if name.startswith("~") or name.startswith("/") or "/" in name:
        return expand(name)
    return ""

def find_dest_in_text(data, text):
    text_l = (text or "").lower()
    best = None
    for k in destinations(data):
        kl = k.lower()
        if kl and kl in text_l:
            score = len(kl)
            if best is None or score > best[0]:
                best = (score, k)
    # also recognize "claude shared directory" / "shared directory"
    if "shared" in text_l and destinations(data):
        for cand in ("shared", "claude"):
            if cand in destinations(data):
                score = len(cand)
                if best is None or score > best[0]:
                    best = (score, cand)
    return best[1] if best else ""

data = load()

if cmd == "defaults":
    emit(
        CFG_SOURCE=expand(data.get("source_dir") or ""),
        CFG_DEST=expand(data.get("dest_dir") or ""),
        CFG_RECENT=str(data.get("recent") if data.get("recent") is not None else ""),
    )
elif cmd == "list-projects":
    if not cfg_path:
        print("(no config file found)")
        sys.exit(0)
    print(f"config: {cfg_path}")
    if not projects(data):
        print("(no projects defined)")
        sys.exit(0)
    for key, raw in projects(data).items():
        e = normalize_project_entry(key, raw)
        aliases = ", ".join(e["aliases"]) or "-"
        match = ", ".join(e["match"])
        paths = ", ".join(e["paths"]) or "(all)"
        dest = e["dest"] or "-"
        recent = e["recent"] or "-"
        print(f"  {key}")
        print(f"    aliases: {aliases}")
        print(f"    match:   {match}")
        print(f"    paths:   {paths}")
        print(f"    dest:    {dest}")
        print(f"    recent:  {recent}")
elif cmd == "list-destinations":
    if not cfg_path:
        print("(no config file found)")
        sys.exit(0)
    print(f"config: {cfg_path}")
    dests = destinations(data)
    if not dests:
        print("(no destinations defined)")
        sys.exit(0)
    for k, v in dests.items():
        print(f"  {k}: {expand(v)}")
elif cmd == "resolve-project":
    token = args[0] if args else ""
    key, entry = find_project(data, token)
    if not key:
        emit(FOUND=0)
        sys.exit(0)
    emit(
        FOUND=1,
        PROJECT_KEY=key,
        PROJECT_MATCHES=",".join(entry["match"]),
        PROJECT_PATHS=",".join(entry["paths"]),
        PROJECT_DEST=entry["dest"],
        PROJECT_RECENT=entry["recent"],
        PROJECT_SOURCE=expand(entry["source_dir"]) if entry["source_dir"] else "",
    )
elif cmd == "resolve-project-in-text":
    text = args[0] if args else ""
    key, entry = find_project_in_text(data, text)
    if not key:
        emit(FOUND=0)
        sys.exit(0)
    emit(
        FOUND=1,
        PROJECT_KEY=key,
        PROJECT_MATCHES=",".join(entry["match"]),
        PROJECT_PATHS=",".join(entry["paths"]),
        PROJECT_DEST=entry["dest"],
        PROJECT_RECENT=entry["recent"],
        PROJECT_SOURCE=expand(entry["source_dir"]) if entry["source_dir"] else "",
    )
elif cmd == "resolve-dest":
    name = args[0] if args else ""
    path = resolve_dest(data, name)
    emit(FOUND=1 if path else 0, DEST_PATH=path)
elif cmd == "resolve-dest-in-text":
    text = args[0] if args else ""
    name = find_dest_in_text(data, text)
    path = resolve_dest(data, name) if name else ""
    emit(FOUND=1 if path else 0, DEST_NAME=name, DEST_PATH=path)
elif cmd == "project-names":
    # newline list of keys + aliases for stopword exemption / phrase parsing
    names = []
    for key, raw in projects(data).items():
        e = normalize_project_entry(key, raw)
        names.append(str(key))
        names.extend(e["aliases"])
        names.extend(e["match"])
    for n in names:
        if n.strip():
            print(n.strip())
elif cmd == "dest-names":
    for k in destinations(data):
        print(k)
else:
    sys.stderr.write(f"error: unknown config command: {cmd}\n")
    sys.exit(1)
PY
}

apply_config_defaults() {
  CONFIG_LOADED="$(discover_config)"
  if [[ -z "$CONFIG_LOADED" ]]; then
    return 0
  fi
  if [[ ! -f "$CONFIG_LOADED" ]]; then
    echo "error: config not found: $CONFIG_LOADED" >&2
    exit 1
  fi
  logv "config: $CONFIG_LOADED"
  local line cfg_source="" cfg_dest="" cfg_recent=""
  while IFS= read -r line; do
    case "$line" in
      CFG_SOURCE=*) eval "$line"; cfg_source="$CFG_SOURCE" ;;
      CFG_DEST=*) eval "$line"; cfg_dest="$CFG_DEST" ;;
      CFG_RECENT=*) eval "$line"; cfg_recent="$CFG_RECENT" ;;
    esac
  done < <(config_py defaults)

  # Only fill unset values (flags / env already applied later).
  if [[ -z "$SOURCE_DIR" && -n "$cfg_source" ]]; then
    SOURCE_DIR="$cfg_source"
  fi
  if [[ -z "$DEST_DIR" && -n "$cfg_dest" ]]; then
    DEST_DIR="$cfg_dest"
  fi
  if [[ -n "$cfg_recent" ]]; then
    DEFAULT_RECENT="$cfg_recent"
  fi
}

apply_named_project() {
  # Resolve PROJECT token against config; populate match/paths/dest/recent.
  local token="$1"
  local line found=0
  [[ -z "$CONFIG_LOADED" || -z "$token" ]] && return 1

  FOUND=0
  PROJECT_KEY=""
  PROJECT_MATCHES=""
  PROJECT_PATHS=""
  PROJECT_DEST=""
  PROJECT_RECENT=""
  PROJECT_SOURCE=""
  while IFS= read -r line; do
    eval "$line"
  done < <(config_py resolve-project "$token")

  if [[ "${FOUND:-0}" != "1" ]]; then
    return 1
  fi

  FROM_CONFIG_PROJECT=1
  PROJECT="$PROJECT_KEY"
  # Prefer project dest when user did not pass --to or --dest.
  if [[ -n "$PROJECT_DEST" && -z "$DEST_ALIAS" ]] && (( ! TO_FLAG_SET )); then
    DEST_ALIAS="$PROJECT_DEST"
  fi
  if [[ -n "$PROJECT_RECENT" && -z "$RECENT" ]]; then
    RECENT="$PROJECT_RECENT"
  fi
  if [[ -n "$PROJECT_SOURCE" ]] && (( ! FROM_FLAG_SET )); then
    SOURCE_DIR="$PROJECT_SOURCE"
  fi
  logv "project config: $PROJECT_KEY (match: ${PROJECT_MATCHES})"
  return 0
}

apply_named_project_from_phrase() {
  local text="$1"
  local line
  [[ -z "$CONFIG_LOADED" || -z "$text" ]] && return 1

  FOUND=0
  while IFS= read -r line; do
    eval "$line"
  done < <(config_py resolve-project-in-text "$text")

  if [[ "${FOUND:-0}" != "1" ]]; then
    return 1
  fi

  FROM_CONFIG_PROJECT=1
  PROJECT="$PROJECT_KEY"
  if [[ -n "$PROJECT_DEST" && -z "$DEST_ALIAS" ]] && (( ! TO_FLAG_SET )); then
    DEST_ALIAS="$PROJECT_DEST"
  fi
  if [[ -n "$PROJECT_RECENT" && -z "$RECENT" ]]; then
    RECENT="$PROJECT_RECENT"
  fi
  if [[ -n "$PROJECT_SOURCE" ]] && (( ! FROM_FLAG_SET )); then
    SOURCE_DIR="$PROJECT_SOURCE"
  fi
  logv "project config (from phrase): $PROJECT_KEY (match: ${PROJECT_MATCHES})"
  return 0
}

resolve_destination_alias() {
  local name="$1"
  local line path=""
  [[ -z "$name" ]] && return 1

  if [[ -n "$CONFIG_LOADED" ]]; then
    FOUND=0
    DEST_PATH=""
    while IFS= read -r line; do
      eval "$line"
    done < <(config_py resolve-dest "$name")
    if [[ "${FOUND:-0}" == "1" && -n "$DEST_PATH" ]]; then
      DEST_DIR="$DEST_PATH"
      logv "dest alias: $name → $DEST_DIR"
      return 0
    fi
  fi

  # Fallback: treat as path
  if [[ "$name" == /* || "$name" == ~/* || "$name" == ./* ]]; then
    DEST_DIR="$(expand_path "$name")"
    return 0
  fi
  return 1
}

resolve_destination_from_phrase() {
  local text="$1"
  local line
  [[ -z "$CONFIG_LOADED" || -z "$text" ]] && return 1
  FOUND=0
  DEST_NAME=""
  DEST_PATH=""
  while IFS= read -r line; do
    eval "$line"
  done < <(config_py resolve-dest-in-text "$text")
  if [[ "${FOUND:-0}" == "1" && -n "$DEST_PATH" ]]; then
    if [[ -z "$DEST_ALIAS" ]]; then
      DEST_ALIAS="$DEST_NAME"
    fi
    # Only override if user did not pass --to
    DEST_DIR="$DEST_PATH"
    logv "dest from phrase: ${DEST_NAME} → $DEST_DIR"
    return 0
  fi
  return 1
}

resolve_one() {
  local arg="$1"
  local candidate matches match_count match base

  if [[ -z "$arg" ]]; then
    return 1
  fi

  if [[ "$arg" == /* && -f "$arg" ]]; then
    if is_markdown "$arg"; then
      expand_path "$arg"
      return 0
    fi
    echo "error: not a Markdown file: $arg" >&2
    return 1
  fi

  if [[ -f "$arg" ]]; then
    if is_markdown "$arg"; then
      expand_path "$arg"
      return 0
    fi
    echo "error: not a Markdown file: $arg" >&2
    return 1
  fi

  candidate="$(strip_outputs_prefix "$arg")"
  if [[ -f "$SOURCE_DIR/$candidate" ]] && is_markdown "$SOURCE_DIR/$candidate"; then
    expand_path "$SOURCE_DIR/$candidate"
    return 0
  fi
  if [[ -f "$SOURCE_DIR/${candidate}.md" ]]; then
    expand_path "$SOURCE_DIR/${candidate}.md"
    return 0
  fi

  base="$(basename "$arg")"
  base="${base%.md}"
  base="${base%.markdown}"
  base="${base%.MD}"
  matches=()
  while IFS= read -r -d '' match; do
    matches+=("$match")
  done < <(find "$SOURCE_DIR" -type f \( -iname "${base}.md" -o -iname "${base}.markdown" \) -print0 2>/dev/null)

  match_count="${#matches[@]}"
  if (( match_count == 1 )); then
    expand_path "${matches[0]}"
    return 0
  elif (( match_count > 1 )); then
    echo "error: ambiguous basename '${base}' — ${match_count} matches under ${SOURCE_DIR}:" >&2
    printf '  %s\n' "${matches[@]}" >&2
    echo "hint: pass a path relative to outputs/, e.g. docs/${base}.md" >&2
    return 1
  fi

  return 1
}

is_stopword() {
  case "$(tolower "$1")" in
    ''|a|an|and|can|claude|control|copy|copied|copies|day|days|dir|directory|docs|engineering|file|files|folder|for|from|hub|in|interface|into|last|latest|markdown|md|month|most|newest|of|or|output|outputs|over|please|project|recent|research|reviews|shared|the|to|top|under|week|you)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

parse_phrase() {
  local raw="$1"
  local lower
  lower="$(tolower "$raw")"

  if [[ -z "$DAYS" ]]; then
    if [[ "$lower" =~ last[[:space:]]+([0-9]+)[[:space:]]+days? ]]; then
      DAYS="${BASH_REMATCH[1]}"
    elif [[ "$lower" =~ past[[:space:]]+([0-9]+)[[:space:]]+days? ]]; then
      DAYS="${BASH_REMATCH[1]}"
    elif [[ "$lower" == *"past week"* || "$lower" == *"last week"* ]]; then
      DAYS=7
    elif [[ "$lower" == *"past month"* || "$lower" == *"last month"* ]]; then
      DAYS=30
    fi
  fi

  if [[ -z "$RECENT" ]]; then
    if [[ "$lower" =~ (last|recent|top|newest)[[:space:]]+([0-9]+) ]]; then
      RECENT="${BASH_REMATCH[2]}"
    elif [[ "$lower" == *"most recent"* || "$lower" == *"latest"* || "$lower" == *"newest"* ]]; then
      if [[ "$lower" =~ (most[[:space:]]+recent|latest|newest).+[[:space:]](output|file)([^s]|$) ]] || \
         [[ "$lower" =~ (most[[:space:]]+recent|latest|newest)[[:space:]]+(output|file)([^s]|$) ]]; then
        RECENT=1
      else
        RECENT="$DEFAULT_RECENT"
      fi
    fi
  fi

  if [[ -z "$SUBDIR" ]]; then
    for cand in docs research reviews latex panning zettelkasten coordination staging diagnostics; do
      if [[ "$lower" =~ (^|[^a-z])${cand}([^a-z]|$) ]]; then
        SUBDIR="$cand"
        break
      fi
    done
  fi

  # Prefer config project resolution from the full phrase.
  if [[ -z "$PROJECT" ]] && [[ -n "$CONFIG_LOADED" ]]; then
    apply_named_project_from_phrase "$raw" || true
  fi

  if [[ -z "$PROJECT" ]]; then
    local candidate=""
    if [[ "$lower" =~ (^|[^a-z0-9])([a-z0-9][a-z0-9_-]*)[[:space:]]+project([^a-z0-9]|$) ]]; then
      candidate="${BASH_REMATCH[2]}"
    elif [[ "$lower" =~ project[[:space:]]+([a-z0-9][a-z0-9_-]*) ]]; then
      candidate="${BASH_REMATCH[1]}"
    fi

    if is_stopword "$candidate"; then
      candidate=""
    fi

    if [[ -z "$candidate" ]]; then
      local cleaned token
      cleaned="$lower"
      cleaned="$(printf '%s' "$cleaned" | sed -E \
        -e 's/most[[:space:]]+recent//g' \
        -e 's/claude[[:space:]]+shared[[:space:]]+directory//g' \
        -e 's/shared[[:space:]]+directory//g' \
        -e 's/engineering[[:space:]]+hub//g' \
        -e 's/control[[:space:]]+interface//g' \
        -e 's/past[[:space:]]+week//g' \
        -e 's/last[[:space:]]+week//g' \
        -e 's/past[[:space:]]+month//g' \
        -e 's/last[[:space:]]+month//g' \
        -e 's/last[[:space:]]+[0-9]+[[:space:]]+days?//g' \
        -e 's/past[[:space:]]+[0-9]+[[:space:]]+days?//g' \
        -e 's/(last|recent|top|newest)[[:space:]]+[0-9]+//g')"
      for token in $cleaned; do
        if is_stopword "$token"; then
          continue
        fi
        if [[ "$token" =~ ^[a-z0-9][a-z0-9_-]*$ ]] && [[ ${#token} -ge 2 ]]; then
          candidate="$token"
          break
        fi
      done
    fi

    if [[ -n "$candidate" ]]; then
      PROJECT="$candidate"
    fi
  fi

  # Destination alias from phrase (config), if not already set via --dest/--to
  if [[ -z "$DEST_ALIAS" && -n "$CONFIG_LOADED" ]]; then
    resolve_destination_from_phrase "$raw" || true
  fi
}

has_filters() {
  [[ -n "$PROJECT" || -n "$CONTAINS" || -n "$DAYS" || -n "$RECENT" || -n "$SUBDIR" || -n "$PHRASE" || -n "$PROJECT_MATCHES" ]]
}

csv_foreach() {
  # Split comma-separated $1 and call command $2 with each non-empty field.
  local csv="$1"
  local fn="$2"
  local IFS=','
  local item
  # shellcheck disable=SC2086
  for item in $csv; do
    item="${item#"${item%%[![:space:]]*}"}"
    item="${item%"${item##*[![:space:]]}"}"
    [[ -z "$item" ]] && continue
    "$fn" "$item"
  done
}

_print_item() { printf '%s\n' "$1"; }

matches_filters() {
  local f="$1"
  local base lower_base needle matched
  base="${f##*/}"
  lower_base="$(tolower "$base")"

  if [[ -n "$PROJECT_MATCHES" ]]; then
    matched=0
    while IFS= read -r needle; do
      [[ -z "$needle" ]] && continue
      needle="$(tolower "$needle")"
      if [[ "$lower_base" == *"$needle"* ]]; then
        matched=1
        break
      fi
    done < <(csv_foreach "$PROJECT_MATCHES" _print_item)
    if (( ! matched )); then
      return 1
    fi
  elif [[ -n "$PROJECT" ]]; then
    needle="$(tolower "$PROJECT")"
    if [[ "$lower_base" != *"$needle"* ]]; then
      return 1
    fi
  fi
  if [[ -n "$CONTAINS" ]]; then
    needle="$(tolower "$CONTAINS")"
    if [[ "$lower_base" != *"$needle"* ]]; then
      return 1
    fi
  fi
  return 0
}

search_roots() {
  local root
  if [[ -n "$SUBDIR" ]]; then
    printf '%s\n' "$SOURCE_DIR/$(strip_outputs_prefix "$SUBDIR")"
    return
  fi
  if [[ -n "$PROJECT_PATHS" ]]; then
    while IFS= read -r root; do
      [[ -z "$root" ]] && continue
      printf '%s\n' "$SOURCE_DIR/$(strip_outputs_prefix "$root")"
    done < <(csv_foreach "$PROJECT_PATHS" _print_item)
    return
  fi
  printf '%s\n' "$SOURCE_DIR"
}

collect_matches() {
  local now cutoff f mtime
  local -a scored=()
  local line root

  now="$(date +%s)"
  cutoff=""
  if [[ -n "$DAYS" ]]; then
    if ! [[ "$DAYS" =~ ^[0-9]+$ ]]; then
      echo "error: --days must be an integer" >&2
      return 1
    fi
    cutoff=$((now - DAYS * 86400))
  fi

  while IFS= read -r root; do
    [[ -z "$root" ]] && continue
    if [[ ! -d "$root" ]]; then
      logv "skip missing path: $root"
      continue
    fi
    while IFS= read -r -d '' f; do
      matches_filters "$f" || continue
      mtime="$(file_mtime "$f")"
      if [[ -n "$cutoff" && "$mtime" -lt "$cutoff" ]]; then
        continue
      fi
      scored+=("$mtime|$f")
    done < <(find "$root" -type f \( -iname '*.md' -o -iname '*.markdown' \) -print0 2>/dev/null)
  done < <(search_roots)

  if (( ${#scored[@]} == 0 )); then
    return 2
  fi

  local sorted
  sorted="$(printf '%s\n' "${scored[@]}" | sort -t'|' -k1,1nr)"

  local count=0
  local limit=""
  if [[ -n "$RECENT" ]]; then
    if ! [[ "$RECENT" =~ ^[0-9]+$ ]] || [[ "$RECENT" -lt 1 ]]; then
      echo "error: --recent must be a positive integer" >&2
      return 1
    fi
    limit="$RECENT"
  fi

  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    mtime="${line%%|*}"
    f="${line#*|}"
    if [[ -n "$limit" ]] && (( count >= limit )); then
      break
    fi
    printf '%s\n' "$f"
    count=$((count + 1))
  done <<< "$sorted"
}

rel_display() {
  local f="$1"
  if [[ "$f" == "$SOURCE_DIR"/* ]]; then
    printf '%s\n' "${f#"$SOURCE_DIR"/}"
  else
    printf '%s\n' "$f"
  fi
}

dest_for_source() {
  local src="$1"
  local rel

  if (( FLAT )); then
    printf '%s\n' "$DEST_DIR/$(basename "$src")"
    return
  fi

  if [[ "$src" == "$SOURCE_DIR"/* ]]; then
    rel="${src#"$SOURCE_DIR"/}"
  else
    rel="$(basename "$src")"
  fi
  printf '%s\n' "$DEST_DIR/$rel"
}

copy_one() {
  local src="$1"
  local dest dest_parent
  dest="$(dest_for_source "$src")"
  dest_parent="$(dirname "$dest")"

  if (( DRY_RUN )); then
    echo "DRY-RUN  $(rel_display "$src")  ->  $dest"
    return 0
  fi

  mkdir -p "$dest_parent"
  if (( FORCE )); then
    cp -f "$src" "$dest"
  else
    if [[ -e "$dest" ]]; then
      echo "skip (exists, use --force): $dest" >&2
      return 0
    fi
    cp "$src" "$dest"
  fi
  echo "copied  $(rel_display "$src")  ->  $dest"
}

list_matches() {
  local f mtime count=0
  local -a files=()

  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    files+=("$f")
  done < <(collect_matches)

  if (( ${#files[@]} == 0 )); then
    echo "error: no Markdown matches" >&2
    describe_filters >&2
    return 2
  fi

  for f in "${files[@]}"; do
    mtime="$(file_mtime "$f")"
    printf '%s  %s\n' "$(format_mtime "$mtime")" "$(rel_display "$f")"
    count=$((count + 1))
  done
  logv "listed ${count} file(s)"
}

describe_filters() {
  echo -n "filters:"
  [[ -n "$PROJECT" ]] && echo -n " project=${PROJECT}"
  [[ -n "$PROJECT_MATCHES" ]] && echo -n " match=${PROJECT_MATCHES}"
  [[ -n "$CONTAINS" ]] && echo -n " contains=${CONTAINS}"
  [[ -n "$SUBDIR" ]] && echo -n " subdir=${SUBDIR}"
  [[ -n "$PROJECT_PATHS" && -z "$SUBDIR" ]] && echo -n " paths=${PROJECT_PATHS}"
  [[ -n "$DAYS" ]] && echo -n " days=${DAYS}"
  [[ -n "$RECENT" ]] && echo -n " recent=${RECENT}"
  [[ -n "$DEST_ALIAS" ]] && echo -n " dest=${DEST_ALIAS}"
  echo
}

copy_matches() {
  local f count=0
  local -a files=()

  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    files+=("$f")
  done < <(collect_matches)

  if (( ${#files[@]} == 0 )); then
    echo "error: no Markdown matches" >&2
    describe_filters >&2
    echo "hint: try --list --project ${PROJECT:-NAME}  or  --list-projects" >&2
    return 2
  fi

  if (( VERBOSE )); then
    echo -n "selected ${#files[@]} file(s); " >&2
    describe_filters >&2
  fi
  for f in "${files[@]}"; do
    copy_one "$f"
    count=$((count + 1))
  done
  echo "done: ${count} file(s) -> ${DEST_DIR}"
}

# ── arg parse ──────────────────────────────────────────────────────────
POSITIONAL=()
FROM_FLAG_SET=0
TO_FLAG_SET=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --config)
      CONFIG_PATH="${2:?--config requires a path}"
      shift 2
      ;;
    --from)
      SOURCE_DIR="$(expand_path "${2:?--from requires a directory}")"
      FROM_FLAG_SET=1
      shift 2
      ;;
    --to)
      DEST_DIR="$(expand_path "${2:?--to requires a directory}")"
      TO_FLAG_SET=1
      shift 2
      ;;
    --dest)
      DEST_ALIAS="${2:?--dest requires a name}"
      shift 2
      ;;
    -p|--project)
      PROJECT="${2:?--project requires a name}"
      shift 2
      ;;
    -c|--contains)
      CONTAINS="${2:?--contains requires a string}"
      shift 2
      ;;
    -s|--subdir)
      SUBDIR="${2:?--subdir requires a directory name}"
      shift 2
      ;;
    -n|--recent)
      RECENT="${2:?--recent requires a count}"
      shift 2
      ;;
    -d|--days)
      DAYS="${2:?--days requires a count}"
      shift 2
      ;;
    -q|--phrase|--query)
      PHRASE="${2:?--phrase requires a string}"
      shift 2
      ;;
    --list-projects)
      MODE="list-projects"
      shift
      ;;
    --list-destinations)
      MODE="list-destinations"
      shift
      ;;
    --list)
      MODE="list"
      if [[ $# -ge 2 && "$2" != -* ]]; then
        SUBDIR="$2"
        shift 2
      else
        shift
      fi
      ;;
    --all)
      MODE="all"
      if [[ $# -ge 2 && "$2" != -* ]]; then
        SUBDIR="$2"
        shift 2
      else
        shift
      fi
      ;;
    --flat)
      FLAT=1
      shift
      ;;
    --force)
      FORCE=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --quiet)
      VERBOSE=0
      shift
      ;;
    --)
      shift
      POSITIONAL+=("$@")
      break
      ;;
    -*)
      echo "error: unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
    *)
      POSITIONAL+=("$1")
      shift
      ;;
  esac
done

# Load config defaults (may set SOURCE_DIR / DEST_DIR / DEFAULT_RECENT)
apply_config_defaults

# Fill remaining defaults
if [[ -z "$SOURCE_DIR" ]]; then
  SOURCE_DIR="$DEFAULT_SOURCE"
fi
if [[ -z "$DEST_DIR" ]]; then
  DEST_DIR="$DEFAULT_DEST"
fi

SOURCE_DIR="$(expand_path "$SOURCE_DIR")"
DEST_DIR="$(expand_path "$DEST_DIR")"

case "$MODE" in
  list-projects)
    config_py list-projects
    exit 0
    ;;
  list-destinations)
    config_py list-destinations
    exit 0
    ;;
esac

if [[ ! -d "$SOURCE_DIR" ]]; then
  echo "error: source outputs directory not found: $SOURCE_DIR" >&2
  echo "hint: set source_dir in config, EH_OUTPUTS_DIR, or pass --from" >&2
  exit 1
fi

# Resolve --dest alias (overrides default dest unless --to was set)
if [[ -n "$DEST_ALIAS" ]]; then
  if (( TO_FLAG_SET )); then
    logv "note: --to overrides --dest $DEST_ALIAS"
  else
    if ! resolve_destination_alias "$DEST_ALIAS"; then
      echo "error: unknown destination alias: $DEST_ALIAS" >&2
      echo "hint: ${SCRIPT_NAME} --list-destinations" >&2
      exit 1
    fi
  fi
fi

if [[ -n "$PHRASE" ]]; then
  # Snapshot dest before phrase so we know if phrase should override
  _dest_before_phrase="$DEST_DIR"
  parse_phrase "$PHRASE"
  logv "phrase: \"$PHRASE\" → $(describe_filters | sed 's/^filters://')"
  # If phrase set DEST_ALIAS and user did not pass --to, dest already updated.
  if (( TO_FLAG_SET )); then
    DEST_DIR="$_dest_before_phrase"
  fi
fi

# Positional args: exact files, else project tokens / dest tokens
EXACT_FILES=()
for arg in "${POSITIONAL[@]+"${POSITIONAL[@]}"}"; do
  if looks_like_path_arg "$arg" || resolve_one "$arg" >/dev/null 2>&1; then
    if src="$(resolve_one "$arg")"; then
      EXACT_FILES+=("$src")
    else
      echo "error: could not find Markdown for: $arg" >&2
      exit 1
    fi
  else
    # Try as destination alias first when PROJECT already set
    if [[ -n "$PROJECT" ]] && [[ -z "$DEST_ALIAS" ]] && resolve_destination_alias "$arg" 2>/dev/null; then
      DEST_ALIAS="$arg"
      continue
    fi
    if [[ -z "$PROJECT" ]]; then
      PROJECT="$arg"
    elif [[ -z "$CONTAINS" ]]; then
      # Second token might be a dest alias
      if resolve_destination_alias "$arg" 2>/dev/null; then
        DEST_ALIAS="$arg"
      else
        CONTAINS="$arg"
      fi
    else
      CONTAINS="${CONTAINS} ${arg}"
    fi
  fi
done

# Resolve project name against config (match tokens, paths, dest, recent)
if [[ -n "$PROJECT" && "$FROM_CONFIG_PROJECT" -eq 0 ]]; then
  if apply_named_project "$PROJECT"; then
    :
  else
    # Free-text substring match (legacy behavior)
    PROJECT_MATCHES=""
  fi
fi

# Apply project dest if --to not set and dest alias pending
if [[ -n "$DEST_ALIAS" ]] && (( ! TO_FLAG_SET )); then
  resolve_destination_alias "$DEST_ALIAS" || true
fi

DEST_DIR="$(expand_path "$DEST_DIR")"

# Default recent window when filtering
if [[ "$MODE" != "all" && -z "$RECENT" && ${#EXACT_FILES[@]} -eq 0 && ( -n "$PROJECT" || -n "$CONTAINS" || -n "$PHRASE" || -n "$PROJECT_MATCHES" ) ]]; then
  RECENT="$DEFAULT_RECENT"
fi

case "$MODE" in
  list)
    list_matches
    exit $?
    ;;
  all)
    if (( ! DRY_RUN )); then
      mkdir -p "$DEST_DIR"
    fi
    if ! has_filters && [[ -z "$SUBDIR" ]]; then
      RECENT=""
    fi
    copy_matches
    exit $?
    ;;
  auto)
    if (( ${#EXACT_FILES[@]} > 0 )) && ! has_filters; then
      if (( ! DRY_RUN )); then
        mkdir -p "$DEST_DIR"
      fi
      copied=0
      for src in "${EXACT_FILES[@]}"; do
        copy_one "$src"
        copied=$((copied + 1))
      done
      echo "done: ${copied} file(s) -> ${DEST_DIR}"
      exit 0
    fi

    if (( ${#EXACT_FILES[@]} > 0 )) && has_filters; then
      echo "error: pass either exact file paths or filters/phrase, not both" >&2
      exit 1
    fi

    if ! has_filters && [[ ${#EXACT_FILES[@]} -eq 0 ]]; then
      echo "error: pass a project name, -q phrase, file path, or --list/--all" >&2
      echo "examples:" >&2
      echo "  ${SCRIPT_NAME} LVT" >&2
      echo "  ${SCRIPT_NAME} -q \"most recent LVT outputs to shared\"" >&2
      echo "  ${SCRIPT_NAME} --list-projects" >&2
      exit 1
    fi

    if (( ! DRY_RUN )); then
      mkdir -p "$DEST_DIR"
    fi
    copy_matches
    exit $?
    ;;
esac
