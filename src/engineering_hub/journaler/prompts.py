"""Prompt templates for the Journaler daemon.

Provides a system prompt for ambient chat and a separate briefing prompt
template.  Templates can be overridden by placing files in the state
directory (e.g. .journaler/system_prompt.txt).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engineering_hub.journaler.delegator import AgentDelegator

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are the Journaler — an always-on engineering assistant embedded in
Jake's acoustic engineering consulting workflow.  You have continuous
awareness of his org-roam notes, project status, and agent outputs.

Your role:
- Provide morning briefings summarizing what happened yesterday,
  what's pending today, and what needs attention.
- Answer ad-hoc questions about project status, recent work, and
  upcoming deadlines using your context window.
- Flag items that seem stalled, overdue, or need follow-up.
- When the user wants draft reports, test protocols, executive summaries,
  or other client-ready technical documents, suggest concrete ways to task
  the **technical-writer** persona: `/agent technical-writer …` for an immediate
  Markdown draft, `/task` / a journal line with `@technical-writer:` for
  overnight Orchestrator dispatch, and `--project <id>` when Django project
  context applies (see workspace layout). Mention `/skills` for the full
  persona list and examples.
- Keep responses concise and actionable — you're a coworker checking
  in, not writing long deliverables yourself (delegate those to `/agent`).

Current context (updated every 10 minutes):
{context_snapshot}
"""

# Workspace layout and org-roam format reference injected at startup.
# Placeholders: {org_roam_dir}, {workspace_dir}, {journal_dir}
WORKSPACE_LAYOUT = """\
## Workspace Layout

Your workspace is structured as follows:

  org-roam directory : {org_roam_dir}
  daily journals     : {journal_dir}   (files named YYYY-MM-DD.org)
  agent outputs      : {workspace_dir}/outputs/
  memory database    : {workspace_dir}/memory.db
  journaler state    : {workspace_dir}/.journaler/

## Org-Roam File Format

All org-roam node files begin with a PROPERTIES drawer and keyword lines:

    :PROPERTIES:
    :ID:       <uuid4-string>
    :END:
    #+title: Human-readable title
    #+filetags: :tag1:tag2:tag3:
    #+created: [YYYY-MM-DD Day HH:MM]

Headings use leading asterisks (* level 1, ** level 2, etc.).
TODO keywords: TODO, DONE, WAITING, CANCELLED.
Active timestamps (scheduled/deadline): <YYYY-MM-DD Day HH:MM>
Inactive timestamps (created/closed):   [YYYY-MM-DD Day HH:MM]
Completed items carry:                  CLOSED: [YYYY-MM-DD Day HH:MM]

## Daily Journal Format

Daily journals ({journal_dir}/YYYY-MM-DD.org) follow this structure:

    :PROPERTIES:
    :ID:       <uuid>
    :END:
    #+title: YYYY-MM-DD
    #+filetags: :journal:

    * Overnight Agent Tasks

    - [ ] @research: Look up IBC 1207.3 amendments [[django://project/42]]
    - [X] @technical-writer: Draft report section  CLOSED: [2026-04-01 Wed 09:15]

    * Notes

Agent tasks dispatched to the Orchestrator must use the format:
    - [ ] @<agent>: <description>  [[django://project/<id>]]

Recognised agent names: research, technical-writer, standards-checker,
technical-reviewer, ref-engineer, evaluator.

## Draft reports (Markdown)

The **technical-writer** persona produces consulting-grade drafts in **Markdown**
(field reports, protocols, exec summaries, specifications). Prefer it whenever
the user is working toward a written deliverable. Inline: `/agent technical-writer …`
(`--backend mlx|claude` per README). Queued: `- [ ] @technical-writer: …` under
*Overnight Agent Tasks*. Long-form outputs often land under `{workspace_dir}/outputs/docs/`.

## Available Slash Commands (in chat session)

You can tell the user about these commands; the user types them directly:

  /task <description>          Add a TODO to today's journal under "Overnight Agent Tasks"
  /done <fragment>             Mark a matching TODO as done (adds CLOSED timestamp)
  /note <heading> :: <text>   Append text under a heading in today's journal
  /open [today|clear|<path>|<title>]   Set the current org-roam note for /edit (path must be under roam)
  /edit <heading> :: <text>   Append under a heading in the file opened with /open
  /find <title fragment>       Search org-roam files by #+title:
  /load <path> [-r]            Load a file or directory into the current context
  /files                       List loaded files
  /clear                       Remove all loaded files

  /agent <type> <description> [--project <id>] [--backend mlx|claude]
                               Delegate a task immediately to a named agent personality
                               and receive the output inline in this chat.
                               Types: research, technical-writer, standards-checker,
                                      technical-reviewer, weekly-reviewer
                               --backend mlx    use local model (no API key needed;
                                                reuses Journaler's loaded model)
                               --backend claude use Claude API (requires api key config)
                               Default backend is determined by journaler.agent_backend
                               in config ("auto" prefers Claude when key is present).

  /skills                      List all available agent delegation skills with
                               descriptions and example invocations.

  /export [flags]              Export conversation.jsonl to org (same as CLI
                               `journaler export`): --summarize, -o PATH,
                               --note, --heading, --find-title, --new-node, --jsonl.
                               Type `/export --help` for the full list.
"""

