# Engineering Hub Control Interface

A persistent, agent-first workspace enabling collaboration between engineers and AI agents on technical projects. Designed for acoustic engineering consulting workflows, connecting to a Django backend (consultingmanager) via REST API.

## Overview

Engineering Hub provides two complementary modes of AI collaboration:

1. **Orchestrator** (task-driven) -- watches your org-roam **daily journal** and the Journaler-owned **`pending-tasks.org`** queue for `@agent:` task lines, dispatches work to specialized agents via Claude API, local MLX models, or an Ollama server, and writes results back to the workspace. Optionally runs agent tasks in **Docker containers** for isolation.
2. **Journaler** (ambient) -- a persistent daemon that runs a local ~32B model via MLX, continuously monitors your org-roam workspace, delivers morning briefings, and responds to ad-hoc questions through **`engineering-hub journaler chat`** (interactive) and an **HTTP** chat endpoint when the daemon is running.

They coexist cleanly: the Orchestrator processes explicit tasks while the Journaler maintains ambient awareness. The Journaler can read the full org-roam workspace, write to daily journals and roam nodes via slash commands where appropriate, and **delegate agent work** using **`/agent`**, **natural-language turns** (in **immediate** mode), or an **overnight queue** (`/queue`, `/tasks`) that writes only to **`pending-tasks.org`** — not to your daily journal. Delegation uses local MLX or Claude API execution.

### Key Features

- **Org-roam Integration**: Tasks live in daily `.org` journal files using `- [ ] @agent:` syntax, and optionally in **`.journaler/pending-tasks.org`** (Journaler queue) under `* Pending Agent Tasks`
- **Specialized Agents**: Research, technical-writer, standards-checker, and more with domain expertise
- **Django Integration**: Pulls project context, standards, and files from the consultingmanager API
- **File Watching**: Monitors workspace for changes and automatically dispatches agent tasks
- **Local MLX Models**: Run agents on Apple Silicon via `mlx-lm` with HuggingFace model IDs
- **Ollama Backend**: Use Ollama for agent generation — works on any platform and inside Docker containers
- **Docker Containers**: Isolate agent task execution in ephemeral containers with resource limits and network controls
- **Journaler Daemon**: Always-on ambient listener with morning briefings, HTTP chat, and Slack integration — optional **model profiles**, Qwen3 **thinking mode**, CLI `--profile` / `--model`, and **`/model`** to switch checkpoints without losing chat history
- **Agent Delegation**: **`journaler chat`** and the daemon’s HTTP `/chat` both use the same setup: an **AgentDelegator**, YAML **skills** summaries injected into the system prompt (personas, when-to-use hints, examples), and **`/agent`** / **`/skills`** slash commands — execution is local MLX or Claude API, selectable per-command via `journaler.agent_backend` and `--backend`
- **Task planner & overnight queue**: **`/queue`** and **`/tasks`** manage proposals and commits to **`pending-tasks.org`**; **`journaler.default_task_mode`** chooses **immediate** (inline / classifier-driven delegation) vs **propose** (`DISPATCH:` + confirmation). Morning briefings include a short summary of recent queue activity when present
- **Skills System**: Extensible `skills/` directory of YAML files defines each agent personality's capabilities; drop a new `.yaml` to add a delegation skill without code changes
- **Context Management**: Token-aware conversation history with automatic compression, topic-shift archival, end-of-day reset, and manual `/clear` controls — keeps the local model coherent across a full workday
- **Org-Roam Write Skill**: Journaler chat can write properly-formatted org-roam files — add TODOs, mark tasks done, append notes to today's journal (`/note`), set a session target on any roam note (`/open`), append under a heading there (`/edit`), search by title (`/find`), and create new nodes — via slash commands
- **Journaler Export**: CLI `journaler export` reads the persisted chat transcript (`conversation.jsonl`) and writes org-roam-friendly output to **stdout** by default (raw per-turn org, optional MLX **summary + open TODOs**); use `--note`, `--find-title`, `-o`, or `--new-node` for file targets. In **`journaler chat`**, bare **`/export`** writes under **`conversation_exports/`** in the configured org-roam root unless you pass one of those targets.
- **Context File Loading**: Inject files or directories into the Journaler's live context (`/load`) or the persistent memory store (`engineering-hub load`)
- **Vector Memory**: Local semantic memory (`memory.db`) with Ollama embeddings for past-task and ingest retrieval
- **PDF Reference Corpus**: Optional ingested reference corpus (`corpus.db` from **libraryfiles-corpus**) injected as RAG into **Journaler chat** turns and **Orchestrator** agent tasks (separate from workspace memory)
- **Context pipeline diagnostic**: Opt-in **`engineering-hub diagnostic context-pipeline`** command (and matching config/env flags) persist full formatted agent context, heuristic checklists, optional corpus audit excerpts, and agent outputs under `outputs/diagnostics/context-pipeline/<run_id>/` — see [diagnostics/RUNBOOK.md](diagnostics/RUNBOOK.md)

## Requirements

- Python 3.11+
- Access to Anthropic API (Claude), a local MLX model on Apple Silicon, or an Ollama server
- Django consultingmanager backend (optional, for full project context)
- Ollama with `nomic-embed-text` (optional, for memory/embeddings and PDF corpus query embeddings; also serves as a generation backend)
- **libraryfiles-corpus** (optional, `pip install -e …`) plus a built `corpus.db` when using PDF reference RAG
- Docker (optional, for containerised agent execution)

## Quick Start

### 1. Clone and Initialize

```bash
git clone <repository-url>
cd engineeringhub_controlinterface
source init.sh
```

### 2. Install Dependencies

```bash
# Core installation
pip install -e '.[dev]'

# For local MLX model support (Apple Silicon)
pip install -e '.[mlx]'
```

### 3. Configure

Copy the example configuration and add your API keys:

```bash
cp config/config.example.yaml config/config.yaml
```

Or set environment variables:

```bash
export ENGINEERING_HUB_ANTHROPIC_API_KEY="your-key-here"
export ENGINEERING_HUB_DJANGO_API_TOKEN="your-token-here"
```

### 4. Run the Orchestrator

```bash
# Start the task-driven orchestrator (watches for @agent: tasks)
engineering-hub start

# Or process pending tasks once and exit
engineering-hub run-once

# Context pipeline diagnostic (synthetic tasks from YAML; persists context + optional LLM output)
engineering-hub diagnostic context-pipeline --dry-run-context-only -v   # no model calls
engineering-hub diagnostic context-pipeline -v                          # full run (needs llm_provider + credentials)
```

### 5. Run the Journaler

