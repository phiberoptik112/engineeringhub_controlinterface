# Engineering Hub Control Interface

A persistent, agent-first workspace enabling collaboration between engineers and AI agents on technical projects. Designed for acoustic engineering consulting workflows, connecting to a Django backend (consultingmanager) via REST API.

## Overview

Engineering Hub provides two complementary modes of AI collaboration:

1. **Orchestrator** (task-driven) -- watches your org-roam **daily journal** and the Journaler-owned **`pending-tasks.org`** queue for `@agent:` task lines, dispatches work to specialized agents via Claude API, local MLX models, or an Ollama server, and writes results back to the workspace. Optionally runs agent tasks in **Docker containers** for isolation.
2. **Journaler** (ambient) -- a persistent daemon that runs a local ~32B model via MLX, continuously monitors your org-roam workspace, delivers morning briefings, and responds to ad-hoc questions through **`engineering-hub journaler chat`** (readline), **`engineering-hub journaler tui`** (full-screen Textual interface with sidebar navigation and quick context loading), and an **HTTP** chat endpoint when the daemon is running.

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
- **Report Drafting Pipeline**: `/pipeline draft-section` chains technical-writer → standards-checker (loop-back) → technical-reviewer → latex-writer into a single command; receives pre-computed result tables from external scripts and produces a reviewed LaTeX section artifact — no calculation inside the pipeline
- **TUI Mode**: Full-screen Textual interface (`journaler tui`) with sidebar category navigation, clickable command cards, fuzzy command palette (`Ctrl+P`), and quick context loading panel (`Ctrl+L`) for journals, briefings, project notes, and prompt histories
- **Context Management**: Token-aware conversation history with automatic compression, topic-shift archival, end-of-day reset, and manual `/clear` controls — keeps the local model coherent across a full workday
- **Org-Roam Write Skill**: Journaler chat can write properly-formatted org-roam files — add TODOs, mark tasks done, append notes to today's journal (`/note`), set a session target on any roam note (`/open`), append under a heading there (`/edit`), search by title (`/find`), and create new nodes — via slash commands
- **Journaler Export**: CLI `journaler export` reads the persisted chat transcript (`conversation.jsonl`) and writes org-roam-friendly output to **stdout** by default (raw per-turn org, optional MLX **summary + open TODOs**); use `--note`, `--find-title`, `-o`, or `--new-node` for file targets. In **`journaler chat`**, bare **`/export`** writes under **`conversation_exports/`** in the configured org-roam root unless you pass one of those targets.
- **Zettelkasten Proposals**: Mine marked daily-journal ideas (`#idea`, `#extract`, `TODO extract`) into reviewable atomic-note proposal buffers, suggest conservative related links from memory, and apply approved notes into org-roam
- **Context File Loading**: Inject files or directories into the Journaler's live context (`/load`), switch into one-document technical-writing focus mode (`/focus`), or persist files into the memory store (`engineering-hub load`)
- **Vector Memory**: Local semantic memory (`memory.db`) with Ollama embeddings for past-task and ingest retrieval
- **PDF Reference Corpus**: Optional ingested reference corpus (`corpus.db` from **libraryfiles-corpus**) injected as RAG into **Journaler chat** turns and **Orchestrator** agent tasks (separate from workspace memory)
- **Context pipeline diagnostic**: Opt-in **`engineering-hub diagnostic context-pipeline`** command (and matching config/env flags) persist full formatted agent context, heuristic checklists, optional corpus audit excerpts, and agent outputs under `outputs/diagnostics/context-pipeline/<run_id>/` — see [diagnostics/RUNBOOK.md](diagnostics/RUNBOOK.md)

## Requirements

