## Learned User Preferences

- When Journaler behavior or slash commands change in a user-visible way, update README when asked or when shipping so docs match the CLI and YAML config.
- Long-form Markdown deliverables (reports, protocols, executive summaries) should route through the technical-writer persona; suggest `/agent technical-writer`, journal `/task` lines with `@technical-writer:`, optional `--project` for Django context, and `/skills` for the full persona list.
- If using a custom `.journaler/system_prompt.txt`, keep `{context_snapshot}` and carry over delegation or draft-routing guidance from the default template when you still want that behavior.

## Learned Workspace Facts

- `journaler chat` and `journaler start` share delegator setup (`build_delegator`), YAML skill summaries injected into the system prompt, and journaler config keys `agent_backend` (defaults to `mlx` for `/agent`), `skills_dir`, and optional journaler-specific Anthropic key; the daemon must re-append the skills block after periodic context refresh so personas are not dropped.
- `/agent` and `/skills` work in interactive `journaler chat` and via the daemon HTTP chat path; `/open` and `/edit` for arbitrary org-roam notes are implemented for interactive `journaler chat` only, not the Journaler HTTP `/chat` endpoint.
- `/load` limits are context-aware from `model_context_window`, history, and optional `journaler.load_max_context_fraction`, `load_max_chars_absolute`, `load_min_chars`, and `load_slack_tokens`; loaded files and corpus RAG injection count toward `TokenBudget` and `/budget`.
- Org-roam edits outside today's journal use `assert_org_path_under_roam` so targets stay under the configured roam root; `ConversationEngine` holds the session roam edit target for `/edit`.
- Domain: acoustic engineering consulting; Django REST project context, org-roam workspace, `skills/*.yaml` personas, and MLX-backed Journaler are first-class parts of the stack.