```bash
# Pre-download the model to local HF cache before first use (~17GB for default 4-bit)
engineering-hub journaler download

# Start the ambient listener daemon
engineering-hub journaler start

# Interactive chat (loads model, no daemon)
engineering-hub journaler chat

# Generate a morning briefing on demand
engineering-hub journaler briefing

# View the latest briefing
engineering-hub journaler briefing --latest

# Check daemon status
engineering-hub journaler status

# Run a single org-roam scan
engineering-hub journaler scan

# Clear conversation history (soft — keeps context snapshot)
engineering-hub journaler clear

# Compress history into a summary, then clear
engineering-hub journaler clear --summarize

# Full reset: clear conversation + wipe scan state
engineering-hub journaler clear --hard

# Export chat transcript to org (default: stdout; source: .journaler/conversation.jsonl)
engineering-hub journaler export
engineering-hub journaler export -o ~/org-roam/exports/chat.org
engineering-hub journaler export --jsonl .journaler/conversation.jsonl --note ~/org-roam/20260212-project.org
engineering-hub journaler export --find-title "Phase B" --heading "Journaler capture"
engineering-hub journaler export --new-node "Chat export 2026-04-06"
engineering-hub journaler export --summarize --note ~/org-roam/my-note.org   # loads MLX; emits * Summary and * Open TODOs

# Same export from an active `journaler chat` session (shell-like quoting for paths/titles), e.g.:
#   /export
#   /export -o ~/org-roam/exports/chat.org
#   /export --summarize --note ~/org-roam/my-note.org --heading "Journaler capture"
#   /export --help

# Pick a named profile or HF id (applies to start, chat, briefing, download, export --summarize)
engineering-hub journaler --profile reasoning chat
engineering-hub journaler --model mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit start
```

`journaler download` uses the same resolution rules as the other subcommands, so run it with `--profile` / `--model` if you want to prefetch a non-default checkpoint.

`journaler export --summarize` loads the Journaler MLX model once (same `--profile` / `--model` flags as other journaler commands). Raw export does not load a model.

In **`journaler chat`**, **`/export`** uses the same export pipeline as the CLI; if you do not pass `-o`, `--note`, `--new-node`, or `--find-title`, it writes a new org-roam node under **`conversation_exports/`** at the root of your configured org-roam tree (the parent of `journal.org_journal_dir`). Use `-o` or **`--new-node`** when you want a different path or title.

### 6. Load Files into Context

**In a live `journaler chat` session** — use slash commands to inject file content into the model’s context, delegate to agents, and export the transcript:

```
/load path/to/file.md           Load a single file
/load path/to/dir/              Load all supported files in a directory
/load path/to/dir/ -r           Load recursively
/load_browse                    Interactive file browser (arrow keys, multi-select)
/files                          List currently loaded files (with sizes)
/files clear                    Remove all loaded files from context
/export                         Export transcript to `<org-roam>/conversation_exports/` (see below)
/export -o ~/path/to/out.org    Same flags as `engineering-hub journaler export`
/export --help                  Full `/export` flag list
/agent technical-writer ...     Delegate inline (see Agent Delegation below)
/agent_browse                   Browse and pick an agent skill interactively
/tasks …                        Overnight queue: list, confirm, commit, rollback (see below)
/queue <description>            Propose one task for the queue (then /tasks confirm && commit)
/skills                         List delegation skills / personas from skills/*.yaml
/open today                     Set /edit target to today's journal (or /open <path>, /open <title>)
/edit_browse                    Browse org-roam files to set /edit target
/edit Section :: body text      Append under a heading in the file opened with /open
/help                           Show all slash commands
```

Supported extensions: `.md`, `.txt`, `.org`, `.py`, `.yaml`, `.yml`, `.json`, `.tex`, `.csv`, `.toml`, `.rst`. Each `/load` is capped from your `journaler.model_context_window`, current conversation/history usage, and optional `journaler.load_*` keys in config (documented under **Journaler → Configuration** below). Oversized files are truncated with a notice. Directory loads share one remaining budget across files (recomputed after each file). Loaded files appear in the model's system prompt on every turn, count toward `/budget` and context pressure, and are cleared when the session ends.

**From the command line** — persist files into the long-term memory store for semantic search:

```bash
# Load a single file
engineering-hub load path/to/notes.md

# Load an entire directory
engineering-hub load path/to/dir/ --recursive

# Associate with a project and tag
engineering-hub load path/to/report.pdf --project 42 --tag review
```

Files loaded this way are captured via `MemoryService` and are searchable by all agents through normal semantic retrieval.

## Project Structure

```text
engineeringhub_controlinterface/
├── src/engineering_hub/
│   ├── agents/          # Agent backends (Anthropic, MLX, Ollama), worker, prompts
│   ├── cli.py           # Command-line interface
│   ├── config/          # Settings (pydantic-settings) and YAML loader
│   ├── container/       # Docker container execution
│   │   ├── docker_executor.py  # Host-side container lifecycle management
│   │   ├── router.py           # Task routing (local vs container)
│   │   ├── task_payload.py     # Serialisation for container payloads
│   │   ├── task_runner.py      # Entry point that runs inside the container
│   │   └── resource_limits.py  # Per-container CPU/memory/timeout limits
│   ├── context/         # Context building and formatting for agents
│   ├── core/            # Data models, exceptions, constants
│   ├── django/          # Django REST API client and cache
│   ├── journaler/       # Journaler ambient listener daemon
│   │   ├── daemon.py        # Main loop, scheduler, signal handling, EOD clear
│   │   ├── context.py       # Org-roam scanner with mtime-based diff
│   │   ├── context_manager.py # Token budget, compression, topic tracking, pressure mgmt
│   │   ├── delegator.py     # AgentDelegator + JournalerMLXBackendAdapter (delegation bridge)
│   │   ├── engine.py        # ConversationEngine + ConversationalMLXBackend
│   │   ├── chat_server.py   # HTTP endpoint (POST /chat, GET /status, GET /skills)
│   │   ├── org_parser.py    # Focused org-mode parser (read)
│   │   ├── org_writer.py    # Org-roam write utilities (write/create/find)
│   │   ├── prompts.py       # System prompt + workspace layout + skills block templates
│   │   ├── task_committer.py # pending-tasks.org append + rollback
│   │   ├── task_intent_extractor.py # MLX JSON classifier (immediate vs queue vs chat)
│   │   ├── task_planner_models.py   # ProposedTask, TaskPlannerSession
│   │   ├── task_slash.py    # /tasks and /queue handlers
│   │   ├── slack.py         # Slack webhook poster
│   │   └── models.py        # ContextSnapshot, ScanState, OrgEntry
│   ├── mcp/             # FastMCP server integration
│   ├── memory/          # Vector memory (SQLite + Ollama embeddings)
│   ├── notes/           # Journal/org-roam parsing and task dispatch
│   └── orchestration/   # Orchestrator, dispatcher, file watcher
├── config/
│   └── config.example.yaml
├── prompts/             # Agent system prompts (used by Orchestrator and Journaler delegation)
├── skills/              # Agent delegation skill definitions (YAML, one per agent type)
├── latex-styles/        # Named LaTeX style profiles (YAML) for the latex-writer agent
├── latex-templates/     # Raw .tex preamble partials for direct template loading
├── Dockerfile           # Full orchestrator image (Linux deployment)
├── Dockerfile.task-runner # Slim ephemeral task container image
├── docker-compose.yml   # Ollama service + shared Docker network
└── tests/
```

