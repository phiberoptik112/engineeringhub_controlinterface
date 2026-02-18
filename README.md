# Engineering Hub Control Interface

A persistent, agent-first workspace enabling collaboration between engineers and AI agents on technical projects. Designed for acoustic engineering consulting workflows, connecting to a Django backend (consultingmanager) via REST API.

## Overview

Engineering Hub provides a shared-notes-based workflow where engineers and AI agents collaborate through a markdown file (`shared-notes.md`). The system watches for task assignments, dispatches work to specialized agents, and syncs results back to the shared workspace.

### Key Features

- **Shared Notes Workflow**: Markdown-based task management with YAML frontmatter and `@agent: STATUS` syntax
- **Specialized Agents**: Research, technical-writer, and standards-checker agents with domain expertise
- **Django Integration**: Pulls project context, standards, and files from the consultingmanager API
- **File Watching**: Monitors workspace for changes and automatically dispatches agent tasks

## Requirements

- Python 3.11+
- Access to Anthropic API (Claude)
- Django consultingmanager backend (optional, for full functionality)

## Quick Start

### 1. Clone and Initialize

```bash
git clone <repository-url>
cd engineeringhub_controlinterface
source init.sh
```

### 2. Install Dependencies

```bash
pip install -e '.[dev]'
```

### 3. Configure

Copy the example configuration and add your API keys:

```bash
cp config/config.example.yaml config/config.yaml
```

Or set environment variables:

```bash
export ANTHROPIC_API_KEY="your-key-here"
export ENGINEERING_HUB_DJANGO_API_TOKEN="your-token-here"
```

You can also create a `.env` file in the project root:

```bash
ANTHROPIC_API_KEY=your-key-here
ENGINEERING_HUB_DJANGO_API_TOKEN=your-token-here
```

### 4. Run

```bash
engineering-hub
```

## Project Structure

```
engineeringhub_controlinterface/
├── src/engineering_hub/
│   ├── agents/          # Agent implementations
│   ├── cli.py           # Command-line interface
│   ├── config/          # Configuration handling
│   ├── context/         # Context building for agents
│   ├── core/            # Core abstractions
│   ├── django/          # Django API client
│   ├── notes/           # Shared notes parsing
│   └── orchestration/   # Task dispatch and coordination
├── config/
│   └── config.example.yaml
├── prompts/             # Agent system prompts
├── mock_server/         # Mock Django API for testing
└── tests/
    ├── unit/
    └── integration/
```

## Shared Notes Format

The shared notes file uses a specific format for task management:

```markdown
---
project_id: 123
django_url: http://localhost:8000
---

# Project Notes

## Tasks

@research: PENDING Research ASTM E336-17a requirements for field testing
@technical-writer: IN_PROGRESS Draft test protocol for IIC measurements

## References

- [[django://project/123]] - Main project context
- [[django://standard/E336-17a]] - ASTM standard reference
```

## Agent Types

| Agent | Purpose |
|-------|---------|
| `research` | Gather and synthesize technical information, summarize standards |
| `technical-writer` | Draft reports, protocols, and technical documentation |
| `standards-checker` | Verify compliance with ASTM/ISO standards |

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

- `django.api_url` - Django consultingmanager API endpoint
- `django.api_token` - API authentication token
- `anthropic.api_key` - Anthropic API key for Claude
- `anthropic.model` - Claude model to use (default: claude-sonnet-4-5-20250929)
- `workspace.dir` - Base workspace directory

## License

MIT
