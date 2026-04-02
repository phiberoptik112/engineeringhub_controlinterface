"""Prompt templates for the Journaler daemon.

Provides a system prompt for ambient chat and a separate briefing prompt
template.  Templates can be overridden by placing files in the state
directory (e.g. .journaler/system_prompt.txt).
"""

from __future__ import annotations

import logging
from pathlib import Path

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
- Keep responses concise and actionable — you're a coworker checking
  in, not writing a report.

Current context (updated every 10 minutes):
{context_snapshot}
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


def format_system_prompt(template: str, context_snapshot: str) -> str:
    """Substitute the context snapshot into the system prompt template."""
    return template.replace("{context_snapshot}", context_snapshot)


def format_briefing_prompt(
    template: str, date_str: str, briefing_context: str
) -> str:
    """Substitute date and context into the briefing prompt template."""
    return template.replace("{date}", date_str).replace(
        "{briefing_context}", briefing_context
    )
