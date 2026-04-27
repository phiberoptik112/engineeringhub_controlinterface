"""Shared natural-language routing for Journaler chat frontends."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engineering_hub.journaler.delegator import AgentDelegator
from engineering_hub.journaler.engine import ConversationEngine
from engineering_hub.journaler.task_intent_extractor import classify_journaler_intent
from engineering_hub.journaler.task_planner_models import ProposedTask


@dataclass(frozen=True)
class RoutedChatResult:
    """A natural-language route result that bypasses the normal chat model turn."""

    response: str
    agent_result: str | None = None
    dispatched: bool = False


def build_agent_catalog(delegator: AgentDelegator | None) -> str:
    """Return a compact agent catalog for the classifier prompt."""
    if delegator is None:
        return ""

    lines: list[str] = []
    for skill in delegator.list_skills():
        aliases = delegator.aliases_for_agent(skill.agent_type)
        alias_text = f" aliases: {', '.join(aliases[:6])}" if aliases else ""
        when = "; ".join(skill.when_to_use[:2])
        if when:
            lines.append(
                f"- {skill.agent_type}: {skill.description.strip()} "
                f"Use when: {when}.{alias_text}"
            )
        else:
            lines.append(
                f"- {skill.agent_type}: {skill.description.strip()}{alias_text}"
            )
    return "\n".join(lines)


def route_natural_language_task(
    message: str,
    *,
    engine: ConversationEngine,
    delegator: AgentDelegator | None,
    mode: str,
    pending_tasks_file: Path,
    run_agent_command: Callable[[str], str],
) -> RoutedChatResult | None:
    """Route a non-slash message to an agent or queue proposal when appropriate.

    Returns ``None`` when the message should continue through the conversational
    Journaler model path.
    """
    if mode.lower() == "propose" or delegator is None:
        return None

    kind, payload = classify_journaler_intent(
        engine,
        message,
        agent_catalog=build_agent_catalog(delegator),
    )
    desc = (payload.get("description") or "").strip()
    if not desc:
        return None

    if kind == "immediate_task":
        agent = _resolve_agent(payload.get("agent_type"), desc, delegator)
        cmd = _agent_command(agent, desc, payload.get("project_id"))
        agent_result = run_agent_command(cmd)
        response = f"**@{agent}**\n\n{agent_result}"
        engine.inject_turn(message, response)
        return RoutedChatResult(
            response=response,
            agent_result=agent_result,
            dispatched=True,
        )

    if kind == "queued_task":
        agent = _resolve_agent(payload.get("agent_type"), desc, delegator)
        proposal = _proposed_task(engine, agent, desc, payload)
        engine.task_planner.add_proposal(proposal)
        response = _format_queue_response(proposal, pending_tasks_file)
        engine.inject_turn(message, response)
        return RoutedChatResult(response=response)

    return None


def _agent_command(agent: str, description: str, project_id: Any) -> str:
    cmd = f"/agent {agent} {description}"
    if project_id is not None:
        cmd += f" --project {project_id}"
    return cmd


def _resolve_agent(
    raw_agent: Any,
    description: str,
    delegator: AgentDelegator,
) -> str:
    candidate = str(raw_agent or "").strip().lower()
    if candidate and candidate not in {"auto", "best", "unknown", "null", "none"}:
        resolved = delegator.resolve_agent_type(candidate)
        if resolved:
            return resolved

    inferred = _infer_agent_from_description(description)
    resolved = delegator.resolve_agent_type(inferred)
    return resolved or "research"


def _infer_agent_from_description(description: str) -> str:
    text = description.lower()
    if any(word in text for word in ("latex", "tex", "overleaf")):
        return "latex-writer"
    if any(
        word in text
        for word in ("standard", "astm", "iso", "ibc", "ashrae", "code")
    ):
        return "standards-checker"
    if any(word in text for word in ("peer review", "review", "critique", "audit")):
        return "technical-reviewer"
    if any(
        word in text
        for word in ("draft", "write", "report", "memo", "summary", "protocol")
    ):
        return "technical-writer"
    if any(word in text for word in ("weekly", "status", "recap", "progress")):
        return "weekly-reviewer"
    return "research"


def _proposed_task(
    engine: ConversationEngine,
    agent: str,
    description: str,
    payload: dict[str, Any],
) -> ProposedTask:
    keywords = payload.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [keywords]
    input_paths = payload.get("input_paths") or []
    if not isinstance(input_paths, list):
        input_paths = []
    output_path = payload.get("output_path")

    return ProposedTask(
        agent_type=agent,
        description=description,
        session_id=engine.session_id,
        session_timestamp=engine.session_opened_at,
        proposed_at=datetime.now(timezone.utc),
        keywords=[str(x) for x in keywords][:8],
        project_id=payload.get("project_id"),
        input_paths=[str(x) for x in input_paths],
        output_path=str(output_path) if output_path else None,
        context_flags=[],
        status="proposed",
        confidence=float(payload.get("confidence", 0.0)),
        clarification_needed=bool(payload.get("clarification_needed", False)),
    )


def _format_queue_response(proposal: ProposedTask, pending_tasks_file: Path) -> str:
    response = (
        "Queued task proposal (not saved yet):\n\n"
        f"- @{proposal.agent_type}: {proposal.description}\n"
    )
    if proposal.output_path:
        response += f"- Suggested output: `{proposal.output_path}`\n"
    response += (
        "\nUse `/tasks confirm` and `/tasks commit` to save it to "
        f"`{pending_tasks_file}`."
    )
    return response
