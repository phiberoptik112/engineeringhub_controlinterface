# Engineering Hub Control Interface

A persistent, agent-first workspace enabling collaboration between engineers and AI agents on technical projects. Designed for acoustic engineering consulting workflows, connecting to a Django backend (consultingmanager) via REST API.

## Overview

Engineering Hub provides two complementary modes of AI collaboration:

1. **Orchestrator** (task-driven) -- watches your org-roam journal for `@agent:` task lines, dispatches work to specialized agents via Claude API or local MLX models, and writes results back to the workspace.
2. **Journaler** (ambient) -- a persistent daemon that runs a local ~32B model via MLX, continuously monitors your org-roam workspace, delivers morning briefings, and responds to ad-hoc questions through an HTTP chat endpoint.

They coexist cleanly: the Orchestrator processes explicit tasks while the Journaler maintains ambient awareness. The Journaler can read the full org-roam workspace, write back to daily journals, create org-roam nodes via slash commands, and — now — **delegate tasks directly to any agent personality inline** using the `/agent` command, with a choice of local MLX or Claude API execution.

### Key Features

- **Org-roam Integration**: Tasks live in daily `.org` journal files using `- [ ] @agent:` syntax
- **Specialized Agents**: Research, technical-writer, standards-checker, and more with domain expertise
- **Django Integration**: Pulls project context, standards, and files from the consultingmanager API
- **File Watching**: Monitors workspace for changes and automatically dispatches agent tasks
- **Local MLX Models**: Run agents on Apple Silicon via `mlx-lm` with HuggingFace model IDs
- **Journaler Daemon**: Always-on ambient listener with morning briefings, HTTP chat, and Slack integration
- **Agent Delegation**: Journaler chat can delegate tasks directly to any named agent personality (`/agent research ...`, `/agent technical-writer ...`) and receive results inline — backed by the local MLX model or Claude API, selectable per-command
- **Skills System**: Extensible `skills/` directory of YAML files defines each agent personality's capabilities; drop a new `.yaml` to add a delegation skill without code changes
- **Context Management**: Token-aware conversation history with automatic compression, topic-shift archival, end-of-day reset, and manual `/clear` controls — keeps the local model coherent across a full workday
- **Org-Roam Write Skill**: Journaler chat can write properly-formatted org-roam files — add TODOs, mark tasks done, append notes, create new nodes — via slash commands
- **Context File Loading**: Inject files or directories into the Journaler's live context (`/load`) or the persistent memory store (`engineering-hub load`)
- **Vector Memory**: Local semantic memory (memory.db) with Ollama embeddings for context retrieval

## Requirements

- Python 3.11+
- Access to Anthropic API (Claude) or a local MLX model on Apple Silicon
- Django consultingmanager backend (optional, for full project context)
- Ollama with `nomic-embed-text` (optional, for memory/embeddings)

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
```

### 6. Load Files into Context

**In a live `journaler chat` session** — use slash commands to inject file content directly into the model's context for the current conversation:

```
/load path/to/file.md           Load a single file
/load path/to/dir/              Load all supported files in a directory
/load path/to/dir/ -r           Load recursively
/files                          List currently loaded files (with sizes)
/files clear                    Remove all loaded files from context
/help                           Show all slash commands
```

Supported extensions: `.md`, `.txt`, `.org`, `.py`, `.yaml`, `.yml`, `.json`, `.tex`, `.csv`, `.toml`, `.rst`. Files over 50,000 chars are truncated with a notice. Loaded files appear in the model's system prompt on every turn and are cleared when the session ends.

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
│   ├── agents/          # Agent backends (Anthropic, MLX), worker, prompts
│   ├── cli.py           # Command-line interface
│   ├── config/          # Settings (pydantic-settings) and YAML loader
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
└── tests/
```

## Journaler: Ambient Listener

The Journaler is a persistent daemon that runs a local ~32B model on Apple Silicon via MLX, continuously monitors your org-roam workspace, and provides ambient awareness of your projects.

### How It Works

