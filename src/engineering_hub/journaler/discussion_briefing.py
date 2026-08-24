"""Discussion Briefing generator: multi-persona roundtable over shared project context.

Each persona in the discussion makes one LLM call, sees the shared workspace context plus
the running transcript from personas who spoke before it, and contributes a role-grounded
statement.  Statements are persisted to per-persona history so future discussions can inject
continuity.

A final synthesis pass produces a "Key Themes" section summarising the discussion.

Typical usage::

    generator = DiscussionBriefingGenerator.from_personas_dir(
        personas_dir=config.personas_dir,
        history_store=PersonaHistoryStore(config.state_dir / "personas"),
        engine=engine,
        max_tokens_per_persona=config.discussion_max_tokens_per_persona,
    )
    markdown = generator.generate(shared_context, date_str="2026-05-26")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from engineering_hub.journaler.persona_history import PersonaHistoryStore
from engineering_hub.journaler.prompts import (
    DISCUSSION_SYNTHESIS_PROMPT,
    format_discussion_persona_prompt,
    format_discussion_user_message,
)

if TYPE_CHECKING:
    from engineering_hub.journaler.engine import ConversationEngine

logger = logging.getLogger(__name__)


@dataclass
class PersonaDef:
    """A loaded persona definition from a YAML file."""

    id: str
    display_name: str
    role_summary: str
    communication_style: str
    areas_of_focus: list[str] = field(default_factory=list)
    system_prompt_suffix: str = ""


def load_personas(personas_dir: Path) -> list[PersonaDef]:
    """Load all persona YAML files from ``personas_dir``.

    Files are sorted alphabetically so discussion order is deterministic.
    Returns an empty list (with a warning) if the directory doesn't exist.
    """
    if not personas_dir.exists():
        logger.warning("Personas directory not found: %s", personas_dir)
        return []

    personas: list[PersonaDef] = []
    for path in sorted(personas_dir.glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            logger.warning("Failed to load persona %s: %s", path, exc)
            continue

        persona_id = raw.get("id") or path.stem
        display_name = raw.get("display_name", persona_id)
        role_summary = raw.get("role_summary", "").strip()
        communication_style = raw.get("communication_style", "").strip()
        areas = raw.get("areas_of_focus") or []
        suffix = raw.get("system_prompt_suffix", "").strip()

        if not role_summary:
            logger.warning("Persona %s missing role_summary — skipping", path.name)
            continue

        personas.append(
            PersonaDef(
                id=persona_id,
                display_name=display_name,
                role_summary=role_summary,
                communication_style=communication_style,
                areas_of_focus=list(areas),
                system_prompt_suffix=suffix,
            )
        )

    logger.info("Loaded %d personas from %s", len(personas), personas_dir)
    return personas


class DiscussionBriefingGenerator:
    """Orchestrates the multi-persona discussion briefing.

    Call :meth:`generate` to produce a full markdown discussion for the given date.
    The generator is stateless beyond its configuration — multiple calls produce
    independent discussions (though they share the history store for continuity).
    """

    def __init__(
        self,
        personas: list[PersonaDef],
        history_store: PersonaHistoryStore,
        engine: ConversationEngine,
        *,
        max_tokens_per_persona: int = 1024,
        persona_lookback_days: int = 7,
        max_history_statements: int = 10,
    ) -> None:
        self._personas = personas
        self._history = history_store
        self._engine = engine
        self._max_tokens = max_tokens_per_persona
        self._lookback = persona_lookback_days
        self._max_history = max_history_statements

    @classmethod
    def from_personas_dir(
        cls,
        *,
        personas_dir: Path,
        history_store: PersonaHistoryStore,
        engine: ConversationEngine,
        max_tokens_per_persona: int = 1024,
        persona_lookback_days: int = 7,
    ) -> "DiscussionBriefingGenerator":
        """Convenience constructor that loads personas from a directory."""
        personas = load_personas(personas_dir)
        return cls(
            personas=personas,
            history_store=history_store,
            engine=engine,
            max_tokens_per_persona=max_tokens_per_persona,
            persona_lookback_days=persona_lookback_days,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(
        self,
        shared_context: str,
        *,
        date_str: str,
        topic: str = "daily discussion briefing",
    ) -> str:
        """Generate a full discussion briefing markdown document.

        Each persona contributes one statement in sequence; each subsequent persona
        sees the accumulated transcript.  A synthesis pass appends a "Key Themes"
        section.  All statements are persisted to the history store.

        Args:
            shared_context: Workspace context from ``JournalContext.get_briefing_context()``.
            date_str: ISO date string, e.g. ``"2026-05-26"``.
            topic: Short label stored with each history entry.

        Returns:
            Formatted markdown discussion document.
        """
        if not self._personas:
            logger.warning("No personas loaded — discussion briefing will be empty.")
            return f"# Topics Discussion Briefing — {date_str}\n\n*No personas configured.*\n"

        running_transcript = ""
        section_blocks: list[str] = []

        for i, persona in enumerate(self._personas):
            is_first = i == 0
            statement = self._call_persona(
                persona=persona,
                shared_context=shared_context,
                running_transcript=running_transcript,
                date_str=date_str,
                is_first=is_first,
            )

            section_block = self._format_persona_section(persona, statement)
            section_blocks.append(section_block)

            # Extend running transcript for the next persona
            running_transcript += f"\n\n### {persona.display_name}\n\n{statement}"

            # Persist to history
            self._history.append(
                persona.id,
                date_str,
                topic,
                statement,
                source="discussion",
            )

        # Synthesis pass
        key_themes = self._synthesise(running_transcript, date_str)

        # Assemble final document
        lines: list[str] = [f"# Topics Discussion Briefing — {date_str}", ""]
        lines += section_blocks
        lines += ["", key_themes]

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _call_persona(
        self,
        *,
        persona: PersonaDef,
        shared_context: str,
        running_transcript: str,
        date_str: str,
        is_first: bool,
    ) -> str:
        """Make one LLM call for ``persona`` and return its statement."""
        past_block = self._history.format_context_block(
            persona.id,
            persona.display_name,
            n_days=self._lookback,
            max_statements=self._max_history,
        )

        system_prompt = format_discussion_persona_prompt(
            date_str=date_str,
            persona_name=persona.display_name,
            role_summary=persona.role_summary,
            communication_style=persona.communication_style,
            areas_of_focus=persona.areas_of_focus,
            system_prompt_suffix=persona.system_prompt_suffix,
            past_context_block=past_block,
        )

        user_message = format_discussion_user_message(
            shared_context=shared_context,
            running_transcript=running_transcript,
            is_first=is_first,
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        try:
            statement = self._engine._backend.chat(messages, self._max_tokens)
        except Exception as exc:
            logger.warning(
                "Discussion briefing: LLM call failed for persona %s: %s",
                persona.id,
                exc,
            )
            statement = f"*(Generation failed for {persona.display_name}: {exc})*"

        return statement.strip()

    def _format_persona_section(self, persona: PersonaDef, statement: str) -> str:
        """Format one persona's contribution as a markdown section."""
        focus_str = ", ".join(persona.areas_of_focus[:3])
        header = f"### {persona.display_name}"
        meta = f"> *Focus: {focus_str}*" if focus_str else ""
        parts = [header, ""]
        if meta:
            parts += [meta, ""]
        parts += [statement, ""]
        return "\n".join(parts)

    def _synthesise(self, full_transcript: str, date_str: str) -> str:
        """Generate a Key Themes synthesis section from the full transcript."""
        prompt = DISCUSSION_SYNTHESIS_PROMPT.replace(
            "{num_personas}", str(len(self._personas))
        )
        messages = [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": (
                    f"Discussion transcript for {date_str}:\n\n{full_transcript.strip()}"
                ),
            },
        ]
        try:
            synthesis = self._engine._backend.chat(messages, 512)
        except Exception as exc:
            logger.warning("Discussion briefing: synthesis call failed: %s", exc)
            synthesis = "*(Synthesis generation failed)*"
        return synthesis.strip()
