"""Prompt templates for the Journaler daemon.

Provides a system prompt for ambient chat and a separate briefing prompt
template.  Templates can be overridden by placing files in the state
directory (e.g. .journaler/system_prompt.txt).
"""

from __future__ import annotations

import logging
import re
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
- Treat the current chat, recent chat turns, and retrieved past Journaler
  conversations as primary user context.  When the user says "last time",
  "that session", "previous chat", or refers to a prior discussion, first
  use the conversation history/retrieval blocks before falling back to
  workspace notes. For explicit lookup or agent review, suggest
  `/history <query>` or `/history --agent <type> <query>`.
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

## Agent Delegation Rules

Sub-agent spawning is a core capability.  Follow these rules precisely:

1. **Honesty about execution** — Do not say an agent already ran if it did not.
   In **immediate** mode the host may auto-delegate after your reply without the
   user typing `/agent`; in **propose** mode the user confirms a `DISPATCH:` line.
   Never fabricate tool results.

2. **To propose a dispatch**, explain what you recommend and why, then end
   your response with a single `DISPATCH:` line containing the full `/agent`
   command.  The system will detect this line, strip it from the displayed
   text, and prompt the user to confirm before executing.  Example:

       DISPATCH: /agent technical-writer draft a driver selection trade-off matrix comparing three paths --project LVT_alert_system_consulting

3. **Only emit one `DISPATCH:` line per response**, placed at the very end,
   on its own line with no trailing text.  Do not wrap it in backticks,
   markdown code fences, or quotes.