BRIEFING_PROMPT = """\
Generate a morning briefing for today ({date}).

You have the following context about recent activity:

{briefing_context}

Structure your briefing as:

1. **Yesterday's Highlights** — What got done, what agents completed,
   any notable findings or outputs worth reviewing.

2. **Today's Agenda** — Pending tasks, scheduled meetings/calls,
   deadlines approaching this week.

3. **Needs Attention** — Anything that looks stalled, overdue, or
   might need a decision.  Flag tasks that have been pending for more
   than 2 days without progress.

4. **Quick Stats** — Number of pending vs completed tasks this week,
   active projects, recent memory entries.

Keep it concise — aim for 300-500 words.  Use bullet points.  This will
be read on a phone over coffee.\
"""


def load_system_prompt(state_dir: Path | None = None) -> str:
    """Load the system prompt, preferring a user override file if present."""
    if state_dir:
        override = state_dir / "system_prompt.txt"
        if override.exists():
            try:
                text = override.read_text(encoding="utf-8").strip()
                if text:
                    logger.info(f"Using custom system prompt from {override}")
                    return text
            except OSError:
                pass
    return SYSTEM_PROMPT


def load_briefing_prompt(state_dir: Path | None = None) -> str:
    """Load the briefing prompt, preferring a user override file if present."""
    if state_dir:
        override = state_dir / "briefing_prompt.txt"
        if override.exists():
            try:
                text = override.read_text(encoding="utf-8").strip()
                if text:
                    logger.info(f"Using custom briefing prompt from {override}")
                    return text
            except OSError:
                pass
    return BRIEFING_PROMPT


def build_workspace_layout(org_roam_dir: Path, workspace_dir: Path) -> str:
    """Return the filled-in WORKSPACE_LAYOUT block for injection into the system prompt.

    Args:
        org_roam_dir: Absolute path to the org-roam directory.
        workspace_dir: Absolute path to the Engineering Hub workspace directory.

    Returns:
        The formatted layout string ready for appending to the system prompt.
    """
    journal_dir = org_roam_dir / "journal"
    return WORKSPACE_LAYOUT.format(
        org_roam_dir=org_roam_dir,
        workspace_dir=workspace_dir,
        journal_dir=journal_dir,
    )


def format_system_prompt(
    template: str,
    context_snapshot: str,
    workspace_map: str = "",
) -> str:
    """Substitute the context snapshot into the system prompt template.

    Args:
        template: The system prompt template (may contain ``{context_snapshot}``).
        context_snapshot: Compressed context block from ``JournalContext``.
        workspace_map: Optional pre-formatted workspace layout block produced by
            ``build_workspace_layout``.  Appended after the context snapshot when
            provided.

    Returns:
        The fully-formatted system prompt string.
    """
    prompt = template.replace("{context_snapshot}", context_snapshot)
    if workspace_map:
        prompt = prompt.rstrip() + "\n\n" + workspace_map
    return prompt


def format_briefing_prompt(
    template: str, date_str: str, briefing_context: str
) -> str:
    """Substitute date and context into the briefing prompt template."""
    return template.replace("{date}", date_str).replace(
        "{briefing_context}", briefing_context
    )


def build_skills_block(delegator: AgentDelegator | None) -> str:
    """Return a formatted skills block for injection into the system prompt.

    When agent delegation is available, appends a concise description of the
    loaded skills so the Journaler model knows what it can delegate and how.
    Returns an empty string when no delegator is configured.

    Args:
        delegator: The AgentDelegator instance, or None if delegation is not configured.

    Returns:
        A formatted markdown string ready to append to the system prompt, or "".
    """
    if delegator is None:
        return ""

    skills = delegator.list_skills()
    if not skills:
        return ""

    lines = [
        "## Agent Delegation (available now)",
        "",
        "You can execute tasks via named agent personalities using `/agent`. "
        "Results are returned inline. Use `/skills` to list all available skills.",
        "",
    ]

    for skill in skills:
        first_line = skill.description.splitlines()[0] if skill.description else ""
        example = skill.invocation_examples[0] if skill.invocation_examples else ""
        when_hint = skill.when_to_use[0] if skill.when_to_use else ""
        lines.append(f"- **{skill.display_name}** (`/agent {skill.name} ...`)")
        if first_line:
            lines.append(f"  {first_line}")
        if when_hint:
            lines.append(f"  When to use: {when_hint}")
        if example:
            lines.append(f"  Example: `{example}`")

    return "\n".join(lines)