## Journaler: Ambient Listener

The Journaler is a persistent daemon that runs a local ~32B model on Apple Silicon via MLX, continuously monitors your org-roam workspace, and provides ambient awareness of your projects.

### How It Works

- **Scans** org-roam (full tree or only `journal.org_journal_dir` plus optional `journaler.watch_dirs`) every 10 minutes (mtime-based incremental diff)
- **Extracts** headings, TODO/DONE items, timestamps, and `@agent:` tasks from `.org` files
- **Reads** recent agent outputs from `memory.db` via `MemoryService.browse_recent()`
- **Compresses** everything into a rolling context snapshot (~4000 tokens)
- **Knows** the workspace layout and org-roam format conventions — injected into the system prompt when the conversation engine starts so the model can reason about file locations and produce valid org syntax
- **Loads agent personas** from `skills/*.yaml`: a concise **skills block** (display name, description, when-to-use, example `/agent` lines) is appended to the system prompt for **both** `journaler start` and **`journaler chat`**. On the daemon, each scheduled org-roam scan refreshes the rolling context snapshot **and re-attaches** that skills block so personas are not dropped mid-run
- **Uses** `journaler.agent_backend`, optional `journaler.skills_dir`, and optional `journaler.anthropic_api_key` (else `anthropic.api_key` / `ENGINEERING_HUB_ANTHROPIC_API_KEY`) for delegation — same resolution for daemon and interactive chat
- **Generates** a morning briefing at a configurable time (default 9:00 AM), with an extra **pending-tasks.org** summary when recent queue timestamps appear in that file
- **Responds** to ad-hoc questions via an HTTP chat endpoint on `localhost:18790`
- **Writes** to daily journals and org-roam nodes via slash commands where intended; **overnight queue** tasks go only to **`pending-tasks.org`** (see **`/tasks`** / **`/queue`**)
- **Posts** briefings and alerts to Slack via incoming webhooks (optional)

### Configuration

Add a `journaler:` section to your `config.yaml`:

```yaml
journaler:
  enabled: true
  model_path: "mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit"  # see model table below
  model_context_window: 32768   # match your model's actual context window
  scan_interval_min: 10
  briefing_enabled: true
  briefing_time: "09:00"
  # scan_org_roam_tree: true        # false = only journal.org_journal_dir + watch_dirs
  # journal_lookback_days: 5
  # journal_max_files: 5
  # watch_dirs: []
  chat_enabled: true
  chat_host: "127.0.0.1"
  chat_port: 18790
  slack_enabled: false
  slack_webhook_url: ""  # or set JOURNALER_SLACK_WEBHOOK env var
  max_conversation_history: 20
  max_tokens: 4096

  # Agent delegation — applies to BOTH `journaler start` and `journaler chat`
  agent_backend: "mlx"   # "mlx" | "claude" | "auto" (see Agent Delegation below)
  # anthropic_api_key: "" # optional Journaler-only override; else top-level anthropic.api_key / ENGINEERING_HUB_ANTHROPIC_API_KEY
  # skills_dir: "~/org-roam/engineering-hub/skills"  # default: skills/ at repo root (resolved from YAML)

  # Overnight queue (Orchestrator scans this file in org mode alongside daily journals)
  # pending_tasks_file: "~/path/to/pending-tasks.org"  # default: workspace_dir/.journaler/pending-tasks.org
  # default_task_mode: "immediate"   # "immediate" (classifier may auto-delegate) or "propose" (DISPATCH + confirm)

  # Context management (all values below are defaults — omit to use defaults)
  context_management:
    compress_at: 0.70              # compress history when window is 70% full
    emergency_trim_at: 0.90        # force-trim if still critical after compression
    auto_clear_on_topic_shift: true
    notify_user_on_action: true    # prepend [Context compressed] notes to responses
    end_of_day_time: "00:00"       # daily conversation reset time
    inactivity_clear_minutes: 120  # auto-clear after 2h of silence
    capture_daily_to_memory: false # write daily summaries to memory.db
    reserved_for_generation: 4096  # tokens held back for model output (≥ journaler.max_tokens)

  # Optional — slash /load size limits (see config.example.yaml for defaults)
  # load_max_context_fraction: 0.40   # fraction of remaining context per file chunk
  # load_max_chars_absolute: 200000
  # load_min_chars: 1024
  # load_slack_tokens: 256
```

`model_path` is optional: if omitted, the Journaler falls back to `mlx.model_path` (the orchestrator MLX path), then to a built-in default (`mlx-community/gemma-4-31b-it-8bit`). Use `journaler download` after changing paths.

#### Model profiles and thinking mode

You can define **named profiles** under `journaler.models` and select one with `journaler.model_profile`. Each profile sets `model_path`, optional `model_context_window`, sampling (`temp`, `top_p`, …), `mlx_backend` (`auto`, `mlx-lm`, or `mlx-vlm`), and **`enable_thinking`** for Qwen3-style chat templates (`null` = omit the argument for models like Gemma; `true` / `false` toggles reasoning blocks on supported tokenizers).

**Resolution order** (same for daemon, interactive chat, and `journaler download`):

1. CLI `--model <hf-id-or-local-path>` (highest priority)
2. CLI `--profile <name>`
3. `journaler.model_profile` when `journaler.models` is non-empty
4. Legacy: `journaler.model_path` → `mlx.model_path` → built-in default

If you only set `journaler.model_path` (no `models:` map), behavior matches the single-model setup above.

Example:

```yaml
journaler:
  model_profile: "default"
  models:
    default:
      model_path: "mlx-community/gemma-4-31b-it-8bit"
      model_context_window: 131072
      enable_thinking: null
    reasoning:
      model_path: "mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit"
      model_context_window: 32768
      temp: 0.6
      top_p: 0.95
      enable_thinking: true
```

Switching models at runtime (without restarting):

- **Interactive chat:** `/model` (status), `/model reasoning` (named profile), `/model path <hf-id-or-path>` (one-off path).
- **HTTP chat (daemon):** send the same text as the JSON `message`, e.g. `{"message": "/model reasoning"}`. Slash commands **`/agent`**, **`/tasks`**, **`/queue`**, **`/skills`**, and **`/model`** are handled the same way as in interactive chat (where applicable). The delegator’s local MLX backend stays in sync so `/agent --backend mlx` uses the newly loaded weights.

Reloading a model loads weights again (seconds to tens of seconds, large RAM use). Conversation history is kept.

#### Recommended models

| Model | Type | Weights | RAM required | Notes |
| --- | --- | --- | --- | --- |
| `mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit` | MoE Instruct | ~17GB | ~32GB | Fast, low RAM; good default *choice* on 32GB machines; supports `enable_thinking` in profiles |
| `mlx-community/Qwen2.5-32B-Instruct-4bit` | Dense Instruct | ~19GB | ~40GB | Stronger instruction following; good for 64GB+ machines |
| `mlx-community/Qwen3-32B-4bit` | Dense | ~19GB | ~40GB | Highest quality in the 32B family; choose on 64–128GB machines |