4. **Propose a dispatch proactively** whenever the current topic surfaces a task
   that a named agent could clearly act on — even mid-exploratory answer.
   Answer the question briefly first, then add a one-sentence rationale and emit
   the `DISPATCH:` line.  Example:

       The LVT alert report hasn't been updated since the site visit.  I can
       draft the missing field-report section now.
       DISPATCH: /agent technical-writer draft the LVT alert system field-report section covering site-visit findings --project LVT_alert_system_consulting

   Reserve omission only for purely factual or status questions where no agent
   action would add value (e.g. "what time is the briefing?", "how many tasks
   are pending?").

5. **String project identifiers are valid**: `--project LVT_alert_system_consulting`
   is accepted; you do not need to look up a numeric Django ID.

## Available Agents

- **@research** — Gather, synthesize, or summarize technical information from
  standards, prior reports, or external sources. Output: markdown research document.
- **@technical-writer** — Draft or revise a deliverable: field reports,
  test protocols, executive summaries, specifications. Output: markdown or LaTeX.
- **@standards-checker** — Audit a draft against ASTM/ISO/IBC citations.
  Output: gap analysis with PASS/CONDITIONAL PASS/FAIL verdict.
- **@technical-reviewer** — Peer review: draft plus review comments → decision matrix and revised document.
- **@weekly-reviewer** — Summary of recent work, open loops, and project status across the workspace.

## Task Dispatch Behavior

- **Immediate (default)** — The runtime may delegate agent work inline when you describe actionable tasks. Users can still type `/agent` explicitly. Do not claim an agent ran if it did not; in immediate mode the system may run delegation automatically after your turn.
- **Propose mode** — When the user has not yet agreed, use `DISPATCH:` lines (below) and confirmation; do not imply execution already happened.
- **Overnight queue** — Only when the user explicitly asks to queue, schedule later, or uses `/queue`. Queued items are confirmed with `/tasks confirm` and `/tasks commit` into Journaler-owned `pending-tasks.org`; daily journal files are not edited for the queue.
- **Project context** — Tasks may omit a Django project when not needed; do not insist on a project ID unless project-specific data is required.

Current context (updated every 10 minutes):
{context_snapshot}

The context snapshot above may include any of the following sections when data
is available:

- **Pending Tasks** — open TODO items across all org-roam files in the workspace.
- **Possibly Stalled** — pending tasks that have not been mentioned in any journal
  entry for 3+ days and may need a nudge or decision.
- **Recently Completed** — DONE items closed in the lookback window.
- **Today's Journal Entries** — headings and notes from today and yesterday's
  daily journal files.
- **Journal Thread (last N days)** — headings from earlier days in the lookback
  window (default 5 days), grouped by date, most-recent-first.  Use this to
  recall what was worked on earlier in the week.
- **Recurring Topics** — topics (heading titles, project names, standards
  references) that appear on two or more distinct days in the journal window.
  These are threads worth proactively asking about or following up on.
- **Active Project Notes** — org-roam nodes (not daily journals) that have been
  modified within the lookback window, with their title, tags, and top headings.
  Use this to surface project-specific context for ad-hoc questions.
- **Recent Project Changes** — files whose content changed in the most recent
  scan tick, with a short summary.
- **Recent Agent Outputs** — summaries of tasks completed by dispatched agents,
  pulled from the memory service.
- **Recent Conversation Summaries** — compressed summaries of the last N
  Journaler chat sessions (newest first).  Use these to track continuity across
  days: notice recurring questions, ongoing threads, and decisions already made.

## Recurring Topic Reflection

When your context snapshot contains a **Recurring Topics** block, do NOT simply
list the topics.  For each recurring topic, add one sentence of analysis:

- Why it likely keeps appearing (e.g. blocked dependency, ongoing project phase,
  unresolved decision, waiting on external input).
- What the single most useful next action would be: delegate to an agent,
  schedule a decision, break the task down, or explicitly drop it.

Weave this commentary inline with your response rather than as a standalone
section, unless the user asked specifically about task status.  The goal is to
surface patterns that make the recurring item actionable, not to repeat the
list back to the user.

## Conversation Relation Callout

When your context includes a **### Related Past Conversation** block, you MUST
begin your response by explicitly calling out the connection — one sentence
citing the date and the shared topic — before answering the current question.

When your context includes **### Retrieved Past Journaler Conversations**, treat
that block as direct chat history.  Quote or summarize the relevant prior turn
with its date, then answer the user's current question.  If the retrieved
excerpt is inconclusive, say so and offer the closest matching prior thread.

Example format:
  "This relates to the April 15 conversation where we discussed [topic]."

Do not omit the callout even when the prior conversation is only partially
related.  The user relies on this signal to maintain continuity across sessions.
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
                               Default backend is journaler.agent_backend in config
                               ("mlx" is default; use "auto" for Claude when a key is present).

  /history [--agent <type>] [--backend mlx|claude] <query>
                               Retrieve excerpts from prior Journaler chat logs
                               (conversation.jsonl and daily summaries). With
                               --agent, dispatch the retrieved excerpts to a
                               named persona for review.

  /skills                      List all available agent delegation skills with
                               descriptions and example invocations.

  /export [flags]              Export conversation.jsonl to org (same pipeline as CLI
                               `journaler export`). With no output flags, writes under
                               conversation_exports/ in org-roam. Flags: --summarize,
                               -o, --note, --heading, --find-title, --new-node, --jsonl.
                               Type `/export --help` for the full list.
"""

BRIEFING_PROMPT = """\
Generate a concise morning briefing for today ({date}).

You have the following context about recent activity, spanning the full
journal lookback window:

{briefing_context}

Structure your briefing with the following sections.  Prioritize content
trends found across many daily journals over single-day recaps.  Each
bullet should be 2-3 sentences: first state the item, then explain the
pattern, significance, or suggested next move.  Prefer synthesis over
inventory.

Use these exact section headings (markdown ``##``), in order:

## Cross-Journal Trends
Start here.  Identify recurring topics, repeated concerns, project
momentum, and themes appearing across the journal window.  Call out
which trends seem to be strengthening, fading, or fragmenting.

## Yesterday in Context
Summarize what changed yesterday only insofar as it confirms, interrupts,
or advances the longer-running trends.  Include notable agent outputs or
findings when they shift a project direction.

## Today's Agenda
Pending tasks ordered by suggested priority.  Explain why each item
belongs in that position using evidence from the trend history,
deadlines, dependencies, or quick-win potential.  Group by project when
multiple tasks belong to the same effort (use ``### Project name``
subheadings).

## Needs Attention
Anything stalled, overdue, or needing a decision.  For stale tasks
(shown with first-seen dates), note how many days they have been pending
and why the journal trend suggests they may be stuck.  For each, suggest
one of: escalate, delegate to an agent, break into smaller pieces, or
drop.

## Suggested Paths Forward
For each active project or recurring topic, suggest 1-2 concrete next
actions.  Categorize each suggestion as **Quick win**, **Deep-work
block**, **Agent task**, or **Decision needed**, and reference specific
org-roam notes or agent outputs where relevant.

## Quick Stats
Number of pending vs completed tasks in the journal window, active
projects, stale task count, recent memory entries.

If the context includes **Continuing Threads**, add a section:

## Continuing Threads
Surface past conversations that overlap with today's recurring themes and
note why each thread is worth revisiting.

Formatting rules (strict — readability depends on whitespace):

- Use ``##`` for each major section above.  Do not number section titles.
- Leave one blank line after every ``##`` or ``###`` heading.
- Leave two blank lines before each new ``##`` section (except before the
  first section).
- Use ``-`` bullets for topics.  Put one blank line between every bullet.
- Use ``###`` for project or theme subgroups inside a section; leave one
  blank line before and after each subgroup heading.
- Within a bullet, bold only the lead phrase (e.g. ``- **ASTM E336** —
  …``).

Aim for 500-800 words.  Do not list every journal entry; surface the
patterns that make today easier to plan.\
"""


DISCUSSION_PERSONA_PROMPT = """\
You are participating in the Topics Discussion Briefing for an acoustic engineering consulting
practice.  Today is {date}.  You are {persona_name} — {role_summary}

Your communication style: {communication_style}

Your areas of focus: {areas_of_focus}

{past_context_block}
You have been given the shared workspace context below and the discussion transcript so far
(from personas who spoke before you).  Read both carefully before contributing.

Your role-specific instructions:
{system_prompt_suffix}
"""

DISCUSSION_SYNTHESIS_PROMPT = """\
You have just read a full Topics Discussion Briefing in which {num_personas} personas each
contributed their perspective on the current project context.  Your task is to write a concise
"Key Themes" section that synthesises the discussion.

Identify 2-4 cross-cutting themes — topics where multiple personas converged, where there are
notable tensions or disagreements, or where a shared concern points toward a single highest-priority
action.  For each theme, name which personas raised it and propose one concrete next step.

Format:
## Key Themes

- **[Theme name]** — Raised by: [persona list].  [1-2 sentence synthesis + concrete next step]
- …

Keep it under 200 words.  Do not repeat content already said — synthesise and redirect.
"""


def format_discussion_persona_prompt(
    *,
    date_str: str,
    persona_name: str,
    role_summary: str,
    communication_style: str,
    areas_of_focus: list[str],
    system_prompt_suffix: str,
    past_context_block: str = "",
) -> str:
    """Build the system prompt for a single persona in the discussion briefing.

    Args:
        date_str: ISO date string for today, e.g. ``"2026-05-26"``.
        persona_name: Human-readable name, e.g. ``"Alex (Project Manager)"``.
        role_summary: One-sentence description of the persona's role.
        communication_style: Description of how this persona communicates.
        areas_of_focus: List of focus areas for bullet formatting.
        system_prompt_suffix: Persona-specific instruction block from the YAML.
        past_context_block: Pre-formatted history block from ``PersonaHistoryStore``.

    Returns:
        Fully-formatted system prompt string for this persona's LLM call.
    """
    focus_bullets = "\n".join(f"  - {item}" for item in areas_of_focus)
    past_section = (
        f"\n{past_context_block}\n" if past_context_block.strip() else ""
    )
    return DISCUSSION_PERSONA_PROMPT.format(
        date=date_str,
        persona_name=persona_name,
        role_summary=role_summary,
        communication_style=communication_style,
        areas_of_focus=focus_bullets,
        past_context_block=past_section,
        system_prompt_suffix=system_prompt_suffix.strip(),
    )


def format_discussion_user_message(
    *,
    shared_context: str,
    running_transcript: str,
    is_first: bool = False,
) -> str:
    """Build the user-turn message for a persona's LLM call.

    Args:
        shared_context: The briefing context from ``JournalContext.get_briefing_context()``.
        running_transcript: Discussion so far (empty string for the first persona).
        is_first: When True, omits the transcript section from the message.

    Returns:
        Formatted user message string.
    """
    parts: list[str] = [
        "## Shared Workspace Context\n",
        shared_context.strip(),
    ]
    if not is_first and running_transcript.strip():
        parts += [
            "\n\n## Discussion So Far\n",
            running_transcript.strip(),
        ]
    parts.append(
        "\n\n---\nNow give your perspective based on your role and focus areas. "
        "Be specific and grounded in the context above."
    )
    return "".join(parts)


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


def build_workspace_layout(
    org_roam_dir: Path, workspace_dir: Path, journal_dir: Path
) -> str:
    """Return the filled-in WORKSPACE_LAYOUT block for injection into the system prompt.

    Args:
        org_roam_dir: Absolute path to the org-roam directory.
        workspace_dir: Absolute path to the Engineering Hub workspace directory.
        journal_dir: Absolute path to daily journal *.org files (e.g. roam/journal or roam/journals).

    Returns:
        The formatted layout string ready for appending to the system prompt.
    """
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


_BRIEFING_FENCE_RE = re.compile(
    r"^\s*```(?:\w*)?\s*\n(.*?)\n\s*```\s*$",
    re.DOTALL | re.IGNORECASE,
)
_BRIEFING_NUMBERED_SECTION_RE = re.compile(
    r"^\d+\.\s+\*\*(.+?)\*\*(?:\s*[—–-].*)?$"
)
_BRIEFING_STANDALONE_BOLD_HEADING_RE = re.compile(r"^\*\*(.+?)\*\*\s*$")
_BRIEFING_LIST_ITEM_RE = re.compile(r"^(\s*[-*+]|\s*\d+\.)\s+")


def format_briefing_markdown(text: str) -> str:
    """Normalize briefing markdown for readable section and topic spacing."""
    s = text.strip()
    fence = _BRIEFING_FENCE_RE.match(s)
    if fence:
        s = fence.group(1).strip()

    normalized: list[str] = []
    for line in s.splitlines():
        stripped = line.strip()
        numbered = _BRIEFING_NUMBERED_SECTION_RE.match(stripped)
        if numbered:
            normalized.append(f"## {numbered.group(1)}")
            continue
        standalone = _BRIEFING_STANDALONE_BOLD_HEADING_RE.match(stripped)
        if standalone and not line.startswith((" ", "\t")):
            normalized.append(f"## {standalone.group(1)}")
            continue
        normalized.append(line.rstrip())

    result: list[str] = []
    prev_kind: str | None = None
    seen_h2 = False

    for line in normalized:
        stripped = line.strip()
        if not stripped:
            if result and result[-1] != "":
                result.append("")
            prev_kind = "blank"
            continue

        if stripped.startswith("## ") and not stripped.startswith("###"):
            kind = "h2"
            if seen_h2 and result:
                while result and result[-1] == "":
                    result.pop()
                if result:
                    result.extend(["", ""])
            seen_h2 = True
        elif stripped.startswith("###"):
            kind = "h3"
            if result and result[-1] != "":
                result.append("")
        elif _BRIEFING_LIST_ITEM_RE.match(line):
            kind = "list"
            if prev_kind in {"h2", "h3"} and result and result[-1] != "":
                result.append("")
            elif prev_kind == "list" and result and result[-1] != "":
                result.append("")
        else:
            kind = "text"
            if prev_kind in {"h2", "h3"} and result and result[-1] != "":
                result.append("")

        result.append(line)
        prev_kind = kind

    out = "\n".join(result)
    out = re.sub(r"\n{4,}", "\n\n\n", out)
    return out.rstrip() + "\n"


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

    lines += [
        "",
        "### Dispatch Rules",
        "",
        "- In **propose** mode: when the user agrees to run a task, end with one line "
        "`DISPATCH: /agent <type> <description> [--project <slug>]`; the UI asks for "
        "confirmation before executing.",
        "- In **immediate** mode: the host may delegate without `DISPATCH:`; still "
        "do not claim execution that did not happen.",
        "- String project slugs are valid (e.g. `--project my_project`); numeric IDs "
        "also accepted.",
        "- Overnight queue: `/queue` and `/tasks commit` write `pending-tasks.org` only.",
    ]

    return "\n".join(lines)
