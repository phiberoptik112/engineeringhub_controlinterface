# Engineering Hub Control Interface

A persistent, agent-first workspace enabling collaboration between engineers and AI agents on technical projects. Designed for acoustic engineering consulting workflows, connecting to a Django backend (consultingmanager) via REST API.

## Overview

Engineering Hub provides two complementary modes of AI collaboration:

1. **Orchestrator** (task-driven) -- watches your org-roam journal for `@agent:` task lines, dispatches work to specialized agents via Claude API or local MLX models, and writes results back to the workspace.
2. **Journaler** (ambient) -- a persistent daemon that runs a local ~32B model via MLX, continuously monitors your org-roam workspace, delivers morning briefings, and responds to ad-hoc questions through an HTTP chat endpoint.

They coexist cleanly: the Orchestrator processes explicit tasks while the Journaler maintains ambient awareness. The Journaler is read-only on the workspace and writes only to its own `.journaler/` state directory.

### Key Features

- **Org-roam Integration**: Tasks live in daily `.org` journal files using `- [ ] @agent:` syntax
- **Specialized Agents**: Research, technical-writer, standards-checker, and more with domain expertise
- **Django Integration**: Pulls project context, standards, and files from the consultingmanager API
- **File Watching**: Monitors workspace for changes and automatically dispatches agent tasks
- **Local MLX Models**: Run agents on Apple Silicon via `mlx-lm` with HuggingFace model IDs
- **Journaler Daemon**: Always-on ambient listener with morning briefings, HTTP chat, and Slack integration
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
```

### 6. Load Files into Context

**In a live `journaler chat` session** — use slash commands to inject file content directly into the model's context for the current conversation:

```
/load path/to/file.md           Load a single file
/load path/to/dir/              Load all supported files in a directory
/load path/to/dir/ -r           Load recursively
/files                          List currently loaded files (with sizes)
/clear                          Remove all loaded files from context
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
│   │   ├── daemon.py        # Main loop, scheduler, signal handling
│   │   ├── context.py       # Org-roam scanner with mtime-based diff
│   │   ├── engine.py        # ConversationEngine + ConversationalMLXBackend
│   │   ├── chat_server.py   # HTTP endpoint (POST /chat, GET /status)
│   │   ├── org_parser.py    # Focused org-mode parser
│   │   ├── prompts.py       # System and briefing prompt templates
│   │   ├── slack.py         # Slack webhook poster
│   │   └── models.py        # ContextSnapshot, ScanState, OrgEntry
│   ├── mcp/             # FastMCP server integration
│   ├── memory/          # Vector memory (SQLite + Ollama embeddings)
│   ├── notes/           # Journal/org-roam parsing and task dispatch
│   └── orchestration/   # Orchestrator, dispatcher, file watcher
├── config/
│   └── config.example.yaml
├── prompts/             # Agent system prompts
└── tests/
```

## Journaler: Ambient Listener

The Journaler is a persistent daemon that runs a local ~32B model on Apple Silicon via MLX, continuously monitors your org-roam workspace, and provides ambient awareness of your projects.

### How It Works

- **Scans** your org-roam directory every 10 minutes (mtime-based incremental diff)
- **Extracts** headings, TODO/DONE items, timestamps, and `@agent:` tasks from `.org` files
- **Reads** recent agent outputs from `memory.db` via `MemoryService.browse_recent()`
- **Compresses** everything into a rolling context snapshot (~4000 tokens)
- **Generates** a morning briefing at a configurable time (default 7:00 AM)
- **Responds** to ad-hoc questions via an HTTP chat endpoint on `localhost:18790`
- **Posts** briefings and alerts to Slack via incoming webhooks (optional)

### Configuration

Add a `journaler:` section to your `config.yaml`:

```yaml
journaler:
  enabled: true
  model_path: "mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit"  # see model table below
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

# Check daemon status
curl http://localhost:18790/status

# Get the latest briefing
curl http://localhost:18790/briefing
```

### Daemon Management (macOS)

The simplest approach is tmux:

```bash
tmux new-session -d -s journaler 'engineering-hub journaler start'
```

For always-on operation, use a launchd plist at `~/Library/LaunchAgents/com.engineeringhub.journaler.plist` with `KeepAlive` and `RunAtLoad` set to true.

### Interactive Chat: Slash Commands

While in `engineering-hub journaler chat`, any input starting with `/` is handled as a command rather than forwarded to the model:

| Command | Description |
| --- | --- |
| `/load <path>` | Load a file or directory into the current conversation context |
| `/load <path> -r` | Load a directory recursively |
| `/files` | List all files currently loaded, with character counts |
| `/clear` | Remove all loaded files from context |
| `/help` | Show the list of available slash commands |

Loaded files are appended to the system prompt as fenced blocks under a `## Loaded Files` heading, making them visible on every subsequent turn. They persist for the life of the chat session only — they are not written to disk or stored in memory.

To persist files for long-term retrieval across sessions, use `engineering-hub load` instead (see [Load Files into Context](#6-load-files-into-context)).

### State Files

The Journaler writes to `<workspace_dir>/.journaler/`:

```text
.journaler/
├── state.json           # File mtimes for incremental scanning
├── context_cache.json   # Compressed rolling context snapshot
├── conversation.jsonl   # Chat history log
└── briefings/           # Generated morning briefings (YYYY-MM-DD.md)
```

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
- `anthropic.api_key` - Anthropic API key for Claude
- `anthropic.model` - Claude model to use (default: claude-sonnet-4-5-20250929)
- `workspace.dir` - Base workspace directory
- `mlx.model_path` - HuggingFace model ID for local MLX inference
- `journaler.*` - Journaler daemon settings (model, scan interval, briefing, chat, Slack)
- `memory.*` - Vector memory settings (enabled, search_k, threshold)
- `ollama.*` - Ollama embedding model settings

## License

MIT
