# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Engineering Hub Control Interface - A persistent, agent-first workspace enabling collaboration between engineers and AI agents on technical projects. Connects to a Django backend (consultingmanager) via REST API while maintaining a shared-notes-based workflow for acoustic engineering work.

## Technology Stack

- **Python 3.11+**
- **Anthropic Python SDK** for Claude agents
- **watchdog** for file monitoring
- **pyyaml** for configuration
- **requests** for Django API client
- **rich** for terminal UI

## Planned Architecture

```
engineering_hub/
├── orchestrator.py      # Main orchestration, file watching, task dispatch
├── notes_manager.py     # Parse/update shared-notes.md
├── context_manager.py   # Build project context from Django API
├── agent_worker.py      # Execute agent tasks via Claude API
├── django_client.py     # Django REST API client
├── agent_tools.py       # Tools agents can invoke (get_project_file, etc.)
└── formatters.py        # Agent-specific context formatters
```

## Key Concepts

- **Shared Notes File**: Markdown file (`shared-notes.md`) serves as the single source of truth with YAML frontmatter, `@agent: STATUS` task syntax, and `[[django://project/ID]]` references
- **Agent Types**: research, technical-writer, standards-checker - each with specialized prompts and context formatting
- **Task Statuses**: PENDING, IN_PROGRESS, COMPLETED, BLOCKED
- **Django Integration**: REST API at `/api/projects/{id}/context/` provides project scope, standards, files

## Configuration

Uses `config.yaml` for Django API connection, workspace paths, and agent configurations. API tokens stored in `DJANGO_API_TOKEN` environment variable.

## Domain Context

Acoustic engineering consulting - projects involve ASTM/ISO standards compliance (E336-17a, E1007-16, etc.), technical specifications, test protocols, and client reports.