- **Scans** your org-roam directory every 10 minutes (mtime-based incremental diff)
- **Extracts** headings, TODO/DONE items, timestamps, and `@agent:` tasks from `.org` files
- **Reads** recent agent outputs from `memory.db` via `MemoryService.browse_recent()`
- **Compresses** everything into a rolling context snapshot (~4000 tokens)
- **Knows** the workspace layout and org-roam format conventions — injected into the system prompt at startup so the model can reason about file locations and produce valid org syntax
- **Generates** a morning briefing at a configurable time (default 7:00 AM)
- **Responds** to ad-hoc questions via an HTTP chat endpoint on `localhost:18790`
- **Writes** to daily journals and org-roam nodes via slash commands in the interactive chat session
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
  briefing_time: "07:00"
  chat_enabled: true
  chat_host: "127.0.0.1"
  chat_port: 18790
  slack_enabled: false
  slack_webhook_url: ""  # or set JOURNALER_SLACK_WEBHOOK env var
  max_conversation_history: 20
  max_tokens: 4000

  # Agent delegation — enables the /agent slash command in Journaler chat
  agent_backend: "auto"   # "auto" | "claude" | "mlx" (see Agent Delegation below)
  # anthropic_api_key: "" # or set ENGINEERING_HUB_ANTHROPIC_API_KEY env var
  # skills_dir: "~/org-roam/engineering-hub/skills"  # default: skills/ at repo root

  # Context management (all values below are defaults — omit to use defaults)
  context_management:
    compress_at: 0.70              # compress history when window is 70% full
    emergency_trim_at: 0.90        # force-trim if still critical after compression
    auto_clear_on_topic_shift: true
    notify_user_on_action: true    # prepend [Context compressed] notes to responses
    end_of_day_time: "00:00"       # daily conversation reset time
    inactivity_clear_minutes: 120  # auto-clear after 2h of silence
    capture_daily_to_memory: false # write daily summaries to memory.db
    reserved_for_generation: 2000  # tokens held back for model output
```

`model_path` is optional: if omitted, the Journaler falls back to `mlx.model_path` (the orchestrator MLX path), then to a built-in default (`mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit`).

#### Recommended models

| Model | Type | Weights | RAM required | Notes |
| --- | --- | --- | --- | --- |
| `mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit` | MoE Instruct | ~17GB | ~32GB | **Default.** Fastest, lowest RAM; Instruct-tuned for structured briefings |
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

**File loading**

| Command | Description |
| --- | --- |
| `/load <path>` | Load a file or directory into the current conversation context |
| `/load <path> -r` | Load a directory recursively |
| `/files` | List all files currently loaded, with character counts |
| `/files clear` | Remove all loaded files from context |

**Agent delegation**

| Command | Description |
| --- | --- |
| `/agent <type> <desc> [--project <id>] [--backend mlx\|claude]` | Delegate a task to a named agent and get the result inline. Types: `research`, `technical-writer`, `standards-checker`, `technical-reviewer`, `weekly-reviewer` |
| `/skills` | List all available agent delegation skills with descriptions and examples |

**Org-roam write operations**

| Command | Description |
| --- | --- |
| `/task <description>` | Add `- [ ] <description>` to today's journal under `* Overnight Agent Tasks` |
| `/done <fragment>` | Mark the first matching `- [ ]` item as `- [X]` with a `CLOSED:` timestamp |
| `/note <heading> :: <text>` | Append text under a heading in today's journal (creates the heading if absent) |
| `/find <title fragment>` | Search all org-roam files for a case-insensitive `#+title:` match; prints matching paths |

**General**

| Command | Description |
| --- | --- |
| `/help` | Show the full list of available slash commands |

Tasks added with `/task` use the `- [ ] @agent:` format understood by the Orchestrator, so they will be picked up and dispatched automatically. `/agent` tasks run immediately and return their output in the current chat turn. Loaded files are appended to the system prompt as fenced blocks and persist for the life of the chat session only.