- Python 3.11+
- Access to Anthropic API (Claude), a local MLX model on Apple Silicon, or an Ollama server
- [Textual](https://textual.textualize.io/) ≥ 3.0 (installed automatically with `pip install -e .`)
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

# Full-screen TUI with sidebar navigation, command menus, and quick context loading
engineering-hub journaler tui

# Generate a morning briefing on demand
engineering-hub journaler briefing

# View the latest briefing
engineering-hub journaler briefing --latest

# Generate a Topics Discussion Briefing (multi-persona roundtable)
engineering-hub journaler briefing --discussion

# View the latest discussion briefing
engineering-hub journaler briefing --latest-discussion

# Run the Coordination Analyst scan on recent journal context
engineering-hub journaler briefing --coordination-scan

# Generate today's daily summary now (reads conversation.jsonl, writes daily_summaries/)
engineering-hub journaler summarize

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

# Run the report drafting pipeline for one section (no model needed for gathering)
engineering-hub journaler pipeline draft-section \
  --section "6.0 Noise Impacts" --project 42 --backend claude
engineering-hub journaler pipeline draft-section \
  --section "3.0 Existing Conditions" --project 42 --loop-limit 3
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
/load_recent                    Load the most recently created files across the workspace (default 5)
/load_recent 10 --days 7        Load up to 10 files created in the last 7 days
/load_recent --list             Preview recent files without loading them
/load_browse                    Interactive file browser (hidden files, size/date columns, / home search)
/files                          List currently loaded files (with sizes)
/files clear                    Remove all loaded files from context
/focus path/to/draft.md         Focus chat on one technical document only
/focus status                   Show active focus document and output target
/focus output path/to/out.md    Set the intended edited-output path
/focus off                      Leave focus mode and clear focus-mode turn history
/export                         Export transcript to `<org-roam>/conversation_exports/` (see below)
/export -o ~/path/to/out.org    Same flags as `engineering-hub journaler export`
/export --help                  Full `/export` flag list
/agent technical-writer ...     Delegate inline (see Agent Delegation below)
/agent_browse                   Browse and pick an agent skill interactively
/tasks …                        Overnight queue: list, confirm, commit, rollback (see below)
/queue <description>            Propose one task for the queue (then /tasks confirm && commit)
/history <query>                Retrieve prior chat excerpts from conversation logs
/history --agent panning-for-gold <query>
                                Have an agent review retrieved chat history
/skills                         List delegation skills / personas from skills/*.yaml
/open today                     Set /edit target to today's journal (or /open <path>, /open <title>)
/edit_browse                    Browse org-roam files to set /edit target
/edit Section :: body text      Append under a heading in the file opened with /open
/zettel propose                 Create reviewable atomic-note proposals from marked journals
/zettel apply path/to/batch.json Apply approved Zettelkasten proposals into org-roam
/help                           Show all slash commands
```

Inside `/load_browse`, press `/` to search supported files under your home directory, including supported dotfiles and files inside hidden dot folders. Results use the same multi-select controls as the normal org-roam browser.

`/load_recent [N] [--days D] [--list]` scans the whole workspace — the org-roam tree, agent `outputs/`, the `.journaler` state directory, `inputs/`, conversation exports, and zettelkasten proposals — ranks files by creation time (`st_birthtime`, falling back to mtime), and auto-loads the top `N` (default `journaler.load_recent_max_files`, 5) using the same per-file budget as `/load`. Pass `--list` to preview the ranked files without loading. Defaults are configurable via `journaler.load_recent_max_files`, `journaler.load_recent_days`, and `journaler.load_recent_roots`.

Supported extensions: `.md`, `.txt`, `.org`, `.py`, `.yaml`, `.yml`, `.json`, `.tex`, `.csv`, `.toml`, `.rst`, `.docx`, `.pdf` (DOCX and PDF are converted to markdown for context). PDF/DOCX conversion uses Docling when the optional `[docling]` extra is installed (better tables/layout, and OCR via `[docling-ocrmac]` for scanned PDFs), otherwise it falls back to `pypdf`/`python-docx`; a scanned/image-only PDF with no extractable text is rejected with a hint to install the OCR extra. Each `/load` is capped from your `journaler.model_context_window`, current conversation/history usage, and optional `journaler.load_*` keys in config (documented under **Journaler → Configuration** below). Oversized files are truncated with a notice. Directory loads share one remaining budget across files (recomputed after each file). Loaded files appear in the model's system prompt on every turn, count toward `/budget` and context pressure, and are cleared when the session ends.

`/focus <path>` is stricter than `/load`: it clears the in-memory chat history for the session, disables ambient Journaler context, corpus RAG, past-session retrieval, and natural-language auto-delegation, and uses only the focused document plus new focus-mode turns as context. This is intended for technical-writing passes where the goal is an edited document. Explicit `/agent technical-writer ...` still works while focus mode is active; the focused document is passed as the primary agent context. If the focused file is an org-roam `.org` note under the roam root, Journaler also sets it as the `/edit` target.

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
│   ├── context/         # Context building, formatting, and DataGatherer for agents
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
│   │   ├── models.py        # ContextSnapshot, ScanState, OrgEntry
│   │   └── tui/             # Full-screen Textual TUI interface
│   │       ├── app.py           # JournalerApp main application
│   │       ├── command_executor.py # Presentation-independent slash command dispatch
│   │       ├── load_tracker.py  # File access frequency persistence
│   │       ├── screens/         # Modal screens (palette, command input, sub-menus)
│   │       ├── widgets/         # Sidebar, command cards, chat view, status bar, context panel
│   │       └── styles/          # Textual CSS theming
│   ├── mcp/             # FastMCP server integration
│   ├── memory/          # Vector memory (SQLite + Ollama embeddings)
│   ├── notes/           # Journal/org-roam parsing and task dispatch
│   └── orchestration/   # Orchestrator, dispatcher, file watcher, AgentPipeline
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

- **Scans** org-roam (full tree or only `journal.org_journal_dir` plus optional `journaler.watch_dirs`) every 10 minutes using **content-hash incremental diff** (SHA-256 per file, canonical resolved paths in `state.json`). Mtime-only bumps (sync tools, metadata touches) are ignored. **Significant changes** — the lines logged on each tick — are limited to daily journal edits, `pending-tasks.org`, and `workspace/outputs/*.md`; roam-tree note updates are tracked quietly in scan stats only
- **Extracts** headings, TODO/DONE items, timestamps, and `@agent:` tasks from `.org` files
- **Reads** recent agent outputs from `memory.db` via `MemoryService.browse_recent()`
- **Compresses** everything into a rolling context snapshot (~4000 tokens)
- **Knows** the workspace layout and org-roam format conventions — injected into the system prompt when the conversation engine starts so the model can reason about file locations and produce valid org syntax
- **Loads agent personas** from `skills/*.yaml`: a concise **skills block** (display name, description, when-to-use, example `/agent` lines) is appended to the system prompt for **both** `journaler start` and **`journaler chat`**. On the daemon, each scheduled org-roam scan refreshes the rolling context snapshot **and re-attaches** that skills block so personas are not dropped mid-run
- **Uses** `journaler.agent_backend`, optional `journaler.skills_dir`, and optional `journaler.anthropic_api_key` (else `anthropic.api_key` / `ENGINEERING_HUB_ANTHROPIC_API_KEY`) for delegation — same resolution for daemon and interactive chat
- **Generates** a morning briefing at a configurable time (default 9:00 AM), with concise 2-3 sentence items that emphasize trends across the journal window and an extra **pending-tasks.org** summary when recent queue timestamps appear in that file. Pending/stale/completed tasks are rebuilt from the full journal lookback window (plus recent org-roam project notes) on every scan, with source file/date provenance in briefing context; prose lines like “finished X” can mark tasks complete when `prose_completion_detection` is enabled (default). When `journaler.briefing_append_to_journal` is enabled (default), the report is also upserted under `* Morning Briefing` in today's org journal (`journal.org_journal_dir`, default `~/org-roam/journal/YYYY-MM-DD.org`).
- **Runs a proactive topic scout** on significant scan ticks (journal / pending-tasks / output changes): one light MLX call writes `topic_hints/YYYY-MM-DD.md` and injects **Topic hints (auto)** into the live system prompt for HTTP chat
- **Delegates** scheduled coordination scans through the same `AgentDelegator` as `/agent` (local MLX by default, with corpus/memory when configured)
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
  briefing_append_to_journal: true   # upsert morning/discussion reports into today's org journal
  # scan_org_roam_tree: true        # false = only journal.org_journal_dir + watch_dirs (faster; significance is content-based either way)
  # journal_lookback_days: 30
  # journal_max_files: 30
  # watch_dirs: []
  # conversation_lookback_days: 7   # how many past daily summaries appear in the proactive snapshot
  # org_link_on_relation: true      # write a cross-reference into today's journal on semantic match
  chat_enabled: true
  chat_host: "127.0.0.1"
  chat_port: 18790
  slack_enabled: false
  slack_webhook_url: ""  # or set JOURNALER_SLACK_WEBHOOK env var
  max_conversation_history: 20
  max_tokens: 4096
  # Extra token budget for <think> reasoning blocks on thinking models
  # (Qwen3.x). Thinking tokens don't count against max_tokens; clamped to
  # remaining context headroom per turn. Set 0 to disable.
  max_thinking_tokens: 8192

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
    capture_daily_to_memory: true  # write daily summaries to memory.db (required for relation detection)
    reserved_for_generation: 4096  # tokens held back for model output (≥ journaler.max_tokens)

  # Optional — slash /load size limits (see config.example.yaml for defaults)
  # load_max_context_fraction: 0.40   # fraction of remaining context per file chunk
  # load_max_chars_absolute: 200000
  # load_min_chars: 1024
  # load_slack_tokens: 256

  # Topics Discussion Briefing — multi-persona roundtable (disabled by default)
  # discussion_briefing_enabled: false
  # discussion_briefing_time: "08:45"    # runs before morning briefing
  # personas_dir: "~/org-roam/engineering-hub/personas"  # default: personas/ at repo root
  # discussion_persona_lookback_days: 7  # days of per-persona history to inject
  # discussion_max_tokens_per_persona: 1024

  # Coordination Analyst scheduled scan (disabled by default)
  # coordination_scan_enabled: false
  # coordination_scan_interval_min: 0    # 0 = disabled; e.g. 240 = every 4h

  # Proactive topic scout — one-shot MLX hint when a scan tick detects journal changes
  # proactive_topic_scout_enabled: true
  # proactive_topic_scout_max_tokens: 512

  # Background agent work loop (disabled by default)
  # background_work_enabled: false
  # background_work_interval_min: 60
  # background_work_max_tasks_per_day: 6
  # background_work_auto_approve: false
  # background_work_agent_backend: "mlx"
  # background_work_chat_lookback_days: 3
```

`model_path` is optional: if omitted, the Journaler falls back to `mlx.model_path` (the orchestrator MLX path), then to a built-in default (`mlx-community/gemma-4-31b-it-8bit`). Use `journaler download` after changing paths.

#### Model profiles and thinking mode

You can define **named profiles** under `journaler.models` and select one with `journaler.model_profile`. Each profile sets `model_path`, optional `model_context_window`, sampling (`temp`, `top_p`, …), `mlx_backend` (`auto`, `mlx-lm`, or `mlx-vlm`), and **`enable_thinking`** for Qwen3-style chat templates (`null` = omit the argument for models like Gemma; `true` / `false` toggles reasoning blocks on supported tokenizers).

Thinking models get a **separate reasoning budget** via `max_thinking_tokens` (default 8192, per profile or top-level): tokens inside `<think>...</think>` blocks draw from this budget instead of `max_tokens`, so a long reasoning phase can't consume the answer budget and truncate the reply mid-thought. Templates that prime the assistant turn with `<think>` (Qwen3.5/3.6) are detected automatically. The budget is clamped to the remaining context headroom each turn. If generation still hits a limit, the Journaler appends an explicit truncation notice instead of stopping silently, and reasoning transcripts are stripped from rolling conversation history so they don't bloat the context window. Delegated `/agent` tasks on the local MLX backend run with a larger 8192-token answer budget (long-form deliverables) plus the same thinking budget.

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
      max_thinking_tokens: 8192    # optional; extra <think> budget on top of max_tokens
      thinking_max_tokens: 16384   # optional; omit to use journaler.thinking_max_tokens floor
```

**Thinking mode token budget:** Qwen3 thinking models emit a long internal reasoning block before the visible answer. Both share a single `max_tokens` cap passed to MLX. When `enable_thinking: true`, Journaler automatically uses at least **`journaler.thinking_max_tokens`** (default **16384**) unless the profile sets `thinking_max_tokens` or you raise it live with `/model set max_tokens <n>`. Check `/model` for **effective max_tokens** when thinking is on. Higher output budgets also increase **reserved for generation** in `/budget`, leaving less headroom for `/load` and history.

#### Output limits: summarize / continue / stop

A response is "truncated" when generation hits the per-pass cap or runs out of context headroom — `prompt_tokens + generated_tokens` cannot exceed the model's context window, so a near-full window leaves little room to generate. With verbose/thinking models this often cuts off **inside the reasoning block**, before the deliverable is even written. The `journaler.output_limit` block makes this model agnostic and lets you control recovery.

```yaml
journaler:
  output_limit:
    policy: prompt          # prompt | auto_continue | auto_summarize | stop
    max_output_tokens: 8192       # per-pass budget (clamped to real headroom)
    max_continuation_passes: 3    # cap on same-turn continuation passes
    continue_mode: ask            # ask | same_turn | follow_up
    summarize_scope_default: history   # history | history_and_partial
    force_answer_on_thinking_cut: true # thinking-phase cut -> jump to final answer
    summarize_before_continue: true    # reclaim headroom before each continue pass
```

**Policies**

- `prompt` (default) — when output is incomplete, interactive chat shows a menu: summarize history (`s`), summarize history + partial answer (`S`), continue same turn (`c`), continue as a follow-up turn (`f`), or stop (Enter).
- `auto_continue` — automatically continues across additional passes until complete or `max_continuation_passes` is hit.
- `auto_summarize` — compresses context (per `summarize_scope_default`) and retries.
- `stop` — keeps the partial output with a one-line notice.

**Natural continuation.** Continuation is phase-aware. If the cut happened while the model was still *thinking*, Journaler does not ask it to "keep going" (which would resume deliberating) — it summarizes the values already established and instructs the model to **write the final answer now** (`force_answer_on_thinking_cut`). If the cut happened mid-answer, it resumes from the exact seam and de-duplicates the overlap so the joined output reads as one continuous response. Before each continue pass it reclaims context headroom (`summarize_before_continue`) so the next pass actually has room to generate. Open code fences are carried across the seam.

**Runtime control.** Use `/output` to see the active policy and `/output set <key> <value>` to change it live (`policy`, `max_tokens`, `passes`, `continue_mode`, `summarize_scope`). The status bar shows a `Gen: ~N` estimate of remaining generation headroom alongside context utilization, and `/budget` lists **Generation headroom (est.)**. Over the HTTP chat endpoint (no stdin), `policy: prompt` falls back to `stop`; use an auto policy for unattended use.

Switching models at runtime (without restarting):

- **Interactive chat:** `/model` (status), `/model_browse` (picker for mlx-community cache + profiles), `/model reasoning` (named profile), `/model path <hf-id-or-path>` (one-off path). Cache folder names like `models--mlx-community--…` are normalized automatically when pasted after `/model path`.
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

# Run the report drafting pipeline via the daemon
curl -X POST http://localhost:18790/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "/pipeline draft-section --section \"6.0 Noise Impacts\" --project 42 --backend claude"}'

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

#### Using `tmux` (recommended for development and quick restarts)

[`tmux`](https://github.com/tmux/tmux/wiki) is a terminal multiplexer that lets you run long-lived processes in the background, reattach to them later, and keep them running if your terminal closes (without needing to deal with full system startup scripts).

Here's how you can manage the Journaler daemon with `tmux`:

**Start the Journaler in a new tmux session:**
```bash
tmux new-session -d -s journaler 'engineering-hub journaler start'
```
- `-d` starts it detached (in the background).
- `-s journaler` names the tmux session for easy reference.

**Reattach to the tmux session to see logs or interact:**
```bash
tmux attach-session -t journaler
```

**Detach and leave it running:**  
Press `Ctrl+b`, then `d` (this sends you back to your regular terminal, leaving the journaler running).

**See all running tmux sessions:**
```bash
tmux ls
```

**Stopping the Journaler:**
1. Reattach (`tmux attach-session -t journaler`)
2. Press `Ctrl+C` to stop the command, then close the session with `exit`
3. Or kill it directly: `tmux kill-session -t journaler`

For always-on operation, use a launchd plist at `~/Library/LaunchAgents/com.engineeringhub.journaler.plist` with `KeepAlive` and `RunAtLoad` set to true.

### Context Management

The Journaler runs all day, and a 32B model's context window fills up over hours of conversation. Six layered strategies keep the model coherent without manual restarts:

| Strategy | When it fires | What it does |
| --- | --- | --- |
| **Rolling window** | Always | Keeps the last N turns; evicts oldest non-preserved turns to the JSONL log |
| **Compression** | Window ≥ 70% full | Asks the model to summarize earlier turns into a ~200-word paragraph; replaces them with a single preserved system message |
| **Emergency trim** | Window ≥ 90% after compression | Force-drops to the last 3 turns |
| **Topic-aware clear** | Topic shift detected (3 consecutive on-topic messages) | Archives the old topic, starts fresh with the new one |
| **End-of-day reset** | Scheduled (`end_of_day_time`, default 15:30) or `/summarize` on demand | Compresses the full day, saves to `daily_summaries/YYYY-MM-DD.md`, resets history |
| **Manual clear** | `/clear` command | User-controlled: soft, compress-then-clear, or full reset |

### Daily Summary Context Loop

Daily summaries are generated automatically at `end_of_day_time` (default **15:30**, configurable) and feed back into every future session through two complementary paths. You can also trigger a summary at any time:

- **Inside `journaler chat`:** type `/summarize` — generates the summary immediately from the current session's history and archives it.
- **From the terminal:** `engineering-hub journaler summarize` — reads today's turns from `conversation.jsonl`, generates the summary, and writes it without needing an active chat session.

To change the scheduled time, set `end_of_day_time` in your config:

```yaml
journaler:
  end_of_day_time: "15:30"   # HH:MM local time
```

Or via environment variable: `ENGINEERING_HUB_JOURNALER_END_OF_DAY_TIME=15:30`.

**Proactive path — snapshot injection (every 10-min tick)**

The last `conversation_lookback_days` (default: 7) summaries from `daily_summaries/` are always included in the system prompt under `### Recent Conversation Summaries`. The model sees a temporal narrative of past sessions at all times — no embeddings required, zero latency.

**Reactive path — per-turn semantic match**

On every chat turn, the user's message is semantically searched against embedded daily summaries in the memory database (`source="journaler"`). When a match exceeds `conversation_relation_threshold` (default: 0.70), the matched summary is injected as a `### Related Past Conversation` block and the model is explicitly instructed to call out the relationship:

```
This relates to the April 15 conversation where we discussed [topic]...
```

If `org_link_on_relation: true` (default), a cross-reference link is automatically appended to today's journal under a `* Journaler Cross-References` heading, building a lightweight relation graph over time. Links use org-mode `[[file:…]]` syntax and open the related day's daily journal (`YYYY-MM-DD.org`) when that file exists, otherwise the Journaler daily summary (`.journaler/daily_summaries/YYYY-MM-DD.md`). Each line includes a longer excerpt of the matched summary (default 400 chars via `context_management.org_link_excerpt_chars`). Duplicate references for the same date are skipped.

**Morning briefing — Continuing Threads**

The morning briefing gains a `### Continuing Threads` section: recurring topics from the org scan are cross-referenced against the memory database, surfacing past conversations that overlap with today's themes.

> **Prerequisite:** The reactive path and briefing threads require `MemoryService` (Ollama running locally) and `capture_daily_to_memory: true` (now the default). The proactive file-based path works without embeddings.

When the engine takes an automatic action (compression, topic shift), a bracketed note is prepended to the model's response:

```
[Context compressed: freed 3,200 tokens from 12 earlier exchanges]

Journaler: Project 42 is active. You have two pending tasks...
```

Set `notify_user_on_action: false` in `context_management` to suppress these notes.

### TUI Mode (Full-Screen Interface)

The TUI provides a full-screen Textual-based interface with persistent navigation, categorized command menus, and a quick context loading panel. Launch it with:

```bash
engineering-hub journaler tui
```

#### Layout

- **Left sidebar** — category tree with all slash command groups (Quick Context, Context Management, File Ops, Agent Delegation, Zettelkasten, Capture Templates, Org-Roam Write, Export, Session)
- **Main panel** — switches between chat view, command card grids, and the quick context panel
- **Bottom bar** — model name, utilization gauge, turn count, topic indicator
- **Input bar** — type messages or slash commands directly

#### Key bindings

| Key | Action |
| --- | --- |
| `Ctrl+P` | Open the fuzzy-filter command palette (search all 30+ commands by name, description, or category) |
| `Ctrl+L` | Open the Quick Context panel (load journals, briefings, project notes, history) |
| `Ctrl+Q` | Quit the TUI |
| `Escape` | Return focus to the chat view |

#### Quick Context Panel

The Quick Context panel (`Ctrl+L`) provides one-click loading of frequently accessed content across four tabs:

| Tab | Contents |
| --- | --- |
| **Journals** | Last 14 daily journal entries from `org_journal_dir` |
| **Briefings** | Recent morning and discussion briefings from `.journaler/briefings/` |
| **Projects** | Top 15 most recently modified `.org` files outside the journal directory |
| **History** | Daily conversation summaries and the current session log |

Click any item to load it into the conversation context. Files already loaded are marked with a visual indicator.

#### Load Tracker (access frequency)

The TUI tracks how often you load each file and surfaces frequently-used content first. Access data persists across sessions in `.journaler/load_tracker.json`. Files can be pinned to always appear at the top.

The same feature is available in `journaler chat` via the `/context` slash command:

```text
/context              Show top 10 suggested files by frequency/recency
/context 3            Load file #3 from the suggested list
```

#### Command cards

Selecting a category from the sidebar shows a grid of command cards. Each card displays the command name, arguments hint, and description. Click or press Enter to:
- Execute immediately (no-arg commands like `/status`, `/skills`, `/budget`)
- Open a sub-menu with options (e.g., `/clear` shows soft/summarize/hard choices; `/agent` opens a persona picker; `/export` shows format options)

#### Usage examples

```bash
# Launch the TUI (same model setup as journaler chat)
engineering-hub journaler tui

# Use with a specific model profile
engineering-hub journaler --profile reasoning tui
```

Inside the TUI:
1. Press `Ctrl+L` to open Quick Context, select a recent journal to load
2. Type a question in the input bar to chat with the Journaler
3. Click "Agent Delegation" in the sidebar to see all agent commands as cards
4. Press `Ctrl+P`, type "export", and select `/export` to export the conversation
5. Use the sidebar "Org-Roam Write" category to access `/task`, `/note`, `/open`, etc.

---

### Interactive Chat: Slash Commands

While in `engineering-hub journaler chat`, any input starting with `/` is handled as a command rather than forwarded to the model:

**Context management**

| Command | Description |
| --- | --- |
| `/clear` | Soft clear: archive conversation history, keep context snapshot |
| `/clear --summarize` | Compress history into a summary, then clear |
| `/clear --hard` | Full reset: clear conversation and wipe scan state |
| `/summarize` | Generate today's daily summary now, write it to `daily_summaries/YYYY-MM-DD.md`, and archive history |
| `/status` | Show context pressure, utilization %, turn count, and current topic |
| `/budget` | Show full token budget breakdown (system prompt, snapshot, history, available) |
| `/topic` | Show the currently detected conversation topic |

**Quick context loading**

| Command | Description |
| --- | --- |
| `/context` | Show top 10 suggested files ranked by load frequency and recency |
| `/context <number>` | Load the Nth file from the suggested list into conversation context |

**Model switching** (requires `journaler.models` in config for profile names)

| Command | Description |
| --- | --- |
| `/model` | Show active model path, profile name, context window, token budgets (`max_tokens` + thinking), `enable_thinking`, and `mlx_backend` |
| `/model_browse` | Interactive picker for configured profiles and cached `mlx-community/*` models (chat REPL and TUI) |
| `/model <profile>` | Load the named profile from `journaler.models` (keeps chat history) |
| `/model path <id-or-path>` | Load a Hugging Face repo id or local MLX snapshot path |

**File loading**

| Command | Description |
| --- | --- |
| `/load <path>` | Load a file or directory into the current conversation context |
| `/load <path> -r` | Load a directory recursively |
| `/load_recent [N] [--days D] [--list]` | Load the most recently created files across the workspace (default 5); `--list` previews without loading |
| `/load_browse` | Interactive fullscreen file browser for org-roam — arrow keys to navigate, Space to multi-select, Enter to load |
| `/files` | List all files currently loaded, with character counts |
| `/files clear` | Remove all loaded files from context |

**Conversations** (`journaler.conversations.enabled`, default true)

| Command | Description |
| --- | --- |
| `/convo` | Interactive picker (chat/TUI only) — switch or create conversations grouped by project/topic |
| `/convo new [--project N] [--topic L] <title>` | Create and switch to a new conversation |
| `/convo list` | Text listing grouped by project then topic |
| `/convo status` | Active conversation id, turns, remembered files |
| `/convo <id\|title-fragment>` | Jump to a matching conversation |
| `/convo rename <title>` | Rename the active conversation |
| `/convo restore-files` | Re-load the active conversation's remembered file paths |
| `/convo archive [id]` | Hide from picker (JSONL retained) |
| `/convo delete <id> --confirm` | Delete metadata and store |

**Export transcript** (same pipeline as `engineering-hub journaler export`; default file target differs in chat)

| Command | Description |
| --- | --- |
| `/export` | Export `.journaler/conversation.jsonl` to a new `.org` file under `<org-roam>/conversation_exports/` |
| `/export …` | Flags: `--jsonl`, `--summarize`, `-o` / `--output`, `--note`, `--heading`, `--find-title`, `--new-node` (shell-style quoting supported) |
| `/export --help` | Print usage |

**Agent delegation**

| Command | Description |
| --- | --- |
| `/agent <type> <desc> [--project <id>] [--backend mlx\|claude]` | Delegate a task to a named agent and get the result inline. Types: `research`, `technical-writer`, `standards-checker`, `technical-reviewer`, `weekly-reviewer`, `latex-writer`, `zettelkasten-curator`, `rental-scout`, `blender`, `horn-iterator` |
| `/horn [sweep\|defaults] [--export csv\|org]` | Run the parametric horn sweep or show LVT defaults (local compute, no LLM) |
| `/history <query>` | Retrieve matching excerpts from prior Journaler chat logs (`conversation.jsonl`) and daily summaries |
| `/history --agent <type> [--backend mlx\|claude] <query>` | Dispatch retrieved prior-chat excerpts to a named agent for review, synthesis, or extraction |
| `/pipeline draft-section --section "<section>" [--project <id>] [--backend mlx\|claude] [--loop-limit <n>]` | Run the multi-stage report drafting pipeline — gathers pre-computed result files, drafts prose, audits compliance, reviews tone, and emits LaTeX; see [Report Drafting Pipeline](#report-drafting-pipeline) |
| `/agent_browse` | Interactive skill picker — arrow keys to browse agents, Enter to select, then type a task description |
| `/skills` | List all available agent delegation skills with descriptions and examples |
| `/integrate` | Run a Task-Integrator cycle now: interview inline in today's journal and propose approval-gated `@agent:` tasks; `/integrate status` shows awaiting/proposed/queued counts |
| `/tasks` | Show session queue proposals, or use `/tasks confirm`, `/tasks commit`, `/tasks reject N`, `/tasks edit N <text>`, `/tasks clear`, `/tasks rollback [N \| --all]` |
| `/queue <description>` | Shorthand to propose one overnight task (defaults agent to `research` until you edit/confirm); then `/tasks confirm` and `/tasks commit` |

**Zettelkasten proposals**

| Command | Description |
| --- | --- |
| `/zettel propose [days]` | Mine marked daily journals for reviewable atomic-note proposal buffers |
| `/zettel apply <proposal-json>` | Apply approved notes from a proposal batch into the org-roam directory |
| `/zettel status` | Show processed source spans and proposal batch count |

**Org-roam write operations**

| Command | Description |
| --- | --- |
| `/task <description>` | Add `- [ ] <description>` to today's journal under `* Overnight Agent Tasks` |
| `/done <fragment>` | Mark the first matching `- [ ]` item as `- [X]` with a `CLOSED:` timestamp |
| `/timesheet <hours> project "<project>" :: <description>` | Log hours to today's journal under `* Timesheet`, grouped by project |
| `/timesheet <hours> --project "<project>" --desc "<description>"` | Same as above, using flag syntax; add `--project-id <id>` to create a `django://project/<id>` link |
| `/timesheet export --month YYYY-MM --project "<project>"` | Export a final monthly timesheet org file from the configured template |
| `/note <heading> :: <text>` | Append text under a heading in today's journal (creates the heading if absent) |
| `/open` | Print the current `/edit` target path, if any |
| `/open clear` | Clear the session edit target |
| `/open today` | Set the edit target to today's daily journal (creates the file if missing) |
| `/open <path>` | Set target to an existing `.org` file; path must resolve under the configured org-roam directory |
| `/open <title fragment>` | Set target when exactly one file matches `#+title:` (substring, case-insensitive); otherwise list matches for disambiguation |
| `/edit <heading> :: <text>` | Append text under a heading in the note opened with `/open` (same ` :: ` delimiter as `/note`) |
| `/edit_browse` | Interactive file browser to set the `/edit` target — browse `.org` files, Enter to select |
| `/find <title fragment>` | Search all org-roam files for a case-insensitive `#+title:` match; prints matching paths |

`/timesheet` writes an append-only line such as `- [2026-05-07 Thu 22:55] 2.00h :: report drafting` under `* Timesheet` → `** Project X` in the daily journal. It also maintains an agent-searchable org-roam note at `<org-roam>/timesheets/timesheet-reference.org` tagged `:timesheet:agent-context:worklog:`; that reference groups entries by project, adds project heading tags such as `:project_42:`, and links back to the daily journal plus `django://project/<id>` when `--project-id` is provided. Each log also mirrors into a **monthly working note** at `<org-roam>/timesheets/YYYY-MM-<project-slug>.org` with `* Hours`, `* Notes`, and `* Review` sections for org-native review and `/edit`. The existing `/capture contracting-hours ...` template remains available when you want one org-roam node per contracting-hour entry instead.

**Monthly review chat flow (org-native)**

1. Log during the month with `/timesheet` (include `--project-id` when the project maps to Django).
2. Open the working note: `/open` with a title fragment such as `2026-07 Timesheet` or the path under `timesheets/`.
3. Discuss and edit in chat: `/edit Notes :: …`, `/edit Review :: …`, or delegate `/agent timesheet-reviewer reconcile July hours for project 42`.
4. Export the final client-facing month: `/timesheet export --month 2026-07 --project "LVT Phase B" --project-id 42`.
5. Optional: set `journaler.timesheet_export_template` in YAML to a customized org template (default: `timesheet_templates/monthly.org` in the repo).

Exported finals are written to `<org-roam>/timesheets/exports/YYYY-MM-<project-slug>-final.org` unless `-o <path>` is provided. The template uses `${placeholder}` fields (project, month, totals, entry lines/table, notes, review) filled from the monthly working note and reference ledger.

Examples:

```text
# Interactive journaler chat
/timesheet 2 project "Project X" :: report drafting and client coordination
/timesheet 0.5 --project "Project X" --desc "follow-up email and action items"

# Link the entry to a Django project reference
/timesheet 1.25 --project "LVT Phase B" --project-id 42 --desc "reviewed test data and updated report outline"

# Numeric project shorthand; logs under "Project 42" and links django://project/42
/timesheet 0.75 project 42 :: prepared field measurement checklist

# Export final monthly timesheet (template from journaler.timesheet_export_template)
/timesheet export --month 2026-07 --project "LVT Phase B" --project-id 42
/timesheet export --month 2026-07 --project "LVT Phase B" --template ~/templates/monthly.org -o ~/org-roam/timesheets/exports/custom-final.org
```

The persistent reference note is intentionally separate from the daily journal so agents can search across accumulated time logs by project, tags, and links. A project-linked entry in `<org-roam>/timesheets/timesheet-reference.org` looks like:

```org
* Timesheet
** [[django://project/42][LVT Phase B]] :project:project_42:
- [2026-05-07 Thu 22:55] 1.25h :: reviewed test data and updated report outline
  - Daily journal: [[file:/path/to/org-roam/journals/2026-05-07.org][2026-05-07.org]]
  - Project link: [[django://project/42][LVT Phase B]]
```

The same command works through the daemon HTTP `/chat` endpoint:

```bash
curl -X POST http://localhost:18790/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "/timesheet 2 --project \"Project X\" --desc \"report drafting\""}'
```

**General**

| Command | Description |
| --- | --- |
| `/help` | Show the full list of available slash commands |

Tasks added with `/task` use the `- [ ] @agent:` format understood by the Orchestrator, so they will be picked up and dispatched automatically. **`/tasks commit`** writes confirmed proposals to **`pending-tasks.org`** (path: `journaler.pending_tasks_file`, default **`workspace_dir/.journaler/pending-tasks.org`**), which the Orchestrator also scans in org mode. `/agent` runs immediately and returns output in the chat turn.

With **`journaler.default_task_mode: immediate`** (default), ordinary messages that describe agent work may be **classified** and **delegated inline** (no `/agent` prefix) unless you use explicit **queue** language (“run later”, “queue for tonight”, …) or **`/queue`**. With **`default_task_mode: propose`**, that auto-path is off; the model uses **`DISPATCH:`** lines and you confirm before the agent runs (interactive chat prompts **Run it? [y/N]**; HTTP `/chat` still auto-runs a `DISPATCH` after the model responds, as before).

**`/model`** and **`/model_browse`** in interactive chat reload the MLX weights but **keep the delegator’s adapter in sync**, so `/agent --backend mlx` continues to use the active checkpoint (same behavior as HTTP `/chat` for `/model`). **`/export`**, **`/open`**, **`/edit`**, bare **`/convo`** (picker), and the **`/load_browse`** / **`/agent_browse`** / **`/edit_browse`** / **`/model_browse`** TUIs are in interactive **`journaler chat`** and **`journaler tui`**; the HTTP endpoint handles **`/model`**, **`/agent`**, **`/convo`** (text subcommands only, no picker), **`/tasks`**, **`/queue`**, **`/timesheet`**, and **`/skills`**. **`/convo`** switches named conversations with per-convo JSONL history; loaded files reset on switch unless you **`/convo restore-files`**. **`/history`** searches prior chat excerpts; **`/convo`** switches the active session.

To persist files for long-term retrieval across sessions, use `engineering-hub load` instead (see [Load Files into Context](#6-load-files-into-context)).

### Agent Delegation

The Journaler can delegate tasks directly to any named agent personality and return the result inline in the chat conversation — no need to write a journal task and wait for the overnight Orchestrator run.

**Modes (see `journaler.default_task_mode` in config):**

- **`immediate`** — Prefer inline execution: a small structured classifier may route suitable user messages to **`AgentDelegator`** automatically. Explicit **queue** phrasing or **`/queue`** adds a **proposal** to the session planner instead (confirm with **`/tasks`** before **`commit`**).
- **`propose`** — Same as the earlier **DISPATCH** flow: the model suggests **`DISPATCH: /agent …`** and you confirm before running (interactive CLI); corpus and loaded files still apply to **`/agent`** and to delegated runs.

**Overnight queue:** Use **`/queue <description>`** or natural language that clearly defers work, then **`/tasks confirm`** and **`/tasks commit`** to append to **`pending-tasks.org`**. Roll back with **`/tasks rollback`**. The Orchestrator picks up unchecked items there on its next scan; completed tasks are moved under **`* Completed Agent Tasks`** in that file when the run finishes.

#### The `/agent` command

```text
/agent <type> <description> [--project <id>] [--backend mlx|claude] [--web|--no-web]
```

| Argument | Description |
| --- | --- |
| `<type>` | Agent personality: `research`, `technical-writer`, `standards-checker`, `technical-reviewer`, `weekly-reviewer` |
| `<description>` | Free-text task description |
| `--project <id>` | Optional Django project ID (stored on the task; does **not** load Orchestrator-style Django + PDF corpus into the delegated prompt — use journal `@agent:` + `[[django://project/id]]` for that) |
| `--backend mlx` | Use the local MLX model (reuses the Journaler's loaded model — no extra RAM) |
| `--backend claude` | Use the Claude API (requires `journaler.anthropic_api_key` or global `anthropic.api_key` / env) |
| `--web` | Query the configured local web search provider (SearXNG by default) and inject bounded results into the delegated prompt |
| `--no-web` | Disable web search for this invocation, even when `agent_web_search.enabled` is true |

The default backend is controlled by `journaler.agent_backend` in config (`"mlx"` uses the local model; set `"auto"` if you want Claude when a key is present, otherwise MLX). The `--backend` flag overrides this per-command.

Web search is local-first. The host process queries SearXNG, formats titles, URLs, snippets, and metadata as a `## Web search results (SearXNG)` context block, then the selected agent backend synthesizes from that block. With `--backend mlx --web`, reasoning stays on the resident MLX model; SearXNG only supplies retrieved snippets. If local search fails, `--web` stops the command unless `agent_web_search.anthropic_backup_enabled` is true and the selected backend resolves to Claude, in which case Anthropic server-side web search may be used as a fallback.

For **draft reports, protocols, executive summaries, and other client-facing Markdown deliverables**, use the **`technical-writer`** persona. The default Journaler system prompt and workspace layout describe **available agents**, **immediate vs queue** behavior, and practical routes: **`/agent technical-writer …`**, natural-language delegation in **immediate** mode, queue **`/task`** / journal lines with `@technical-writer:`, **`/queue`** + **`/tasks commit`** for **`pending-tasks.org`**, optional **`--project <id>`** for Django context, and **`/skills`** for full persona text. Delegated technical-writer runs use `prompts/technical-writer.txt`; saved artifacts often land under **`outputs/docs/`**.

#### Agent Reference

Each agent has a defined scope, output format, and set of practical invocations. Use `/skills` in the chat to see the live list.

---

**`research`** — Gather, synthesize, or summarize technical information from standards, prior reports, or external sources.
Output: markdown research document under `outputs/research/`.

```text
/agent research IBC 1207.3 occupant comfort requirements for multifamily --project 42
/agent research Compare ASTM E336-17a vs E336-23 changes to flanking path requirements
/agent research Find current FAA guidance on vertiport noise criteria --web
/agent research What STC ratings does IBC 1207.3 require for hotel guest rooms
```

---

**`technical-writer`** — Draft or revise client-facing deliverables: field reports, test protocols, executive summaries, specifications.
Output: markdown document under `outputs/docs/`. Use `--project` to inject Django project scope.

```text
/agent technical-writer draft executive summary for the Oak Street noise assessment --project 25
/agent technical-writer write a field report section covering the Phase 2 site visit findings
/agent technical-writer draft a scope-of-work letter for wall assembly testing at 200 Main St
/agent technical-writer revise the Introduction section to clarify the test standard citations
/agent technical-writer draft a response to reviewer comment #4 on the STC discrepancy
```

---

**`standards-checker`** — Audit a draft or set of conclusions against ASTM, ISO, and IBC citations.
Output: gap analysis with PASS / CONDITIONAL PASS / FLAG FOR REVIEW verdicts.

```text
/agent standards-checker audit ASTM citations in the attached draft report --backend mlx
/agent standards-checker verify the STC ratings in the Phase 1 report match E336-17a procedure
/agent standards-checker check IBC 1207.3 compliance for the proposed partition assembly specs
/agent standards-checker does our field protocol meet E1007-16 requirements for IIC testing
```

---

**`technical-reviewer`** — Peer review a draft document: produces a review comment set and a revised version.
Output: review comments + revised document under `outputs/docs/`.

```text
/agent technical-reviewer review the attached noise assessment report for technical accuracy
/agent technical-reviewer check the executive summary for clarity and correct standard references
/agent technical-reviewer review the protocol draft before we send it to the client
```

---

**`weekly-reviewer`** — Summarize recent work across the workspace, surface open loops, and produce a project status overview.
Output: markdown weekly review under `outputs/`.

```text
/agent weekly-reviewer summarize this week's work and open loops across all active projects
/agent weekly-reviewer what's the status of the LVT and Oak Street projects this week
/agent weekly-reviewer what deliverables are outstanding and which projects need follow-up
```

---

**`timesheet-reviewer`** — Reconcile monthly org-roam timesheet working notes against the reference ledger; propose missing entries and Notes/Review text before export.
Output: reconciliation summary under `outputs/timesheets/`. Run `/timesheet export` for the final templated org deliverable.

```text
/agent timesheet-reviewer reconcile July 2026 hours for project 42
/agent timesheet-reviewer review the monthly LVT timesheet and flag missing entries
/agent timesheet-reviewer draft Notes for the July timesheet after reconciling reference entries
/agent timesheets prepare month-end summary for LVT Phase B --project 42
```

---

**`latex-writer`** — Produce compilable `.tex` source for consulting deliverables with style-controlled preambles.
Output: `.tex` file under `outputs/latex/`. Accepts `--style`, `--template`, `--list-styles`.

```text
/agent latex-writer --style consulting-report draft field report for project 18
/agent latex-writer --style executive-summary draft exec summary for project 5
/agent latex-writer --list-styles
/agent latex-writer --template preamble-minimal scaffold a blank test protocol
```

---

**`panning-for-gold`** — Mine a meeting transcript, brainstorm session, or raw notes for actionable ideas, evaluations, and PKM captures.
Output: gold-found markdown with ACT NOW / RESEARCH MORE / PARK IT / KILL IT triage.

```text
/agent panning-for-gold extract ideas from the attached site visit transcript
/agent panning-for-gold mine this brainstorm doc for anything worth capturing
```

---

**`zettelkasten-curator`** — Scan daily journal markers (`#idea`, `#extract`, `TODO extract`) and propose atomic org-roam notes with conservative link suggestions.
Output: proposal batch JSON + org review file under `outputs/zettelkasten/`.

```text
/agent zettelkasten-curator extract and propose atomic notes from this week's journals
/agent zettel review recent journals for #idea and #extract markers
```

---

**`lvt-task-extractor`** — Scan LVT project notes and journals for pending actions, categorized into Technical/CAD, Procurement, Coordination, and Documentation.
Output: markdown task checklist under `outputs/tasks/`.

```text
/agent lvt-task-extractor extract pending LVT actions from this week's journals
/agent lvt scan recent notes and produce the LVT pending actions list for Monday planning
/agent lvt what do I still owe LVT this week
```

---

**`coordination-analyst`** — Scan context for client coordination signals: tricky asks, scope implications, required deliverables, implied engineering tasks, and suggested response framing.
Output: structured markdown under `outputs/coordination/`.

```text
/agent coordination-analyst review this email from the Oak Street client about extra assemblies
/agent coord scan this week's journal for outstanding client coordination items
/agent coordination-analyst what engineering tasks does this client ask imply for project 32
/agent coord flag any scope drift indicators in the attached meeting notes
```

---

**`rental-scout`** — Manage the Bay Area rental search: read/update criteria, trigger listing scans, report scored top matches, clear the dedup database, and queue overnight tasks. Aliases: `rental`, `scout`, `housing`. **Tool-use agent** — requires structured tool calling; use `--backend claude` or `--backend auto` when the Journaler default is MLX (see [Bay Area Rental Scout](#bay-area-rental-scout)).

Output: markdown match report; org-mode digests ready for journal appends. Workspace set by `rental_scout.workspace_dir` (default `~/dev/rental_scout`).

```text
/agent rental-scout show today's top matches in Oakland
/agent rental-scout set max price to 3800 and require in-unit laundry
/agent rental --backend claude run a dry-run scan of craigslist and zumper
/agent rental-scout format the top 5 matches for my journal
/agent rental-scout how many listings have we seen, and from which sources?
/agent rental-scout --backend auto clear the dedup database and rescan Oakland
/agent rental-scout queue an overnight scan for tomorrow's digest
```

See [Bay Area Rental Scout](#bay-area-rental-scout) for setup, configuration, MCP wiring, and the full command reference.

---

**`blender`** — Inspect and visualize acoustic scenes in a live Blender session via MCP (room geometry, SPL colormaps, receiver markers). Aliases: `3d`, `blender-mcp`. **Tool-use agent** — requires structured tool calling; use `--backend claude` when Journaler default is MLX (see [Blender MCP](#blender-mcp)).

Check connectivity without delegating: `/blender status`.

```text
/blender status
/agent blender --backend claude summarize the current scene and list receiver empties
/agent 3d --backend claude apply a viridis SPL colormap to the wall mesh collection
/agent blender-mcp check MCP tools before editing materials on the room shell
```

See [Blender MCP](#blender-mcp) for setup and configuration.

---

**`horn-iterator`** — Parametric exponential-horn design screener for the LVT alert system: sweeps flare length and mouth width/height, computes cutoff frequency, mouth area, coverage/FOV, and low-frequency rolloff, then validates candidates against the LVT constraints. Aliases: `horn`, `horn-sweep`, `waveguide`. **Tool-use agent** — use `--backend claude` when the Journaler default is MLX. Computes locally (no external API).

Run deterministically without the LLM via the `/horn` slash command or the `engineering-hub horn` CLI.

```text
/horn defaults
/horn sweep --export csv
/agent horn-iterator --backend claude sweep the design space and rank the best LVT candidates
/agent horn --backend claude evaluate a 130mm exponential length with a 120x80mm mouth
```

See [Horn Iterator](#horn-iterator) for setup and configuration.

---

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

### Coordination Analyst Agent

The `coordination-analyst` agent scans provided context — emails, meeting notes, journal entries, project correspondence — for client coordination signals and produces a structured breakdown:

- **Core Ask**: what the client actually wants, stripped of politeness and ambiguity
- **Scope Implications**: what this adds or changes relative to the agreed project scope
- **Deliverables Required**: concrete outputs needed (report sections, drawings, test data)
- **Engineering Tasks**: specific org-TODO-ready tasks implied by the ask
- **Suggested Response Framing**: professionally framed reply language for the engineer
- **Risk / Urgency**: timeline pressure, relationship risk, downstream dependencies
- **Scope Drift Indicators**: cross-message patterns suggesting unstated expectations

**Invoke on demand:**
```
/agent coordination-analyst review the attached email thread from project 32
/agent coord scan this week's journal for outstanding client coordination items
```

**Run as a one-off CLI scan:**
```bash
engineering-hub journaler briefing --coordination-scan
```

Output is written to `.journaler/outputs/coordination/YYYY-MM-DD.md`. When the scheduled scan is enabled (`journaler.coordination_scan_enabled: true`, `coordination_scan_interval_min: 240`), the daemon runs the scan automatically and feeds results into the **coordination-liaison** persona's history store so they surface in the next Discussion Briefing.

### Topics Discussion Briefing

The Discussion Briefing generates a multi-persona roundtable over the day's shared project context. Each persona makes one LLM call, seeing the workspace context plus the running transcript from personas who spoke before it.

**Personas** are defined in the top-level `personas/` directory alongside `skills/`. Each `.yaml` file describes one org identity:

```yaml
# personas/project-manager.yaml
id: project-manager
display_name: "Alex (Project Manager)"
role_summary: "Tracks project timelines, budget health, client commitments, and blockers."
communication_style: "Direct and deadline-oriented. Flags pressure early."
areas_of_focus:
  - project timeline and milestone status
  - blocking issues and dependencies
  - scope changes and their schedule impact
system_prompt_suffix: |
  You are Alex, the Project Manager. Focus on: are we on track? Are any
  deadlines in danger? What do we need to decide or escalate today?
```

**Five personas ship out of the box:**

| Persona | Focus |
| --- | --- |
| Alex (Project Manager) | Schedule, risk, client commitments, blockers |
| Sam (Acoustic Engineer) | Measurement methodology, standards compliance, technical flags |
| Taylor (Client Liaison) | Communication quality, relationship health, outstanding client items |
| Jordan (Standards Reviewer) | ASTM/ISO/IBC compliance, citation accuracy, defensibility |
| Morgan (Coordination Liaison) | Client coordination signals, scope drift, implied tasks |

Each persona maintains an append-only history log under `.journaler/personas/{id}/history.jsonl`, injected back as past context in subsequent sessions for continuity across days.

**Generate on demand:**
```bash
engineering-hub journaler briefing --discussion
engineering-hub journaler briefing --latest-discussion
```

**Enable as a scheduled briefing** (runs before the morning briefing by default):
```yaml
journaler:
  discussion_briefing_enabled: true
  discussion_briefing_time: "08:45"
```

Output is saved to `.journaler/briefings/discussion-YYYY-MM-DD.md`. When `briefing_append_to_journal` is enabled (default), the discussion report is also upserted under `* Discussion Briefing` in today's org journal.

### Background Agent Work Loop

The Background Agent Work Loop closes the gap between briefings (which identify work) and agent execution (which does work). When enabled, the daemon extracts actionable tasks from morning and discussion briefings, queues them, and proactively delegates them through the existing `AgentDelegator` throughout the day.

#### How it works

1. **Task Extraction** -- After each morning or discussion briefing, the daemon runs an MLX extraction pass that identifies concrete tasks from the briefing content (agenda items, suggested paths forward, persona next-steps). Recent chat history from `conversation.jsonl` is included so the extractor can boost tasks the user has been discussing and skip tasks already addressed via interactive `/agent` commands.

2. **Background Queue** -- Extracted tasks are stored in `{state_dir}/background_queue/YYYY-MM-DD.json` with priority, suggested agent, and source tracking. Tasks are deduplicated against the existing queue.

3. **Work Loop** -- A scheduled tick (default: every 60 minutes) picks the highest-priority pending task, builds an enriched delegation context (briefing context + relevant chat history), and delegates via `AgentDelegator`. Output is written to `{state_dir}/outputs/background/YYYY-MM-DD/{task_id}.md`.

4. **Work Status** -- A rolling status file at `{state_dir}/agent_work_status/YYYY-MM-DD.md` tracks what was planned vs. accomplished, grouped by source (morning briefing, discussion briefing, journal tasks). This file is injected into both `get_current_context()` and `get_briefing_context()` so the chat model and tomorrow's briefing see what the background agent did.

5. **Enhanced EOD Summary** -- The end-of-day summary now incorporates the morning briefing agenda, background work status, and chat history to produce a reconciliation: what was planned, what got done, what the user engaged with, and what should seed tomorrow's briefing.

#### Configuration

```yaml
journaler:
  background_work_enabled: true            # off by default
  background_work_interval_min: 60         # check queue every hour
  background_work_max_tasks_per_day: 6     # cap to avoid runaway model usage
  background_work_auto_approve: true       # auto-delegate without confirmation
  background_work_agent_backend: "mlx"     # "mlx" | "claude" | "auto"
  background_work_chat_lookback_days: 3    # days of chat history for priority signals
```

When `background_work_auto_approve` is `false` (default), extracted tasks are logged but not delegated automatically -- the daemon reports them in the work status file for the user to review. Set to `true` for fully autonomous operation.

#### State files

```text
.journaler/
├── background_queue/        # Daily JSON task queues
│   └── YYYY-MM-DD.json
├── agent_work_status/       # Daily work status markdown
│   └── YYYY-MM-DD.md
└── outputs/background/      # Background agent output files
    └── YYYY-MM-DD/
        └── {task_id}.md
```

### Task-Integrator (inline call-and-response)

The Task-Integrator removes the need to remember the `- [ ] @agent-type: description` delegation syntax. You write free-form notes in your daily journal as usual; the integrator reads the whole note, interviews you inline, and converts your replies into correctly-formatted `@agent:` tasks — behind an approval checkbox.

#### How it works

1. **Read intake** -- Each cycle reads today's daily `.org` note, excluding the agent-managed sections (`task_integrator_excluded_sections`, e.g. `Agent Conversation`, `Overnight Agent Tasks`, `Timesheet`). New free-form content (deduplicated by content hash) becomes interview material.

2. **Interview** -- For implied or explicit tasks, an MLX pass writes 1-3 clarifying questions into an `* Agent Conversation` section, anchored by a `:CONV_ID:` drawer, with a `Reply (type your answer below this line):` marker.

3. **Resolution** -- When you type a reply inline under the marker, the next cycle drafts ready-to-run tasks as `- [ ] @agent: …` checkboxes under a `Proposed tasks (re: <id>)` block. You see the syntax but never write it.

4. **Approval** -- Tick a proposal's checkbox to approve it. On the next cycle (Mon-Fri by default), each approved task is appended to `* Overnight Agent Tasks`, where the existing Orchestrator pipeline picks it up — closing the delegation gap.

State lives in `{state_dir}/task_integrator/YYYY-MM-DD.json`; all org interaction is append-only to today's note.

#### Running it

- **Daemon:** set `task_integrator_enabled: true`; cycles run every `task_integrator_interval_min` minutes.
- **On demand:** type `/integrate` in **`journaler chat`** (or the TUI, or send `/integrate` over HTTP `POST /chat`) to run one cycle now. `/integrate status` shows today's awaiting/proposed/queued counts.

#### Configuration

```yaml
journaler:
  task_integrator_enabled: true              # off by default
  task_integrator_interval_min: 15           # cycle interval in the daemon
  task_integrator_conversation_section: "Agent Conversation"  # where Q&A + proposals go
  task_integrator_output_section: "Overnight Agent Tasks"     # where approved tasks are queued
  task_integrator_excluded_sections:         # daily-note headings treated as agent-managed
    - "Agent Conversation"
    - "Overnight Agent Tasks"
    - "Completed Agent Tasks"
    - "Pending Agent Tasks"
    - "Timesheet"
    - "Journaler Cross-References"
  task_integrator_max_questions: 3           # interview questions per topic
  task_integrator_weekdays_only: true        # only queue approved tasks Mon-Fri
  task_integrator_max_tokens: 1024           # model budget per interview/resolution call
```

#### Commands

| Command | Where | Description |
| --- | --- | --- |
| `/integrate` | `journaler chat`, `journaler tui`, HTTP `POST /chat` | Run one full cycle now (interview → resolution → approval) and report counts |
| `/integrate status` | `journaler chat`, `journaler tui`, HTTP `POST /chat` | Show today's `awaiting reply` / `proposed (awaiting approval)` / `queued` counts without running a cycle |

A manual `/integrate` is useful when you want an immediate pass after editing your note rather than waiting for the next daemon tick. Each phase is idempotent: already-interviewed content is skipped (content-hash dedup), an already-processed reply is not re-resolved (reply-hash tracking), and an already-queued proposal is not queued twice. `/integrate` echoes a one-line summary such as:

```text
Task-Integrator: asked 1 question set(s), wrote 0 proposal(s), queued 0 task(s); 1 thread(s) awaiting reply.
```

and `/integrate status` reports, for example:

```text
Task-Integrator status (2026-06-16): 1 awaiting reply, 0 proposed (awaiting approval), 0 queued.
```

#### Example interaction

Suppose your daily journal starts like this — you jotted a free-form note under `* Notes`:

```org
#+title: 2026-06-16

* Notes
Need to validate the new N4v3 horn geometry timing against the N4v2 build before the VVDN call.
```

**1. Interview.** On the next cycle (or after you run `/integrate`), the integrator reads the whole note minus the managed sections, recognizes an actionable topic, and appends an interview block to `* Agent Conversation`:

```org
* Agent Conversation
** [2026-06-16 Tue 09:15] N4v3 vs N4v2 horn timing validation
:PROPERTIES:
:CONV_ID: 7f3a9c21
:END:
Q1: Which acceptance threshold defines a passing timing comparison?
Q2: Do you need a written protocol, a data summary, or both before the VVDN call?
Q3: Is there a deadline tied to the VVDN call date?

Reply (type your answer below this line):

```

**2. Reply inline.** You answer directly in the note under the marker — in plain language, no syntax to remember:

```org
Reply (type your answer below this line):
Within 5% of the N4v2 timing. I want a short data summary plus a one-page protocol. VVDN call is Thursday, so I need it by Wednesday EOD.
```

**3. Resolution.** The next cycle detects the reply and drafts ready-to-run, approval-gated tasks under a proposal block (you see the `@agent:` syntax but never type it):

```org
** [2026-06-16 Tue 09:31] Proposed tasks (re: 7f3a9c21)
:PROPERTIES:
:PROPOSAL_FOR: 7f3a9c21
:END:
Tick a box to approve; approved tasks are queued to * Overnight Agent Tasks.
- [ ] @research: Compare N4v3 vs N4v2 horn timing data and report deviations against a 5% acceptance threshold
- [ ] @technical-writer: Draft a one-page timing-comparison test protocol for the N4v3 build ahead of the VVDN call
```

**4. Approval.** You tick the boxes you want (a normal org checkbox edit):

```org
- [x] @research: Compare N4v3 vs N4v2 horn timing data and report deviations against a 5% acceptance threshold
- [ ] @technical-writer: Draft a one-page timing-comparison test protocol for the N4v3 build ahead of the VVDN call
```

**5. Queue handoff.** On the next cycle (Mon–Fri by default), each approved proposal is appended to `* Overnight Agent Tasks` in the exact syntax the Orchestrator scans:

```org
* Overnight Agent Tasks
- [ ] @research: Compare N4v3 vs N4v2 horn timing data and report deviations against a 5% acceptance threshold
```

From here the existing [Orchestrator pipeline](#orchestrator-task-driven-agents) picks up the `@agent:` line and dispatches it like any other overnight task — the unticked proposal stays a proposal until you approve it.

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

### Report Drafting Pipeline

The `/pipeline draft-section` command chains four specialized agents into a single automated workflow for drafting a section of a technical consulting report. All numeric calculations (dB levels, compliance margins, measurement averages) are performed by **external Python scripts or simulators before the pipeline is invoked**. The pipeline receives only finalized result tables and produces prose.

#### Scope boundary

| Responsibility | Owner |
| --- | --- |
| Field measurements, Leq/L90/L10 averages | External Python scripts |
| CadnaA / simulation model output | External simulator |
| dB calculations, compliance pass/fail math | External Python scripts |
| Collecting and routing finalized tables | `DataGatherer` (`context/data_gatherer.py`) |
| Drafting prose + flagging relevant metrics | `technical-writer` persona |
| Auditing prose claims against standard limits | `standards-checker` persona |
| Reviewing for professional tone | `technical-reviewer` persona |
| Final LaTeX formatting | `latex-writer` persona |

#### How it works

1. **Gather** — `DataGatherer` scans `outputs/staging/project-{id}/` (files produced by `engineering-hub load` / ingest) and classifies them by keyword heuristic: regulatory criteria (HDOH, FAA, zoning limits), simulation output (CadnaA, predicted levels), equipment specs (SWL, manufacturer data), and field measurement results (Leq, L90, ambient). Files are read verbatim — no arithmetic.
2. **Draft** — `technical-writer` receives the classified data bundle and drafts prose for the named section. It identifies which metrics are most relevant to the project's compliance goal but does not compute or modify any numeric values.
3. **Audit (with loop-back)** — `standards-checker` audits the draft against the applicable regulatory limits. If it finds `NON-COMPLIANT` items, it returns specific corrections and the pipeline loops back to the writer with those audit notes (bounded by `--loop-limit`, default 2 retries). If compliance cannot be achieved within the retry limit, a `PIPELINE_FAILED_*.md` artifact is written under `outputs/pipeline/` for manual review.
4. **Review** — `technical-reviewer` checks the compliant draft for professional tone, appropriate hedging language, and defensibility.
5. **Format** — `latex-writer` renders the reviewed prose as a LaTeX section with proper `tabular` environments, column headers, and footnotes.

The final artifact is written to `outputs/pipeline/pipeline_<slug>_<timestamp>.md` (Markdown intermediate) or `.tex` (after the LaTeX stage).

#### Slash command

```text
/pipeline draft-section --section "6.0 Noise Impacts" --project 42
/pipeline draft-section --section "3.0 Existing Conditions" --project 42 --backend claude --loop-limit 3
```

| Flag | Description |
| --- | --- |
| `--section "<label>"` | Report section identifier (required) |
| `--project <id>` | Django project ID for staging directory lookup |
| `--backend mlx\|claude` | Agent backend for all stages (default: `auto`) |
| `--loop-limit <n>` | Maximum standards-checker retries (default: 2) |

When no live delegator is configured (no API key, no MLX model loaded), the command queues a `@pipeline:` TODO to `pending-tasks.org` for the Orchestrator to pick up.

#### CLI command

```bash
engineering-hub journaler pipeline draft-section \
  --section "6.0 Noise Impacts" --project 42 --backend claude

engineering-hub journaler pipeline draft-section \
  --section "3.0 Existing Conditions" --project 42 --loop-limit 3
```

#### Preparing data files

Place pre-computed result files in the project's staging directory before running the pipeline:

```bash
# Ingest a file so it appears in outputs/staging/project-42/
engineering-hub load path/to/cadnaa_results.csv --project 42

# Or copy files directly (CSV, Markdown, plain text, .tex)
cp results/ambient_summary.csv outputs/staging/project-42/
cp results/hdoh_criteria.md outputs/staging/project-42/
```

`DataGatherer` classifies files on the following first-match keyword order (most specific first):

| Category | Example keywords |
| --- | --- |
| `regulatory` | `HDOH`, `FAA`, `ASHRAE`, `zoning`, `noise limit`, `dB limit`, `Class B` |
| `simulation_output` | `CadnaA`, `predicted`, `propagation`, `receiver`, `noise map` |
| `equipment_specs` | `SWL`, `sound power`, `manufacturer`, `HVAC`, `RTU`, `datasheet` |
| `field_results` | `Leq`, `L90`, `L10`, `ambient`, `monitoring`, `dBA`, `octave band` |
| `unclassified` | anything else |

#### Diagnostic tasks

The `diagnostics/context_pipeline_tasks.yaml` includes five pipeline-specific tasks that exercise each stage in isolation — including a deliberate `NON-COMPLIANT` standards-checker case to verify the loop-back path. Run with:

```bash
engineering-hub diagnostic context-pipeline --dry-run-context-only -v
```

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
├── state.json           # Canonical path keys, mtimes, and SHA-256 content hashes for incremental scanning
├── context_cache.json   # Compressed rolling context snapshot
├── conversation.jsonl   # Full chat history log (all turns, including archived/compressed)
├── briefings/           # Generated morning briefings (YYYY-MM-DD.md)
├── topic_hints/         # Auto-generated conversation starters after significant scans
├── daily_summaries/     # End-of-day conversation summaries (YYYY-MM-DD.md)
├── background_queue/    # Daily JSON task queues extracted from briefings (YYYY-MM-DD.json)
├── agent_work_status/   # Daily background agent work status (YYYY-MM-DD.md)
└── outputs/background/  # Background agent output files (YYYY-MM-DD/{task_id}.md)
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

#### How retrieval is wired

| Path | When it runs | Query text | Where chunks appear |
| --- | --- | --- | --- |
| **Journaler chat** (`journaler start` HTTP `/chat`, **`journaler chat`**) | Every normal user turn (not slash commands) | The **entire user message** is embedded and searched | Appended to the **system** prompt for that turn only (`ConversationEngine.chat`) |
| **Inline `/agent` delegation** | Each `/agent` run when `corpus.enabled` is true and the corpus is available | The delegated task description | Added to the delegated `AgentWorker` context before the task block |
| **Orchestrator agents** (`engineering-hub start`, `run-once`) | Each dispatched `@agent:` task after Django context is loaded | **`task.description`** plus optional **`task.context`** | Concatenated into the formatted context string after the memory block (`ContextManager` → `ContextFormatter`) |

**Not covered by these paths:** morning **`journaler briefing`** and **`journaler export --summarize`** (single-shot completion).

#### `/agent` delegation and corpus

Inline **`/agent`** now receives Journaler-loaded files and task-matched PDF corpus excerpts in its delegated context. The `--project` flag is still stored on the task (for output paths and future use), but it does not trigger full Orchestrator-style Django context via `ContextManager.format_for_agent`.

**Practical implications:**

- For **PDF RAG + persona in one shot**, use **`/agent`** directly; the delegated task description is searched against the corpus.
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

## Bay Area Rental Scout

Engineering Hub integrates with the separate **rental-scout** pipeline project (typically cloned to `~/dev/rental_scout`). Hub does **not** scrape listings itself — it orchestrates the pipeline via a shared service layer exposed to Journaler agents, the Orchestrator, and external MCP clients.

```text
┌─────────────────────┐     ┌──────────────────────────────┐     ┌─────────────────────┐
│ Cursor / Journaler  │────▶│ engineering_hub.rental_scout │────▶│ ~/dev/rental_scout  │
│ /agent rental-scout │     │ .service (criteria, scan,    │     │ main.py, criteria,  │
│ engineering-hub     │     │  digest queries)             │     │ seen_listings.db    │
│ mcp-server          │     └──────────────────────────────┘     └─────────────────────┘
└─────────────────────┘
```

### Prerequisites

1. **Clone the rental-scout project** into your workspace (default `~/dev/rental_scout`).
2. **Create and install its virtualenv** (Playwright and scraper deps live here, not in Engineering Hub):

```bash
cd ~/dev/rental_scout
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env   # then edit API keys / LLM settings
```

3. **Configure listing scoring** in `rental_scout/.env`:
   - `LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY` (default), or
   - `LLM_PROVIDER=ollama` + `OLLAMA_URL` / `OLLAMA_MODEL` for local scoring

4. **Point Engineering Hub at the workspace** in `config.yaml` (or rely on the default):

```yaml
rental_scout:
  workspace_dir: "~/dev/rental_scout"
  # Optional — auto-detected when {workspace_dir}/.venv/bin/python exists:
  # python_path: "~/dev/rental_scout/.venv/bin/python"
```

`rental_run_scan` uses `{workspace_dir}/.venv/bin/python` when present, sets `cwd` to the workspace (so `.env` loads), and exports `RENTAL_SCOUT_WORKSPACE`.

### Agent tools (via `/agent rental-scout`)

All eight rental tools are available in-process when the backend supports tool calling:

| Tool | Purpose |
| --- | --- |
| `rental_get_criteria` | Read current search settings |
| `rental_update_criteria` | Patch cities, price, bedrooms, amenities |
| `rental_run_scan` | Run the scrape + score pipeline (may take several minutes) |
| `rental_get_top_matches` | Read scored matches from the latest digest |
| `rental_get_listing_stats` | Dedup DB stats and last-scan metadata |
| `rental_format_digest_for_org` | Top matches as org-mode text |
| `rental_clear_seen_listings` | Reset dedup DB (`confirm=true` required) |
| `rental_add_journal_task` | Queue an `@rental-scout` org task for the Orchestrator |

**Backend note:** Rental-scout is a tool-use agent. With Journaler default `agent_backend: "mlx"`, delegation falls back to single-shot text without real tool calls. For scans and criteria changes, prefer:

```text
/agent rental-scout --backend claude show today's top matches
/agent rental-scout --backend auto run a dry-run scan of craigslist
```

Or set `journaler.agent_backend: "auto"` in config. For Orchestrator-dispatched org tasks, use `llm_provider: "anthropic"` or `llm_provider: "ollama"` with a tool-capable chat model.

#### Sample Journaler commands

```text
# Read settings and recent matches (no new scan)
/agent rental-scout what are my current search criteria?
/agent rental-scout show today's top matches in Oakland with score >= 7

# Adjust criteria, then scan
/agent rental-scout --backend claude set max price to 3800 and require in-unit laundry
/agent rental-scout --backend claude run a full scan of craigslist, redfin, and zumper

# Preview without marking listings seen or sending digest
/agent rental --backend claude run a dry-run scan of craigslist

# Stats and journal output
/agent rental-scout how many listings have we seen, and from which sources?
/agent rental-scout format the top 5 matches for my journal

# Reset dedup after changing cities (agent should confirm with user first)
/agent rental-scout --backend claude clear the seen-listings database and rescan

# Queue for Orchestrator instead of running immediately
/agent rental-scout queue an overnight scan for tomorrow morning
```

Org tasks written by `rental_add_journal_task` use the format:

```org
* Overnight Agent Tasks
- [ ] @rental-scout: run rental scan for Oakland
```

## Blender MCP

Engineering Hub connects to a **running Blender session** with an MCP addon (for example [dcc-mcp-blender](https://github.com/dcc-mcp/dcc-mcp-blender) at `http://127.0.0.1:8765/mcp`). Hub does not start Blender — it calls the addon's HTTP MCP endpoint from Journaler agents and optionally proxies those tools through `engineering-hub mcp-server`.

```text
┌─────────────────────┐     ┌──────────────────────────────┐     ┌─────────────────────┐
│ journaler chat      │────▶│ engineering_hub.blender      │────▶│ Blender + MCP addon │
│ /agent blender      │     │ .service (health, list, call)│     │ (HTTP /mcp)         │
│ /blender status     │     └──────────────────────────────┘     └─────────────────────┘
└─────────────────────┘
```

### Prerequisites

1. Install and enable an MCP addon inside Blender (dcc-mcp-blender, blender-mcp, etc.).
2. Start the MCP server from the addon (note the URL — default for dcc-mcp-blender is `http://127.0.0.1:8765/mcp`).
3. Enable integration in `config.yaml`:

```yaml
blender:
  enabled: true
  mcp_url: "http://127.0.0.1:8765/mcp"
  # Optional auth: ENGINEERING_HUB_BLENDER_AUTH_TOKEN env var
  tool_denylist: ["run_python_script"]
```

**Security:** Blender MCP can execute generated Python in your open `.blend` file. Use tool denylists, work on backed-up files, and confirm destructive operations.

### Agent tools (via `/agent blender`)

| Tool | Purpose |
| --- | --- |
| `blender_health` | Connection check and filtered tool count |
| `blender_list_tools` | Discover remote MCP tool names/schemas |
| `blender_call_tool` | Invoke any allowed remote tool by name |

**Backend note:** `@blender` is a tool-use agent. With Journaler default `agent_backend: "mlx"`, delegation falls back to single-shot text without real tool calls. Prefer:

```text
/blender status
/agent blender --backend claude summarize the current scene and list mesh objects
/agent blender --backend auto create a 6x4x2.7m room box named ConferenceRoom_A
```

Natural-language routing to `@blender` (when not in propose mode) also appends `--backend claude` automatically.

### MCP proxy (optional)

When `blender.enabled: true`, `engineering-hub mcp-server` mounts a live proxy under the `blender_` namespace so Cursor can use memory + rental + Blender tools from one config. If Blender is offline, proxy mounting may add latency to other hub MCP operations — you can also connect Cursor directly to the Blender addon URL instead.

### Run the pipeline directly (without Hub)

Useful for cron, debugging, or manual daily runs:

```bash
cd ~/dev/rental_scout
source .venv/bin/activate

# Full daily scan
python main.py

# Restrict sources / preview / skip LLM scoring
python main.py --sources craigslist,redfin,zumper
python main.py --dry-run --output-json latest_digest.json
python main.py --skip-llm
python main.py --schedule   # daily 07:00 loop
```

Artifacts in the workspace: `criteria.yaml`, `seen_listings.db`, `latest_digest.json`.

### MCP client setup (Cursor / Claude Desktop)

Add Engineering Hub's unified MCP server — rental tools are mounted under the `rental_` namespace:

```json
{
  "mcpServers": {
    "engineering-brain": {
      "command": "/Users/you/dev/engineeringhub_controlinterface/.venv/bin/engineering-hub",
      "args": ["mcp-server"]
    }
  }
}
```

Start HTTP transport for remote clients:

```bash
engineering-hub mcp-server --transport http --port 8000
```

**MCP tool names:** `rental_get_criteria`, `rental_update_criteria`, `rental_run_scan`, `rental_get_top_matches`, `rental_get_listing_stats`, `rental_clear_seen_listings`, `rental_add_journal_task`, `rental_format_digest_for_org`

**Example prompts in Cursor** (once MCP is connected):

```text
Use rental_get_criteria to show my current rental search settings.
Use rental_run_scan with dry_run=true for craigslist only, then rental_get_top_matches for Oakland.
Use rental_format_digest_for_org to give me org-mode output for the top 5 matches.
```

Standalone rental MCP (without memory tools):

```bash
python -m engineering_hub.mcp.rental_scout                # stdio
python -m engineering_hub.mcp.rental_scout --transport sse  # HTTP on :18791
```

## Horn Iterator

A local parametric sweep calculator for exponential-horn waveguide design, built around the LVT alert-system constraints. It sweeps the exponential flare length and the mouth width/height, then for each candidate computes the cutoff frequency (`fc = c·m / (4π)`), mouth area (`S_M = S_T·e^(m·L)`), the chopped/unchopped vertical aperture, horizontal/vertical coverage angles, the quarter-wave standing-wave resonance, and the low-frequency SPL rolloff below cutoff. Every candidate is validated against the LVT envelope (250 Hz–12 kHz, 100–105 dB @ 1 m, 130–180° FOV, 150×80×160 mm, ≤10 lb). All computation is local — no LLM or API is required.

```text
┌─────────────────────┐     ┌──────────────────────────────┐     ┌─────────────────────┐
│ /horn (slash)       │     │ horn_iterator.service        │     │ config / geometry / │
│ engineering-hub horn│ ──▶ │ run_sweep, evaluate_design,  │ ──▶ │ physics / sweeper / │
│ /agent horn-iterator│     │ export_results, get_defaults │     │ export              │
└─────────────────────┘     └──────────────────────────────┘     └─────────────────────┘
```

### CLI

```bash
engineering-hub horn defaults                 # show LVT constraints + sweep bounds
engineering-hub horn sweep                     # run the sweep, print a markdown report
engineering-hub horn sweep --export csv        # also write a CSV to the output dir
engineering-hub horn sweep --step-l 5 --step-wh 5   # finer grid
```

### Slash command (journaler chat / TUI / HTTP)

```text
/horn                       # defaults (constraints + bounds)
/horn sweep                 # run the sweep inline
/horn sweep --export org    # run + export an org-table artifact
```

### Agent tools (via `/agent horn-iterator`)

| Tool | Purpose |
| --- | --- |
| `horn_get_defaults` | Configured LVT constraints and sweep bounds |
| `horn_evaluate_design` | Evaluate one (length, width, height) candidate |
| `horn_run_sweep` | Sweep the design space; return passing designs |
| `horn_export_results` | Run a sweep and write CSV/org to the output dir |

The `horn-iterator` persona is a tool-use agent. When the Journaler default backend is MLX, request Claude for structured tool calling:

```text
/agent horn-iterator --backend claude sweep the design space and rank the best LVT candidates
/agent horn --backend claude evaluate a 130mm exponential length with a 120x80mm mouth
/agent waveguide --backend claude export the full sweep as org for my journal
```

Because the default envelope is intentionally tight, the sweep often reports few or zero fully-passing designs; the agent surfaces the binding constraints and the closest compromises ranked by low-frequency rolloff. Configure overrides and the export directory under `horn_iterator:` in `config.yaml` (see [Configuration Reference](#configuration-reference)).

## MCP Server

`engineering-hub mcp-server` exposes hub tools to external MCP clients (Cursor, Claude Desktop) over stdio (default) or HTTP (`--transport http`).

Two toolsets are served from one process:

- **engineering-brain memory tools** — `search_brain`, `browse_recent`, `capture_note`, `get_stats`
- **Rental Scout tools** (mounted under the `rental_` namespace) — `rental_get_criteria`, `rental_update_criteria`, `rental_run_scan`, `rental_get_top_matches`, `rental_get_listing_stats`, `rental_clear_seen_listings`, `rental_add_journal_task`, `rental_format_digest_for_org`
- **Blender MCP proxy** (when `blender.enabled: true`, mounted under the `blender_` namespace) — remote addon tools; requires Blender running. See [Blender MCP](#blender-mcp).

The rental tools operate on the workspace configured by `rental_scout.workspace_dir` (criteria.yaml, seen_listings.db, latest_digest.json). `rental_run_scan` shells out to the rental-scout pipeline's `main.py` using the workspace `.venv` Python when available. See [Bay Area Rental Scout](#bay-area-rental-scout) for full setup and sample commands.

Cursor / Claude Desktop config:

```json
{
  "mcpServers": {
    "engineering-brain": {
      "command": "/path/to/.venv/bin/engineering-hub",
      "args": ["mcp-server"]
    }
  }
}
```

The rental-scout sub-server can also run standalone: `python -m engineering_hub.mcp.rental_scout` (stdio) or `--transport sse`.

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
- `journaler.scan_org_roam_tree` - When false, scan only `journal.org_journal_dir` and `journaler.watch_dirs` (default: true). Significance logging uses content hashes either way; false reduces walk/CPU on large roam trees
- `journaler.watch_dirs` - Extra org directories to include in scans
- `journaler.journal_lookback_days` / `journaler.journal_max_files` - Window for parsing daily journals (defaults: 30 / 30)
- `journaler.roam_task_lookback_days` / `journaler.roam_task_max_files` - Include checkboxes from recently modified org-roam project notes in the task registry (defaults: 14 / 30)
- `journaler.prose_completion_detection` - Treat journal prose (“finished X”, “X complete”) as task resolution when the checkbox is still open (default: true)
- `journaler.conversation_lookback_days` - Number of past daily conversation summaries included in the proactive context snapshot every tick (default: 7; independent of `journal_lookback_days`)
- `journaler.proactive_topic_scout_enabled` - Run a one-shot MLX topic scout when a scan tick detects significant journal / queue / output changes (default: true)
- `journaler.proactive_topic_scout_max_tokens` - Generation budget for the topic scout (default: 512)
- `journaler.background_work_enabled` - Enable background agent work loop that extracts tasks from briefings and delegates them (default: false)
- `journaler.background_work_interval_min` - Interval in minutes between background work loop ticks (default: 60)
- `journaler.background_work_max_tasks_per_day` - Maximum background tasks per day (default: 6)
- `journaler.background_work_auto_approve` - Auto-delegate extracted tasks without user confirmation (default: false)
- `journaler.background_work_agent_backend` - Agent backend for background work tasks: `"mlx"`, `"claude"`, or `"auto"` (default: `"mlx"`)
- `journaler.background_work_chat_lookback_days` - Days of `conversation.jsonl` chat history to include in task extraction and delegation context (default: 3)
- `journaler.task_integrator_enabled` - Enable the inline call-and-response Task-Integrator loop over the daily journal (default: false)
- `journaler.task_integrator_interval_min` - Minutes between Task-Integrator cycles in the daemon (default: 15)
- `journaler.task_integrator_conversation_section` - Daily-journal heading where interview questions and proposals are written (default: "Agent Conversation")
- `journaler.task_integrator_output_section` - Daily-journal heading where approved `@agent:` tasks are queued (default: "Overnight Agent Tasks")
- `journaler.task_integrator_excluded_sections` - Daily-journal headings the integrator must not read as intake (agent-managed sections)
- `journaler.task_integrator_max_questions` - Maximum interview questions per topic (default: 3)
- `journaler.task_integrator_weekdays_only` - Only queue approved tasks Mon-Fri (default: true)
- `journaler.task_integrator_max_tokens` - Max tokens for Task-Integrator interview/resolution model calls (default: 1024)
- `journaler.org_link_on_relation` - When true, write a cross-reference link into today's journal whenever a related past conversation is detected via semantic search (default: true). Links target the related day's org journal, with fallback to `.journaler/daily_summaries/YYYY-MM-DD.md`. Excerpt length is controlled by `context_management.org_link_excerpt_chars` (default: 400).
- `journaler.model_profile` - Name of the active entry in `journaler.models` (when the map is non-empty)
- `journaler.models` - Optional map of named MLX profiles (`model_path`, `model_context_window`, sampling, `mlx_backend`, `enable_thinking`, `max_thinking_tokens`)
- `journaler.model_context_window` - Context window for pressure math when not using per-profile values (default: 32768)
- `journaler.agent_backend` - Backend for `/agent` delegation: `"mlx"` (default), `"claude"`, or `"auto"` (used by **`journaler start`** and **`journaler chat`**)
- `journaler.anthropic_api_key` - Optional per-journaler Anthropic key (falls back to `anthropic.api_key` / env if unset; same scope as `agent_backend`)
- `journaler.skills_dir` - Path to skills YAML directory (default: `skills/` at repo root; loaded into the system prompt for daemon and interactive chat)
- `journaler.context_management.*` - Token pressure thresholds, compression triggers, EOD reset time, topic-shift behavior
- `memory.*` - Vector memory settings (enabled, search_k, threshold)
- `rental_scout.workspace_dir` - Rental Scout workspace holding the pipeline and its artifacts (default: `~/dev/rental_scout`; used by `/agent rental-scout` and the `rental_` MCP tools)
- `rental_scout.python_path` - Python interpreter for `rental_run_scan` subprocesses (default: auto-detect `{workspace_dir}/.venv/bin/python`, else current interpreter)
- `blender.enabled` - Enable Blender MCP client for `/agent blender`, `/blender status`, and optional MCP proxy (default: false)
- `blender.mcp_url` - HTTP MCP endpoint for the Blender addon (default: `http://127.0.0.1:8765/mcp`)
- `blender.auth_token` / `ENGINEERING_HUB_BLENDER_AUTH_TOKEN` - Optional bearer token for authenticated Blender MCP endpoints
- `blender.tool_denylist` - Remote tool names blocked from agent invocation (default includes `run_python_script`)
- `blender.tool_allowlist` - When set, only listed remote tools may be invoked
- `blender.enabled` - Enable Blender MCP client integration and optional MCP proxy (default: false)
- `blender.mcp_url` - HTTP MCP endpoint for the Blender addon (default: `http://127.0.0.1:8765/mcp`)
- `blender.auth_token` - Optional bearer token for Blender MCP (`ENGINEERING_HUB_BLENDER_AUTH_TOKEN` env var)
- `blender.connect_timeout_s` - Connection timeout in seconds (default: 10)
- `blender.tools_cache_ttl_s` - Seconds to cache remote tool listings (default: 60)
- `blender.tool_allowlist` - When set, only these remote tool names may be invoked
- `blender.tool_denylist` - Remote tool names blocked from agent invocation (default includes `run_python_script`)
- `horn_iterator.enabled` - Enable the horn iterator agent, CLI, and `/horn` slash command (default: true)
- `horn_iterator.output_dir` - Directory for sweep exports (CSV/org); defaults to `{workspace_dir}/horn_iterator`
- `horn_iterator.flare_rate_per_m` - Override the exponential flare rate m (/m); blueprint default 17.8 (fc ≈ 486 Hz)
- `horn_iterator.slot_area_mm2` - Override the diffraction-slot throat area S_T (mm²); default 1050
- `horn_iterator.adapter_length_mm` - Override the fixed pre-flare adapter length (mm); default 52
- `corpus.enabled` - Enable PDF reference corpus RAG (requires `libraryfiles-corpus` and `corpus.db`)
- `corpus.db_path` - Path to `corpus.db` from libraryfiles-corpus ingest
- `corpus.search_k` / `corpus.threshold` - Max chunks and minimum similarity for corpus hits (defaults: 5 / 0.40)
- `agent_web_search.enabled` - Enable local-first web result injection for `/agent` by default (default: false; `--web` forces it per command)
- `agent_web_search.provider` - Web search provider for `/agent`; currently `searxng`
- `agent_web_search.searxng_url` - Base URL for the SearXNG instance (default: `http://localhost:8080`)
- `agent_web_search.max_results` / `agent_web_search.max_chars` - Result count and formatted context cap (defaults: 5 / 12000)
- `agent_web_search.anthropic_backup_enabled` - Allow Claude server-side web search fallback when local search fails and the backend is Claude (default: false)
- `diagnostics.context_pipeline.enabled` - Persist formatted context + results for each Orchestrator task under `outputs/diagnostics/context-pipeline/<run_id>/` (default: false)
- `diagnostics.context_pipeline.context_audit_prompt` - Append temporary CONTEXT AUDIT block to agent system prompts (default: false); env: `ENGINEERING_HUB_DIAGNOSTIC_CONTEXT_AUDIT_PROMPT`
- `diagnostics.context_pipeline.debug_context_max_chars` - Truncation cap for the extra DEBUG log of formatted context when diagnostics are enabled (default: 50000)
- CLI: `engineering-hub diagnostic context-pipeline` — run a YAML task suite with `--dry-run-context-only`, `--tasks`, `--max-tasks`, `--context-audit-prompt` (see [diagnostics/RUNBOOK.md](diagnostics/RUNBOOK.md))

## License

MIT
