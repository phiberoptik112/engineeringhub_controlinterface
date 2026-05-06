# Context pipeline diagnostic — operator runbook

## CLI

From the repo root (or any cwd with a valid `config.yaml` / env):

```bash
# Persist formatted context + checklist only (CI-friendly, no API keys required for LLM)
engineering-hub diagnostic context-pipeline --dry-run-context-only -v

# Full run: context + agent calls (requires configured llm_provider and credentials)
engineering-hub diagnostic context-pipeline -v

# Append the CONTEXT AUDIT block to system prompts for this run
engineering-hub diagnostic context-pipeline --context-audit-prompt -v

# Custom task suite and cap
engineering-hub diagnostic context-pipeline --tasks ./diagnostics/context_pipeline_tasks.yaml --max-tasks 5
```

Artifacts land under:

`{workspace}/outputs/diagnostics/context-pipeline/<run_id>/`

Per task:

- `formatted_context.txt` — full string passed with the user message
- `task.json` — parsed task metadata
- `checklist.json` — heuristic **CONTEXT DELIVERED** flags
- `corpus_audit_excerpt.jsonl` — lines from `retrieval_audit.jsonl` matching `task_id`, when configured
- `result.json` / `agent_response.md` — after agent execution (omitted in `--dry-run-context-only`)

Run root:

- `summary.json` — task index, paths, checklists; `failure_mode_counts` starts empty for manual or evaluator merge

## Config (orchestrator `start` / `run-once`)

Enable the same artifact capture for normal pending-task processing:

```yaml
diagnostics:
  context_pipeline:
    enabled: true
    context_audit_prompt: false   # optional: append CONTEXT AUDIT to prompts
    debug_context_max_chars: 50000
```

Environment (see `Settings`):

- `ENGINEERING_HUB_CONTEXT_PIPELINE_DIAGNOSTIC_ENABLED=true`
- `ENGINEERING_HUB_DIAGNOSTIC_CONTEXT_AUDIT_PROMPT=true` — same as `context_audit_prompt` in YAML

When enabled, the orchestrator logs a truncated copy of the formatted context at DEBUG (`-v`).

## Cursor sub-agents (parallel triage)

1. Run the CLI once and note `run_id` (printed path ends with `.../context-pipeline/<run_id>`).
2. Spawn several **Task** subagents in Cursor (e.g. `generalPurpose`), each assigned one `task_*` subdirectory.
3. Prompt each subagent: read `formatted_context.txt`, `agent_response.md` (if present), and `checklist.json`; fill one row of your **Task Log Template** (failure mode A–E + notes).
4. Merge rows; tally failure modes (3+ matching mode → primary diagnosis).

## Optional LLM evaluator (automation sketch)

Second model call can turn artifacts into structured JSON. Example **target schema** (store as `evaluator_result.json` beside the task):

```json
{
  "task_id": "string",
  "context_delivered_confirmed": {
    "project_overview": true,
    "memory_relevant": "yes|no|partially|absent",
    "corpus_relevant": "yes|no|partially|absent"
  },
  "agent_output_context_usage": {
    "cited_corpus": "yes|no|partially",
    "referenced_standard_sections": "yes|no|partially",
    "referenced_memory": "yes|no|absent",
    "used_insert_placeholders": "yes|no",
    "asked_clarifying_questions": "yes|no"
  },
  "failure_mode": "A|B|C|D|E",
  "notes_one_sentence": "string"
}
```

After manual or automated evaluation, merge `failure_mode` values into `summary.json` under `failure_mode_counts`.
