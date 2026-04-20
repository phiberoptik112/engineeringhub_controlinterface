## Learned User Preferences

- When Journaler behavior or slash commands change in a user-visible way, update README when asked or when shipping so docs match the CLI and YAML config.
- Long-form Markdown deliverables (reports, protocols, executive summaries) should route through the technical-writer persona; suggest `/agent technical-writer`, journal `/task` lines with `@technical-writer:`, optional `--project` for Django context, and `/skills` for the full persona list.
- If using a custom `.journaler/system_prompt.txt`, keep `{context_snapshot}` and carry over delegation or draft-routing guidance from the default template when you still want that behavior.

## Learned Workspace Facts

- `journaler chat` and `journaler start` share delegator setup (`build_delegator`), YAML skill summaries injected into the system prompt, and journaler config keys `agent_backend` (defaults to `mlx` for `/agent`), `skills_dir`, and optional journaler-specific Anthropic key; the daemon must re-append the skills block after periodic context refresh so personas are not dropped.
- `/agent` and `/skills` work in interactive `journaler chat` and via the daemon HTTP chat path; `/open`, `/edit`, `/load_browse`, `/agent_browse`, and `/edit_browse` are implemented for interactive `journaler chat` only, not the Journaler HTTP `/chat` endpoint.
- `/load` limits are context-aware from `model_context_window`, history, and optional `journaler.load_max_context_fraction`, `load_max_chars_absolute`, `load_min_chars`, and `load_slack_tokens`; loaded files and corpus RAG injection count toward `TokenBudget` and `/budget`.
- Org-roam edits outside today's journal use `assert_org_path_under_roam` so targets stay under the configured roam root; `ConversationEngine` holds the session roam edit target for `/edit`.
- Domain: acoustic engineering consulting; Django REST project context, org-roam workspace, `skills/*.yaml` personas, and MLX-backed Journaler are first-class parts of the stack.
- `/load_browse`, `/agent_browse`, and `/edit_browse` are curses-based TUI browsers in `file_browser.py`; all support arrow-key navigation, Shift+Arrow fast scroll (5 lines), Page Up/Down; `/load_browse` has multi-select (Space), `/agent_browse` and `/edit_browse` are single-select.
- Anthropic API key is no longer stored on `JournalerConfig`; `build_delegator` accepts `SecretStr | str` and credentials resolve from `Settings` / `ENGINEERING_HUB_*` env vars at the call site in `run_daemon` and `journaler chat`.
- Default `max_tokens` is 4096 (aligned with `mlx_lm.server --max-tokens 4096`); `TokenBudget.reserved_for_generation` is `max(cfg.reserved_for_generation, max_tokens)` so prompt budget never overestimates available input space.
- `journal.org_journal_dir` is the canonical daily journal path (e.g. `~/org-roam/journals`); `org_roam_dir` is derived as its parent; `journaler.scan_org_roam_tree` (default true) controls full-tree vs journal+watch_dirs scanning; `journal_lookback_days` (5) and `journal_max_files` (5) cap parsed dailies.
- Default morning briefing time is 09:00; configurable via `journaler.briefing_time` in YAML or `Settings.journaler_briefing_time`.
- `journaler export` reads `conversation.jsonl` from the state directory; supports `--format raw` (deterministic org) and `--summarize` (MLX one-shot summary + TODO extraction); CLI default is stdout, with `-o`, `--note`, `--find-title`, and `--new-node`. In `journaler chat`, bare `/export` writes a new node under `<org-roam>/conversation_exports/` unless one of those targets is set.
- Model switching uses named profiles under `journaler.models` in YAML config with `/model <profile>` slash command at runtime; `JournalerModelSpec` bundles per-model `model_path`, sampling params, `model_context_window`, and `enable_thinking` for thinking-mode templates (e.g. Qwen3).