Pre-download the chosen model before first use (avoids a silent in-process download):

```bash
# Download the default model
engineering-hub journaler download

# Or specify a different model via config, then download that
engineering-hub journaler download
```

The Journaler uses its own model (separate from the orchestrator's `llm_provider` setting), so both can run simultaneously. On a 128GB Apple Silicon Mac, the default MoE model uses ~17GB weights leaving plenty of headroom for the orchestrator.

### HTTP Chat API

When the daemon is running with `chat_enabled: true`:

```bash
# Ask a question
curl -X POST http://localhost:18790/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What tasks are pending for project 42?"}'

# Delegate to the research agent inline
curl -X POST http://localhost:18790/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "/agent research IBC 1207.3 requirements --project 42 --backend claude"}'

# Check daemon status
curl http://localhost:18790/status

# Get the latest briefing
curl http://localhost:18790/briefing

# List available agent delegation skills
curl http://localhost:18790/skills

# Switch to another configured profile (same syntax as interactive /model)
curl -X POST http://localhost:18790/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "/model reasoning"}'
```

### Daemon Management (macOS)

The simplest approach is tmux:

```bash
tmux new-session -d -s journaler 'engineering-hub journaler start'
```

For always-on operation, use a launchd plist at `~/Library/LaunchAgents/com.engineeringhub.journaler.plist` with `KeepAlive` and `RunAtLoad` set to true.

### Context Management

The Journaler runs all day, and a 32B model's context window fills up over hours of conversation. Six layered strategies keep the model coherent without manual restarts:

| Strategy | When it fires | What it does |
| --- | --- | --- |
| **Rolling window** | Always | Keeps the last N turns; evicts oldest non-preserved turns to the JSONL log |
| **Compression** | Window ≥ 70% full | Asks the model to summarize earlier turns into a ~200-word paragraph; replaces them with a single preserved system message |
| **Emergency trim** | Window ≥ 90% after compression | Force-drops to the last 3 turns |
| **Topic-aware clear** | Topic shift detected (3 consecutive on-topic messages) | Archives the old topic, starts fresh with the new one |
| **End-of-day reset** | Scheduled (default midnight) | Compresses the full day, saves to `daily_summaries/YYYY-MM-DD.md`, resets history |
| **Manual clear** | `/clear` command | User-controlled: soft, compress-then-clear, or full reset |

When the engine takes an automatic action (compression, topic shift), a bracketed note is prepended to the model's response:

```
[Context compressed: freed 3,200 tokens from 12 earlier exchanges]

Journaler: Project 42 is active. You have two pending tasks...
```

Set `notify_user_on_action: false` in `context_management` to suppress these notes.

### Interactive Chat: Slash Commands

While in `engineering-hub journaler chat`, any input starting with `/` is handled as a command rather than forwarded to the model:

**Context management**

| Command | Description |
| --- | --- |
| `/clear` | Soft clear: archive conversation history, keep context snapshot |
| `/clear --summarize` | Compress history into a summary, then clear |
| `/clear --hard` | Full reset: clear conversation and wipe scan state |
| `/status` | Show context pressure, utilization %, turn count, and current topic |
| `/budget` | Show full token budget breakdown (system prompt, snapshot, history, available) |
| `/topic` | Show the currently detected conversation topic |

**Model switching** (requires `journaler.models` in config for profile names)

| Command | Description |
| --- | --- |
| `/model` | Show active model path, profile name, context window, `enable_thinking`, and `mlx_backend` |
| `/model <profile>` | Load the named profile from `journaler.models` (keeps chat history) |
| `/model path <id-or-path>` | Load a Hugging Face id or local MLX snapshot path |

**File loading**

| Command | Description |
| --- | --- |
| `/load <path>` | Load a file or directory into the current conversation context |
| `/load <path> -r` | Load a directory recursively |
| `/load_browse` | Interactive fullscreen file browser for org-roam — arrow keys to navigate, Space to multi-select, Enter to load |
| `/files` | List all files currently loaded, with character counts |
| `/files clear` | Remove all loaded files from context |

**Export transcript** (same pipeline as `engineering-hub journaler export`; default file target differs in chat)

| Command | Description |
| --- | --- |
| `/export` | Export `.journaler/conversation.jsonl` to a new `.org` file under `<org-roam>/conversation_exports/` |
| `/export …` | Flags: `--jsonl`, `--summarize`, `-o` / `--output`, `--note`, `--heading`, `--find-title`, `--new-node` (shell-style quoting supported) |
| `/export --help` | Print usage |

**Agent delegation**

| Command | Description |
| --- | --- |
| `/agent <type> <desc> [--project <id>] [--backend mlx\|claude]` | Delegate a task to a named agent and get the result inline. Types: `research`, `technical-writer`, `standards-checker`, `technical-reviewer`, `weekly-reviewer`, `latex-writer` |
| `/agent_browse` | Interactive skill picker — arrow keys to browse agents, Enter to select, then type a task description |
| `/skills` | List all available agent delegation skills with descriptions and examples |
| `/tasks` | Show session queue proposals, or use `/tasks confirm`, `/tasks commit`, `/tasks reject N`, `/tasks edit N <text>`, `/tasks clear`, `/tasks rollback [N \| --all]` |
| `/queue <description>` | Shorthand to propose one overnight task (defaults agent to `research` until you edit/confirm); then `/tasks confirm` and `/tasks commit` |

**Org-roam write operations**

| Command | Description |
| --- | --- |
| `/task <description>` | Add `- [ ] <description>` to today's journal under `* Overnight Agent Tasks` |
| `/done <fragment>` | Mark the first matching `- [ ]` item as `- [X]` with a `CLOSED:` timestamp |
| `/note <heading> :: <text>` | Append text under a heading in today's journal (creates the heading if absent) |
| `/open` | Print the current `/edit` target path, if any |
| `/open clear` | Clear the session edit target |
| `/open today` | Set the edit target to today's daily journal (creates the file if missing) |
| `/open <path>` | Set target to an existing `.org` file; path must resolve under the configured org-roam directory |
| `/open <title fragment>` | Set target when exactly one file matches `#+title:` (substring, case-insensitive); otherwise list matches for disambiguation |
| `/edit <heading> :: <text>` | Append text under a heading in the note opened with `/open` (same ` :: ` delimiter as `/note`) |
| `/edit_browse` | Interactive file browser to set the `/edit` target — browse `.org` files, Enter to select |
| `/find <title fragment>` | Search all org-roam files for a case-insensitive `#+title:` match; prints matching paths |

**General**

| Command | Description |
| --- | --- |
| `/help` | Show the full list of available slash commands |

Tasks added with `/task` use the `- [ ] @agent:` format understood by the Orchestrator, so they will be picked up and dispatched automatically. **`/tasks commit`** writes confirmed proposals to **`pending-tasks.org`** (path: `journaler.pending_tasks_file`, default **`workspace_dir/.journaler/pending-tasks.org`**), which the Orchestrator also scans in org mode. `/agent` runs immediately and returns output in the chat turn.

With **`journaler.default_task_mode: immediate`** (default), ordinary messages that describe agent work may be **classified** and **delegated inline** (no `/agent` prefix) unless you use explicit **queue** language (“run later”, “queue for tonight”, …) or **`/queue`**. With **`default_task_mode: propose`**, that auto-path is off; the model uses **`DISPATCH:`** lines and you confirm before the agent runs (interactive chat prompts **Run it? [y/N]**; HTTP `/chat` still auto-runs a `DISPATCH` after the model responds, as before).

**`/model`** in interactive chat reloads the MLX weights but **keeps the delegator’s adapter in sync**, so `/agent --backend mlx` continues to use the active checkpoint (same behavior as HTTP `/chat`). **`/export`**, **`/open`**, **`/edit`**, and the **`/load_browse`** / **`/agent_browse`** / **`/edit_browse`** TUIs are only in interactive **`journaler chat`**; the HTTP endpoint handles **`/model`**, **`/agent`**, **`/tasks`**, **`/queue`**, and **`/skills`**. Loaded files are appended to the system prompt as fenced blocks and persist for the life of the chat session only.

To persist files for long-term retrieval across sessions, use `engineering-hub load` instead (see [Load Files into Context](#6-load-files-into-context)).

### Agent Delegation

The Journaler can delegate tasks directly to any named agent personality and return the result inline in the chat conversation — no need to write a journal task and wait for the overnight Orchestrator run.

**Modes (see `journaler.default_task_mode` in config):**

- **`immediate`** — Prefer inline execution: a small structured classifier may route suitable user messages to **`AgentDelegator`** automatically. Explicit **queue** phrasing or **`/queue`** adds a **proposal** to the session planner instead (confirm with **`/tasks`** before **`commit`**).
- **`propose`** — Same as the earlier **DISPATCH** flow: the model suggests **`DISPATCH: /agent …`** and you confirm before running (interactive CLI); corpus and loaded files still apply to **`/agent`** and to delegated runs.

**Overnight queue:** Use **`/queue <description>`** or natural language that clearly defers work, then **`/tasks confirm`** and **`/tasks commit`** to append to **`pending-tasks.org`**. Roll back with **`/tasks rollback`**. The Orchestrator picks up unchecked items there on its next scan; completed tasks are moved under **`* Completed Agent Tasks`** in that file when the run finishes.

#### The `/agent` command

```text
/agent <type> <description> [--project <id>] [--backend mlx|claude]
```

| Argument | Description |
| --- | --- |
| `<type>` | Agent personality: `research`, `technical-writer`, `standards-checker`, `technical-reviewer`, `weekly-reviewer` |
| `<description>` | Free-text task description |
| `--project <id>` | Optional Django project ID (stored on the task; does **not** load Orchestrator-style Django + PDF corpus into the delegated prompt — use journal `@agent:` + `[[django://project/id]]` for that) |
| `--backend mlx` | Use the local MLX model (reuses the Journaler's loaded model — no extra RAM) |
| `--backend claude` | Use the Claude API (requires `journaler.anthropic_api_key` or global `anthropic.api_key` / env) |

The default backend is controlled by `journaler.agent_backend` in config (`"mlx"` uses the local model; set `"auto"` if you want Claude when a key is present, otherwise MLX). The `--backend` flag overrides this per-command.

For **draft reports, protocols, executive summaries, and other client-facing Markdown deliverables**, use the **`technical-writer`** persona. The default Journaler system prompt and workspace layout describe **available agents**, **immediate vs queue** behavior, and practical routes: **`/agent technical-writer …`**, natural-language delegation in **immediate** mode, queue **`/task`** / journal lines with `@technical-writer:`, **`/queue`** + **`/tasks commit`** for **`pending-tasks.org`**, optional **`--project <id>`** for Django context, and **`/skills`** for full persona text. Delegated technical-writer runs use `prompts/technical-writer.txt`; saved artifacts often land under **`outputs/docs/`**.

**Examples:**

```text
/agent research IBC 1207.3 occupant comfort requirements --project 42
/agent technical-writer draft executive summary for noise assessment --project 25 --backend claude
/agent standards-checker audit ASTM citations in draft report --backend mlx
/agent weekly-reviewer summarize this week's work and open loops
/agent latex-writer --style executive-summary draft exec summary for project 5
/agent latex-writer --list-styles
```

If no live backend is configured, the command falls back to writing the task to today's journal under `* Overnight Agent Tasks` for the Orchestrator to pick up on its next scan.

#### Backend selection

| Mode | Description |
| --- | --- |
| `"mlx"` (default) | Always the local model — the Journaler's already-loaded MLX model is reused via a thin adapter, so no second model is loaded and no extra RAM is consumed |
| `"claude"` | Always Claude API — errors if no key is configured |
| `"auto"` | Claude API if a key is configured, otherwise local MLX (previous default behavior) |

#### The `/skills` command

In **`journaler chat`**, type `/skills`. With the daemon, use `GET http://localhost:18790/skills` or send a chat message **`/skills`** over `POST /chat`. Each path lists loaded skills with descriptions and example invocations.

#### Skills system

Agent delegation capabilities are defined in the top-level `skills/` directory alongside `prompts/`. Each `.yaml` file describes one agent type:

```yaml
# skills/research.yaml
name: research
display_name: Research Agent
agent_type: research
description: |
  Gathers and synthesizes technical information from authoritative sources...
when_to_use:
  - User asks to research a topic or standard
  - User needs information about ASTM, ISO, IBC, or ANSI requirements
invocation_examples:
  - "/agent research IBC 1207.3 requirements --project 42"
  - "/agent research ASTM E336-17a vs E336-21 material differences"
```

To add a new delegation capability, drop a new `.yaml` file into `skills/` — no code changes needed. The Journaler loads all skill files when the **ConversationEngine** starts (**daemon** or **interactive `journaler chat`**) and injects a summary into the system prompt (including **when_to_use** hints) so the ambient model knows what it can delegate and how. **Custom** `.journaler/system_prompt.txt` overrides the default template; copy the `{context_snapshot}` placeholder and any delegation guidance you still want if you maintain your own file.

### LaTeX Writer Agent

The `latex-writer` agent produces compilable `.tex` source files for consulting deliverables — field reports, test protocols, executive summaries, and design specifications. Invoke it inline from the Journaler or queue it as an Orchestrator task.

#### Output modes

| Keyword | Behaviour |
| --- | --- |
| `draft` (or none) | Full paragraph content with `\placeholder{}` for any missing data |
| `outline` / `skeleton` | Section headings + `\begin{itemize}` bullet stubs |
| `scaffold` | Section headings only with `% TODO:` comments |

#### Style and template selection

Every invocation accepts two optional flags that control the LaTeX preamble and section structure used by the agent:

| Flag | Effect |
| --- | --- |
| `--style <name>` | Load a named style profile from `latex-styles/<name>.yaml` |
| `--template <stem>` | Load a raw `.tex` preamble partial from `latex-templates/<stem>.tex` (overrides `--style` when both are given) |
| `--list-styles` | Return a listing of all available styles and templates without running the agent |

The selected preamble **replaces** the default `<preamble_template>` block in the agent's system prompt before the request is sent, so the agent uses it exactly. Any `section_structure` field in the style YAML is also injected as a hint that takes precedence over the default chapter/section skeleton.

**Available styles** (out of the box):

| Name | Class | Best for |
| --- | --- | --- |
| `consulting-report` *(default)* | `report` | Standard acoustic consulting deliverables — 1-inch margins, natbib, booktabs, siunitx |
| `executive-summary` | `article` | Client-facing 2–6 page summaries — lean packages, renamed abstract, `\section{}` only |
| `technical-spec` | `report` | Detailed specifications — enumitem requirement lists, cleveref, listings, single-spacing |

**Available preamble templates** (raw `.tex` partials):

| Stem | Description |
| --- | --- |
| `preamble-consulting` | Mirrors the `consulting-report` style as a reusable `.tex` file |
| `preamble-minimal` | Minimal compilable preamble for quick scaffolds |

#### Invocation examples

```text
# List what's available
/agent latex-writer --list-styles

# Default style (consulting-report)
/agent latex-writer draft report for ASTM E336 field test --project 12

# Named style
/agent latex-writer --style executive-summary draft exec summary for project 5
/agent latex-writer --style technical-spec scaffold STC-55 wall assembly spec

# Raw preamble template
/agent latex-writer --template preamble-minimal scaffold quick outline --project 7
```

#### Adding your own styles

Drop a new `.yaml` file into `latex-styles/` following the schema below — no code changes needed:

```yaml
name: my-style
display_name: "My Custom Style"
description: "One-line description shown by --list-styles"
document_class: report
class_options: "12pt,letterpaper"
template_file: null           # optional: stem of a file in latex-templates/
packages:
  - { name: geometry, options: "margin=1in" }
  - { name: booktabs }
  # ... additional packages ...
custom_commands:
  - '\newcommand{\placeholder}[1]{\textbf{\textcolor{red}{[INSERT: #1]}}}'
title_block:
  title: "TITLE"
  author: "FIRM NAME \\\\ Acoustic Engineering Consulting"
  date: "\\today"
section_structure: |
  Optional hint injected into the agent's system prompt describing
  the preferred chapter/section hierarchy for this style.
```

If `template_file` points to a `.tex` file in `latex-templates/` (e.g. `template_file: preamble-consulting.tex`), that file's content is used as-is and the `packages`/`custom_commands`/`title_block` fields are ignored. This is the fastest way to lock in an exact preamble you've already tuned.

Outputs land under `outputs/latex/` with a `.tex` extension. If `pdflatex` is on your `$PATH`, compilation is attempted automatically and a one-line validation summary is appended to the agent response.

### Org-Roam Write Skill

The Journaler's system prompt is enriched when the engine starts with a `## Workspace Layout` block that tells the model:

- The absolute paths of `org_roam_dir`, `workspace_dir`, and the daily journal directory
- Org-roam format conventions: `:PROPERTIES:/:ID:/END:` drawer, `#+title:`, `#+filetags:`, heading levels, `TODO`/`DONE` keywords, active (`<…>`) and inactive (`[…]`) timestamp formats, `CLOSED:` annotation
- The `@agent:` task syntax the Orchestrator picks up
- A **Draft reports (Markdown)** note: routing long-form prose to **`technical-writer`**, Markdown output, and typical **`outputs/docs/`** placement
- All available slash commands

Unless you override `system_prompt.txt`, the base Journaler role also asks the model to **propose concrete tasking paths** (inline `/agent`, journal `/task`, project id) when the user is heading toward written deliverables.

This means the model can suggest correctly-formatted org content in its responses, and the user can immediately write it with the corresponding slash command. The write functions (`org_writer.py`) enforce consistent formatting — UUID `:ID:` properties, `YYYYMMDDHHMMSS-slug.org` filenames, proper `CLOSED:` timestamps — regardless of what the model outputs.

### State Files

The Journaler writes to `<workspace_dir>/.journaler/`:

```text
.journaler/
├── state.json           # File mtimes for incremental scanning
├── context_cache.json   # Compressed rolling context snapshot
├── conversation.jsonl   # Full chat history log (all turns, including archived/compressed)
├── briefings/           # Generated morning briefings (YYYY-MM-DD.md)
└── daily_summaries/     # End-of-day conversation summaries (YYYY-MM-DD.md)
```

`conversation.jsonl` is append-only and serves as the permanent audit trail. Archived and compressed turns are written here even after the in-memory history is cleared, so any day's conversation can be reconstructed from the log. Use **`engineering-hub journaler export`** to turn this file into org-mode: by default a deterministic **raw** transcript (headings plus `#+begin_src text` blocks per turn) on **stdout**; with **`--summarize`**, a single model pass adds **`* Summary`** and **`* Open TODOs`** (`- [ ]` items). In **`journaler chat`**, bare **`/export`** (no `-o` / `--note` / `--find-title` / `--new-node`) writes a new roam node under **`conversation_exports/`** instead of printing into the session. Target an existing file with **`--note`** or **`--find-title`** (substring match on `#+title:` under `org_journal_dir`'s parent), or **`--new-node`** to create a new org-roam node under that roam directory. Override the transcript path with **`--jsonl`**. See **`engineering-hub journaler export --help`** for all flags.

### PDF reference corpus (vector DB / RAG)

The hub can attach **PDF reference chunks** from a pre-ingested database (`corpus.db`, produced by the **libraryfiles-corpus** project). Query-time embeddings use the same Ollama host and embed model as **`memory.*`** (`ollama.host`, `ollama.embed_model`). Enable and point at the DB in `config.yaml`:

```yaml
corpus:
  enabled: true
  db_path: "~/path/to/corpus.db"
  search_k: 5          # max chunks merged into the prompt
  threshold: 0.40      # minimum cosine similarity (0–1)
```

See [config/config.example.yaml](config/config.example.yaml) for the full commented block.

#### How retrieval is wired (two paths)

| Path | When it runs | Query text | Where chunks appear |
| --- | --- | --- | --- |
| **Journaler chat** (`journaler start` HTTP `/chat`, **`journaler chat`**) | Every normal user turn (not slash commands) | The **entire user message** is embedded and searched | Appended to the **system** prompt for that turn only (`ConversationEngine.chat`) |
| **Orchestrator agents** (`engineering-hub start`, `run-once`) | Each dispatched `@agent:` task after Django context is loaded | **`task.description`** plus optional **`task.context`** | Concatenated into the formatted context string after the memory block (`ContextManager` → `ContextFormatter`) |

**Not covered by these paths:** morning **`journaler briefing`**, **`journaler export --summarize`** (single-shot completion), and **`/agent`** delegation (see below).

#### `/agent` delegation and corpus

Inline **`/agent`** uses `AgentWorker` with an **empty** formatted project context string. That means **no** Django block and **no** PDF corpus block inside the delegated call, even if you pass **`--project`**. The `--project` flag is still stored on the task (for output paths and future use), but it does not trigger `ContextManager.format_for_agent` today.

**Practical implications:**

- For **PDF RAG + persona in one shot**, ask in **plain chat** first (corpus injects automatically), then optionally **`/agent`** for structured output if the answer already sits in the visible thread.
- For **Django + PDF RAG + agent prompt**, queue an **`@agent:`** line in the journal with **`[[django://project/<id>]]`** so the **Orchestrator** builds full context.

#### Best practices

1. **Keep Ollama and the embed model running** (`ollama pull <embed_model>`). If the embedder is down, corpus search returns nothing; startup logs warn when the DB is missing or the service is unavailable.
2. **Journaler turns:** The model embeds the **full** message. Prefer one focused question (or a short paragraph listing synonyms/acronyms) over a no-op greeting — otherwise similarity can be weak or noisy.
3. **Orchestrator tasks:** Put searchable substance in the **task line** (and extra phrasing in task `context` if your notes format supports it). The search does not see the whole journal entry, only the task fields passed into context build.
4. **Tune `search_k` and `threshold`:** Raise `threshold` if you get irrelevant chunks; raise `k` slightly if recall is too thin. Corpus defaults are slightly stricter than workspace memory (`memory.threshold` vs `corpus.threshold`).
5. **Budget:** In **`journaler chat`**, **`/status`** / **`/budget`** reports **Corpus injection** token usage so you can see when RAG is eating context.

#### Example prompts (Journaler chat)

Use wording that matches how references are written in your ingest (section titles, standard numbers, defined terms).

```text
What does ASTM E336 require for reverberation room volume qualifications?
Summarize the field measurement procedure for impact insulation class in the reference corpus.
IBC 1207.3 — occupant exposure limits and how they relate to NC curves.
Define "normalized impact sound pressure level" as used in our lab reports.
```

#### Example task lines (Orchestrator / journal)

Corpus query = task **description** + optional **context**:

```org
* Overnight Agent Tasks
- [ ] @standards-checker: Verify ASTM E1007-16 vs E1007-22 delta for tapping machine calibration [[django://project/42]]
- [ ] @research: IBC acoustical privacy requirements for adjacency between conference rooms and open offices
```

## Orchestrator: Task-Driven Agents

The Orchestrator watches your workspace for `@agent:` task lines — in **org mode**, both **daily journals** and the Journaler file **`pending-tasks.org`** (headings **`Overnight Agent Tasks`** / **`Pending Agent Tasks`** per config) — and dispatches them to specialized agents.

### Task Format (org-roam mode)

In your daily `.org` journal files under a `* Overnight Agent Tasks` heading:

```org
* Overnight Agent Tasks
- [ ] @research: Look up IBC 1207.3 amendments [[django://project/42]]
- [ ] @technical-writer: Draft response to reviewer comment #4
- [X] @research: Already completed task (skipped)
```

The Journaler maintains a separate queue file (default **`workspace_dir/.journaler/pending-tasks.org`**) with a **`* Pending Agent Tasks`** section. Tasks committed from **`journaler chat`** via **`/tasks commit`** use the same `- [ ] @agent:` checkbox line shape; the Orchestrator does not need changes to dispatch them. Optional **`:PROPERTIES:`** drawers (e.g. **`:SESSION_ID:`**) are for Journaler bookkeeping and rollback.

### Agent Types

| Agent | Purpose |
| --- | --- |
| `research` | Gather and synthesize technical information, summarize standards |
| `technical-writer` | Draft reports, protocols, and technical documentation |
| `standards-checker` | Verify compliance with ASTM/ISO standards |
| `technical-reviewer` | Review technical documents for accuracy |
| `latex-writer` | Produce compilable `.tex` source files with named style/template selection |

### Context pipeline diagnostic

Use this to verify what the Orchestrator actually passes into agents (Django block, memory/corpus/template sections) without relying on console DEBUG scrollback — DEBUG logs still only summarize retrieval; the harness **writes the full formatted string** to disk.

**CLI** (default task file: [diagnostics/context_pipeline_tasks.yaml](diagnostics/context_pipeline_tasks.yaml)):

```bash
engineering-hub diagnostic context-pipeline --tasks diagnostics/context_pipeline_tasks.yaml --max-tasks 10 -v
engineering-hub diagnostic context-pipeline --dry-run-context-only -v
engineering-hub diagnostic context-pipeline --context-audit-prompt -v   # append CONTEXT AUDIT block to system prompts
```

Global `-v` / `--verbose` enables DEBUG logging for the rest of the hub. Use `--docker` / `--no-docker` / `--llm-provider` like `run-once`.

**Artifacts** (per run): `{workspace_dir}/outputs/diagnostics/context-pipeline/<run_id>/` — each task folder contains `formatted_context.txt`, `task.json`, `checklist.json`, optional `corpus_audit_excerpt.jsonl`, and after execution `result.json` / `agent_response.md`. Run root has `summary.json`.

**While running `start` / `run-once`** (same artifact layout for each dispatched task), enable in `config.yaml`:

```yaml
diagnostics:
  context_pipeline:
    enabled: true
    context_audit_prompt: false   # optional: same as --context-audit-prompt
    debug_context_max_chars: 50000
```

Environment overrides: `ENGINEERING_HUB_CONTEXT_PIPELINE_DIAGNOSTIC_ENABLED`, `ENGINEERING_HUB_DIAGNOSTIC_CONTEXT_AUDIT_PROMPT`.

Operator playbook (parallel Cursor sub-agents, optional evaluator JSON): [diagnostics/RUNBOOK.md](diagnostics/RUNBOOK.md).

## Docker Container Execution

Agent tasks can run in isolated Docker containers instead of the host process. This provides resource limits, network isolation, and a clean execution environment for each task.

### Architecture

The system uses a hybrid model:

- **MLX tasks** always run on the host (requires Apple Silicon Metal — not available in Linux containers)
- **Anthropic tasks** can run in containers (HTTP API calls to Anthropic)
- **Ollama tasks** can run in containers (HTTP API calls to the Ollama service on the Docker network)

The Orchestrator and Journaler always run on the host. Only the agent task execution step is containerised.

### Quick Start

```bash
# 1. Start the Ollama service (provides local model inference to containers)
docker compose up -d

# 2. Pull a model into Ollama
docker compose exec ollama ollama pull llama3.1:8b

# 3. Build the task runner image
engineering-hub docker build

# 4. Run the orchestrator with Docker execution
engineering-hub start --docker --llm-provider ollama
```

### Configuration

Add a `docker:` section to your `config.yaml`:

```yaml
llm_provider: "ollama"

ollama:
  host: "http://localhost:11434"
  chat_model: "llama3.1:8b"

docker:
  enabled: true
  task_image: "engineering-hub-task:latest"
  network: "engineering-hub-net"
  cpu_limit: 2.0
  memory_limit: "2g"
  task_timeout: 300
  max_concurrent: 3
  ollama_host: "http://ollama:11434"
```

### CLI Commands

```bash
# Build the task runner Docker image
engineering-hub docker build

# Show Docker status (image, running containers, connectivity)
engineering-hub docker status

# Clean up stopped task containers
engineering-hub docker prune

# Override docker execution from CLI (regardless of config)
engineering-hub start --docker
engineering-hub run-once --docker --llm-provider ollama
engineering-hub start --no-docker   # force local even if config says docker
```

### How It Works

1. The Orchestrator's `TaskRouter` checks `docker_enabled` and `llm_provider`
2. For containerisable providers (Anthropic, Ollama), it serialises the task payload to JSON
3. `DockerExecutor` spawns an ephemeral container with:
   - The payload mounted read-only at `/task`
   - A writable `/output` volume for results
   - API keys injected via environment variables (never baked into the image)
   - CPU, memory, and timeout limits enforced by Docker
   - The `engineering-hub-net` Docker network for Ollama/API access
4. The `task_runner.py` entry point inside the container reads the payload, runs the LLM backend, and writes `result.json`
5. The host reads the result and continues the normal orchestrator flow (memory capture, roam wrappers, etc.)

### Networking

- **With Docker Compose Ollama**: Task containers reach Ollama at `http://ollama:11434` via the shared `engineering-hub-net` network
- **With host Ollama**: Task containers use `http://host.docker.internal:11434` (set `docker.ollama_host` accordingly)
- **Anthropic API**: Containers make outbound HTTPS calls to `api.anthropic.com`

## Development

### Running Tests

```bash
pytest
```

### Running the Mock Server

For development without the Django backend:

```bash
pip install -e '.[mock-server]'
uvicorn mock_server.main:app --reload
```

### Code Quality

```bash
ruff check src/
mypy src/
```

## Configuration Reference

See [config/config.example.yaml](config/config.example.yaml) for all available options:

- `llm_provider` - `"anthropic"` (cloud API), `"mlx"` (local Apple Silicon), or `"ollama"` (local/networked Ollama server)
- `django.api_url` - Django consultingmanager API endpoint
- `django.api_token` - API authentication token
- `anthropic.api_key` - Anthropic API key for Claude (Orchestrator; Journaler `/agent --backend claude` also accepts optional `journaler.anthropic_api_key` first)
- `anthropic.model` - Claude model to use (default: claude-sonnet-4-5-20250929)
- `workspace.dir` - Base workspace directory
- `mlx.model_path` - HuggingFace model ID for local MLX inference
- `ollama.host` - Ollama server URL (default: `http://localhost:11434`)
- `ollama.embed_model` - Embedding model (default: `nomic-embed-text`)
- `ollama.chat_model` - Generation model (required when `llm_provider: "ollama"`)
- `ollama.chat_timeout` - HTTP timeout for generation requests (default: 120s)
- `docker.enabled` - Run agent tasks in Docker containers (default: false)
- `docker.task_image` - Docker image for task containers (default: `engineering-hub-task:latest`)
- `docker.network` - Docker network name (default: `engineering-hub-net`)
- `docker.cpu_limit` / `docker.memory_limit` / `docker.task_timeout` - Resource limits per container
- `docker.max_concurrent` - Maximum parallel task containers (default: 3)
- `docker.ollama_host` - Ollama URL as seen from inside containers (default: `http://ollama:11434`)
- `journal.org_journal_dir` - Daily `YYYY-MM-DD.org` directory (Journaler uses this path directly; parent is the roam root for searches)
- `journaler.*` - Journaler daemon settings (model, scan interval, briefing, chat, Slack)
- `journaler.pending_tasks_file` - Org file for **`/tasks commit`** output (default: `workspace_dir/.journaler/pending-tasks.org`); Orchestrator scans it in org mode with daily journals
- `journaler.default_task_mode` - **`immediate`** (default: classifier may auto-delegate) or **`propose`** (**`DISPATCH:`** + confirm in CLI)
- `journaler.scan_org_roam_tree` - When false, scan only `journal.org_journal_dir` and `journaler.watch_dirs` (default: true)
- `journaler.watch_dirs` - Extra org directories to include in scans
- `journaler.journal_lookback_days` / `journaler.journal_max_files` - Window for parsing daily journals (defaults: 5 / 5)
- `journaler.model_profile` - Name of the active entry in `journaler.models` (when the map is non-empty)
- `journaler.models` - Optional map of named MLX profiles (`model_path`, `model_context_window`, sampling, `mlx_backend`, `enable_thinking`)
- `journaler.model_context_window` - Context window for pressure math when not using per-profile values (default: 32768)
- `journaler.agent_backend` - Backend for `/agent` delegation: `"mlx"` (default), `"claude"`, or `"auto"` (used by **`journaler start`** and **`journaler chat`**)
- `journaler.anthropic_api_key` - Optional per-journaler Anthropic key (falls back to `anthropic.api_key` / env if unset; same scope as `agent_backend`)
- `journaler.skills_dir` - Path to skills YAML directory (default: `skills/` at repo root; loaded into the system prompt for daemon and interactive chat)
- `journaler.context_management.*` - Token pressure thresholds, compression triggers, EOD reset time, topic-shift behavior
- `memory.*` - Vector memory settings (enabled, search_k, threshold)
- `corpus.enabled` - Enable PDF reference corpus RAG (requires `libraryfiles-corpus` and `corpus.db`)
- `corpus.db_path` - Path to `corpus.db` from libraryfiles-corpus ingest
- `corpus.search_k` / `corpus.threshold` - Max chunks and minimum similarity for corpus hits (defaults: 5 / 0.40)
- `diagnostics.context_pipeline.enabled` - Persist formatted context + results for each Orchestrator task under `outputs/diagnostics/context-pipeline/<run_id>/` (default: false)
- `diagnostics.context_pipeline.context_audit_prompt` - Append temporary CONTEXT AUDIT block to agent system prompts (default: false); env: `ENGINEERING_HUB_DIAGNOSTIC_CONTEXT_AUDIT_PROMPT`
- `diagnostics.context_pipeline.debug_context_max_chars` - Truncation cap for the extra DEBUG log of formatted context when diagnostics are enabled (default: 50000)
- CLI: `engineering-hub diagnostic context-pipeline` — run a YAML task suite with `--dry-run-context-only`, `--tasks`, `--max-tasks`, `--context-audit-prompt` (see [diagnostics/RUNBOOK.md](diagnostics/RUNBOOK.md))

## License

MIT