To persist files for long-term retrieval across sessions, use `engineering-hub load` instead (see [Load Files into Context](#6-load-files-into-context)).

### Agent Delegation

The Journaler can delegate tasks directly to any named agent personality and return the result inline in the chat conversation — no need to write a journal task and wait for the overnight Orchestrator run.

#### The `/agent` command

```text
/agent <type> <description> [--project <id>] [--backend mlx|claude]
```

| Argument | Description |
| --- | --- |
| `<type>` | Agent personality: `research`, `technical-writer`, `standards-checker`, `technical-reviewer`, `weekly-reviewer` |
| `<description>` | Free-text task description |
| `--project <id>` | Optional Django project ID for context enrichment |
| `--backend mlx` | Use the local MLX model (reuses the Journaler's loaded model — no extra RAM) |
| `--backend claude` | Use the Claude API (requires `anthropic_api_key` in config) |

The default backend is controlled by `journaler.agent_backend` in config (`"auto"` prefers Claude when a key is present, otherwise falls back to MLX). The `--backend` flag overrides this per-command.

**Examples:**

```text
/agent research IBC 1207.3 occupant comfort requirements --project 42
/agent technical-writer draft executive summary for noise assessment --project 25 --backend claude
/agent standards-checker audit ASTM citations in draft report --backend mlx
/agent weekly-reviewer summarize this week's work and open loops
```

If no live backend is configured, the command falls back to writing the task to today's journal under `* Overnight Agent Tasks` for the Orchestrator to pick up on its next scan.

#### Backend selection

| Mode | Description |
| --- | --- |
| `"auto"` (default) | Claude API if `anthropic_api_key` is set, otherwise local MLX |
| `"claude"` | Always Claude API — errors if no key is configured |
| `"mlx"` | Always the local model — the Journaler's already-loaded MLX model is reused via a thin adapter, so no second model is loaded and no extra RAM is consumed |

#### The `/skills` command

Type `/skills` in the chat (or `GET http://localhost:18790/skills`) to list all available delegation skills with descriptions and example invocations.

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

To add a new delegation capability, drop a new `.yaml` file into `skills/` — no code changes needed. The Journaler loads all skill files at startup and injects a summary into its system prompt so the model knows what it can delegate and how.

### Org-Roam Write Skill

The Journaler's system prompt is enriched at startup with a `## Workspace Layout` block that tells the model:

- The absolute paths of `org_roam_dir`, `workspace_dir`, and the daily journal directory
- Org-roam format conventions: `:PROPERTIES:/:ID:/END:` drawer, `#+title:`, `#+filetags:`, heading levels, `TODO`/`DONE` keywords, active (`<…>`) and inactive (`[…]`) timestamp formats, `CLOSED:` annotation
- The `@agent:` task syntax the Orchestrator picks up
- All available slash commands

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

`conversation.jsonl` is append-only and serves as the permanent audit trail. Archived and compressed turns are written here even after the in-memory history is cleared, so any day's conversation can be reconstructed from the log.

## Orchestrator: Task-Driven Agents

The Orchestrator watches your workspace for `@agent:` task lines and dispatches them to specialized agents.

### Task Format (org-roam mode)

In your daily `.org` journal files under a `* Overnight Agent Tasks` heading:

```org
* Overnight Agent Tasks
- [ ] @research: Look up IBC 1207.3 amendments [[django://project/42]]
- [ ] @technical-writer: Draft response to reviewer comment #4
- [X] @research: Already completed task (skipped)
```

### Agent Types

| Agent | Purpose |
| --- | --- |
| `research` | Gather and synthesize technical information, summarize standards |
| `technical-writer` | Draft reports, protocols, and technical documentation |
| `standards-checker` | Verify compliance with ASTM/ISO standards |
| `technical-reviewer` | Review technical documents for accuracy |

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

- `llm_provider` - `"anthropic"` (cloud API) or `"mlx"` (local Apple Silicon)
- `django.api_url` - Django consultingmanager API endpoint
- `django.api_token` - API authentication token
- `anthropic.api_key` - Anthropic API key for Claude (used by the Orchestrator and Journaler `/agent --backend claude`)
- `anthropic.model` - Claude model to use (default: claude-sonnet-4-5-20250929)
- `workspace.dir` - Base workspace directory
- `mlx.model_path` - HuggingFace model ID for local MLX inference
- `journaler.*` - Journaler daemon settings (model, scan interval, briefing, chat, Slack)
- `journaler.model_context_window` - Model's actual context window in tokens (default: 32768)
- `journaler.agent_backend` - Backend for `/agent` delegation: `"auto"` (default), `"claude"`, or `"mlx"`
- `journaler.anthropic_api_key` - Optional per-journaler Anthropic key (falls back to `anthropic.api_key` if unset)
- `journaler.skills_dir` - Path to skills YAML directory (default: `skills/` at repo root)
- `journaler.context_management.*` - Token pressure thresholds, compression triggers, EOD reset time, topic-shift behavior
- `memory.*` - Vector memory settings (enabled, search_k, threshold)
- `ollama.*` - Ollama embedding model settings

## License

MIT
