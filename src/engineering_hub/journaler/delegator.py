"""AgentDelegator: bridge between the Journaler daemon and the agent worker system.

Allows the Journaler to delegate tasks directly to named agent personalities
(research, technical-writer, standards-checker, technical-reviewer, weekly-reviewer)
using either the local MLX model already loaded in memory or the Claude API.

Backend selection
-----------------
- "mlx"    — Reuses the Journaler's already-resident ConversationalMLXBackend via a
             thin LLMBackend adapter. No second model load; no extra RAM.
- "claude" — Uses AnthropicBackend. Requires an anthropic_api_key be configured.
- "auto"   — Prefers Claude when an anthropic_worker is available, falls back to MLX.

Skills
------
Skill definitions live in YAML files under the skills/ directory (top-level, alongside
prompts/). Each YAML file describes one agent type: when to use it, example invocations,
and output conventions. New skills are added by dropping a new .yaml file — no code changes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import SecretStr

from engineering_hub.agents.worker import AgentWorker
from engineering_hub.core.constants import AgentType
from engineering_hub.core.models import ParsedTask, TaskStatus
from engineering_hub.journaler.org_writer import add_todo_to_journal

if TYPE_CHECKING:
    from engineering_hub.journaler.engine import ConversationalMLXBackend

logger = logging.getLogger(__name__)


def _anthropic_key_str(anthropic_api_key: SecretStr | str) -> str:
    if isinstance(anthropic_api_key, SecretStr):
        return (anthropic_api_key.get_secret_value() or "").strip()
    return (anthropic_api_key or "").strip()


def build_delegator(
    mlx_backend: ConversationalMLXBackend,
    *,
    anthropic_api_key: SecretStr | str = "",
    skills_dir: Path | None = None,
    default_backend: str = "mlx",
    output_dir: Path | None = None,
    prompts_dir: Path | None = None,
) -> AgentDelegator | None:
    """Construct an :class:`AgentDelegator` or return ``None`` if setup fails.

    Shared by the Journaler daemon and interactive ``journaler chat`` so both
    inject the same skills/persona metadata into the system prompt.
    """
    if output_dir is None:
        output_dir = Path.cwd() / "outputs"
    try:
        from engineering_hub.agents.worker import AgentWorker

        anthropic_worker = None
        key = _anthropic_key_str(anthropic_api_key)
        if key:
            anthropic_worker = AgentWorker.from_anthropic(
                api_key=key,
                prompts_dir=prompts_dir,
                output_dir=output_dir,
            )
            logger.info("Claude API worker initialized for agent delegation")

        delegator = AgentDelegator(
            mlx_backend=mlx_backend,
            anthropic_worker=anthropic_worker,
            skills_dir=skills_dir,
            default_backend=default_backend,
            prompts_dir=prompts_dir,
            output_dir=output_dir,
        )
        logger.info(
            "AgentDelegator ready (default backend: %s, skills: %s)",
            default_backend,
            len(delegator.list_skills()),
        )
        return delegator
    except Exception as exc:
        logger.warning("AgentDelegator init failed — delegation unavailable: %s", exc)
        return None


# Canonical agent type names accepted in /agent commands
_AGENT_ALIASES: dict[str, str] = {
    "research": "research",
    "researcher": "research",
    "technical-writer": "technical-writer",
    "tech-writer": "technical-writer",
    "writer": "technical-writer",
    "standards-checker": "standards-checker",
    "standards": "standards-checker",
    "checker": "standards-checker",
    "technical-reviewer": "technical-reviewer",
    "tech-reviewer": "technical-reviewer",
    "reviewer": "technical-reviewer",
    "weekly-reviewer": "weekly-reviewer",
    "weekly": "weekly-reviewer",
    "latex-writer": "latex-writer",
    "latex": "latex-writer",
    "tex-writer": "latex-writer",
    "tex": "latex-writer",
}


# ---------------------------------------------------------------------------
# LLMBackend adapter
# ---------------------------------------------------------------------------


class JournalerMLXBackendAdapter:
    """Adapts ConversationalMLXBackend to the LLMBackend protocol.

    The Journaler keeps a 32B model loaded in memory for conversational use.
    This adapter lets the same resident model serve single-turn agent tasks
    without loading a second copy.
    """

    def __init__(self, journaler_backend: ConversationalMLXBackend) -> None:
        self._backend = journaler_backend

    def set_backend(self, journaler_backend: ConversationalMLXBackend) -> None:
        """Point at a new resident MLX backend (e.g. after Journaler ``/model``)."""
        self._backend = journaler_backend

    def complete(self, system: str, user_message: str, max_tokens: int) -> str:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ]
        return self._backend.chat(messages, max_tokens)

    def test_connection(self) -> bool:
        return self._backend.is_loaded()


# ---------------------------------------------------------------------------
# Skill definition
# ---------------------------------------------------------------------------


@dataclass
class SkillDef:
    """A loaded skill definition from a YAML file."""

    name: str
    display_name: str
    agent_type: str
    description: str
    when_to_use: list[str] = field(default_factory=list)
    invocation_examples: list[str] = field(default_factory=list)


def _load_skills(skills_dir: Path) -> dict[str, SkillDef]:
    """Load all *.yaml files in skills_dir into a name-keyed dict."""
    skills: dict[str, SkillDef] = {}
    if not skills_dir.exists():
        logger.warning(f"Skills directory not found: {skills_dir}")
        return skills

    for path in sorted(skills_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            skill = SkillDef(
                name=data.get("name", path.stem),
                display_name=data.get("display_name", path.stem),
                agent_type=data.get("agent_type", path.stem),
                description=data.get("description", "").strip(),
                when_to_use=data.get("when_to_use", []),
                invocation_examples=data.get("invocation_examples", []),
            )
            skills[skill.name] = skill
            logger.debug(f"Loaded skill: {skill.name} ({skill.display_name})")
        except Exception as exc:
            logger.warning(f"Failed to load skill from {path.name}: {exc}")

    logger.info(f"Loaded {len(skills)} skill(s) from {skills_dir}")
    return skills


# ---------------------------------------------------------------------------
# AgentDelegator
# ---------------------------------------------------------------------------


class AgentDelegator:
    """Bridge between the Journaler daemon and the AgentWorker system.

    Holds two optional workers:
    - mlx_worker     — backed by the Journaler's already-loaded MLX model (always available
                       when the Journaler is running, zero extra RAM)
    - anthropic_worker — backed by the Claude API (requires API key configuration)

    The delegate() method selects the appropriate worker based on the ``backend``
    argument and the ``default_backend`` configured at init.
    """

    def __init__(
        self,
        mlx_backend: ConversationalMLXBackend,
        anthropic_worker: AgentWorker | None = None,
        skills_dir: Path | None = None,
        default_backend: str = "mlx",
        prompts_dir: Path | None = None,
        output_dir: Path | None = None,
    ) -> None:
        self._default_backend = default_backend.lower()
        self._anthropic_worker = anthropic_worker

        self._mlx_adapter = JournalerMLXBackendAdapter(mlx_backend)
        self._mlx_worker = AgentWorker(
            backend=self._mlx_adapter,
            prompts_dir=prompts_dir,
            output_dir=output_dir,
        )

        resolved_skills = skills_dir or _default_skills_dir()
        self._skills = _load_skills(resolved_skills)

    def set_mlx_backend(self, backend: ConversationalMLXBackend) -> None:
        """Sync the MLX delegator adapter with a newly loaded Journaler backend."""
        self._mlx_adapter.set_backend(backend)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def delegate(
        self,
        agent_type: str,
        description: str,
        project_id: int | None = None,
        backend: str = "auto",
    ) -> str:
        """Execute a task via the selected backend and return the result as a string.

        Args:
            agent_type: Agent name (e.g. "research", "technical-writer"). Aliases accepted.
            description: Task description text.
            project_id: Optional Django project ID for context enrichment.
            backend: "auto", "mlx", or "claude". Overrides default_backend for this call.

        Returns:
            Formatted result string for display in the Journaler chat.
        """
        resolved_type = _AGENT_ALIASES.get(agent_type.lower())
        if resolved_type is None:
            available = ", ".join(sorted(_AGENT_ALIASES.keys()))
            return (
                f"Unknown agent type '{agent_type}'. "
                f"Available: {available}"
            )

        try:
            agent_enum = AgentType(resolved_type)
        except ValueError:
            return f"Agent type '{resolved_type}' is not registered in the system."

        worker = self._select_worker(backend)
        if worker is None:
            return (
                "No agent backend is available. "
                "Configure 'anthropic.api_key' for Claude or ensure the MLX model is loaded."
            )

        task = ParsedTask(
            agent=resolved_type,
            status=TaskStatus.PENDING,
            project_id=project_id,
            description=description,
            start_line=0,
            end_line=0,
            raw_block=f"@{resolved_type}: {description}",
        )

        backend_label = self._backend_label(backend, worker)
        logger.info(
            f"Delegating to {resolved_type} agent via {backend_label}: "
            f"{description[:60]}..."
        )

        try:
            result = worker.execute(task, context="")
        except Exception as exc:
            logger.error(f"Agent delegation failed: {exc}")
            return f"Agent execution failed: {exc}"

        if result.success:
            header = (
                f"**{self._skill_display_name(resolved_type)} — "
                f"completed via {backend_label}**"
            )
            if result.output_path:
                header += f"\nOutput saved to: `{result.output_path}`"
            body = result.agent_response or "(No response text returned)"
            return f"{header}\n\n{body}"
        else:
            return (
                f"Agent task failed ({backend_label}): "
                f"{result.error_message or 'unknown error'}"
            )

    def write_to_journal(
        self,
        agent_type: str,
        description: str,
        journal_dir: Path,
        project_id: int | None = None,
    ) -> str:
        """Write an agent task to today's org journal as a fallback.

        Used when no live backend is configured or when the user explicitly
        wants the task queued for overnight Orchestrator dispatch.

        Args:
            agent_type: Agent name (e.g. "research").
            description: Task description.
            journal_dir: Directory containing YYYY-MM-DD.org daily files.
            project_id: Optional Django project ID to append as a wikilink.

        Returns:
            Status message string.
        """
        resolved_type = _AGENT_ALIASES.get(agent_type.lower(), agent_type.lower())
        item = f"@{resolved_type}: {description.strip()}"
        if project_id is not None:
            item += f" [[django://project/{project_id}]]"

        ok, msg = add_todo_to_journal(journal_dir, item)
        if ok:
            return (
                f"Task queued in today's journal for overnight dispatch:\n"
                f"`- [ ] {item}`\n\n"
                f"The Orchestrator will pick it up on the next scan."
            )
        return f"Failed to write task to journal: {msg}"

    def resolve_agent_type(self, name: str) -> str | None:
        """Return the canonical agent type string for a given name/alias, or None."""
        return _AGENT_ALIASES.get(name.lower())

    def is_known_agent(self, name: str) -> bool:
        """Return True if the name resolves to a known agent type."""
        return name.lower() in _AGENT_ALIASES

    def list_skills(self) -> list[SkillDef]:
        """Return all loaded skill definitions."""
        return list(self._skills.values())

    def skills_summary(self) -> str:
        """Return a formatted string of available skills for system prompt injection."""
        if not self._skills:
            return "No delegation skills loaded."

        lines = ["## Agent Delegation Skills\n"]
        for skill in self._skills.values():
            lines.append(f"**{skill.display_name}** (`{skill.name}`)")
            lines.append(f"  {skill.description.splitlines()[0]}")
            if skill.when_to_use:
                lines.append(f"  Use when: {skill.when_to_use[0]}")
            lines.append("")

        backend_info = self._available_backends_summary()
        lines.append(f"Available backends: {backend_info}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _select_worker(self, backend: str) -> AgentWorker | None:
        """Select the appropriate AgentWorker for the given backend string."""
        effective = backend.lower() if backend.lower() != "auto" else self._default_backend

        if effective == "claude":
            if self._anthropic_worker:
                return self._anthropic_worker
            logger.warning("Claude backend requested but no anthropic_worker configured.")
            return None

        if effective == "mlx":
            return self._mlx_worker

        # auto: prefer Claude, fall back to MLX
        if self._anthropic_worker:
            return self._anthropic_worker
        return self._mlx_worker

    def _backend_label(self, requested: str, worker: AgentWorker) -> str:
        """Human-readable label for the backend actually used."""
        if worker is self._anthropic_worker:
            return "Claude API"
        return "local MLX"

    def _skill_display_name(self, agent_type: str) -> str:
        skill = self._skills.get(agent_type)
        return skill.display_name if skill else agent_type.replace("-", " ").title()

    def _available_backends_summary(self) -> str:
        parts = ["local MLX (always available)"]
        if self._anthropic_worker:
            parts.append("Claude API (configured)")
        return ", ".join(parts)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_skills_dir() -> Path:
    """Return the default skills/ directory (repo root alongside prompts/)."""
    # Walk up from this file to find the repo root (contains pyproject.toml or skills/)
    here = Path(__file__).resolve()
    for parent in [here.parent, here.parent.parent, here.parent.parent.parent,
                   here.parent.parent.parent.parent]:
        candidate = parent / "skills"
        if candidate.is_dir():
            return candidate
    # Fallback: relative to cwd
    return Path("skills")
