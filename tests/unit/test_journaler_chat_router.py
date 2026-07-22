"""Tests for shared Journaler natural-language agent routing."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engineering_hub.journaler.chat_router import (
    build_agent_catalog,
    route_natural_language_task,
)
from engineering_hub.journaler.delegator import SkillDef


class _Engine:
    def __init__(self) -> None:
        self.session_id = "session-1"
        self.session_opened_at = datetime.now(timezone.utc)
        self.task_planner = _Planner()
        self.injected: list[tuple[str, str]] = []

    def inject_turn(self, user: str, assistant: str) -> None:
        self.injected.append((user, assistant))


class _Planner:
    def __init__(self) -> None:
        self.proposals: list[Any] = []

    def add_proposal(self, proposal: Any) -> None:
        self.proposals.append(proposal)


class _Delegator:
    def list_skills(self) -> list[SkillDef]:
        return [
            SkillDef(
                name="technical-writer",
                display_name="Technical Writer",
                agent_type="technical-writer",
                description="Draft client-ready technical documents.",
                when_to_use=["The user asks to draft reports or protocols"],
            )
        ]

    def aliases_for_agent(self, agent_type: str) -> list[str]:
        if agent_type == "technical-writer":
            return ["technical-writer", "writer"]
        return []

    def resolve_agent_type(self, name: str) -> str | None:
        return {"writer": "technical-writer", "technical-writer": "technical-writer"}.get(
            name
        )


def test_build_agent_catalog_uses_skill_metadata() -> None:
    catalog = build_agent_catalog(_Delegator())  # type: ignore[arg-type]

    assert "technical-writer" in catalog
    assert "Draft client-ready" in catalog
    assert "writer" in catalog


def test_route_immediate_task_executes_and_injects(monkeypatch: Any, tmp_path: Path) -> None:
    engine = _Engine()
    commands: list[str] = []

    def fake_classify(*args: Any, **kwargs: Any) -> tuple[str, dict[str, Any]]:
        assert "technical-writer" in kwargs["agent_catalog"]
        return (
            "immediate_task",
            {
                "agent_type": "auto",
                "description": "draft a field report summary",
                "project_id": "LVT_alert_system_consulting",
            },
        )

    monkeypatch.setattr(
        "engineering_hub.journaler.chat_router.classify_journaler_intent",
        fake_classify,
    )

    result = route_natural_language_task(
        "Can you draft the field report summary?",
        engine=engine,  # type: ignore[arg-type]
        delegator=_Delegator(),  # type: ignore[arg-type]
        mode="immediate",
        pending_tasks_file=tmp_path / "pending-tasks.org",
        run_agent_command=lambda cmd: commands.append(cmd) or "agent output",
    )

    assert result is not None
    assert result.dispatched is True
    assert commands == [
        "/agent technical-writer draft a field report summary "
        "--project LVT_alert_system_consulting"
    ]
    assert engine.injected[0][0] == "Can you draft the field report summary?"


def test_route_queue_task_adds_proposal(monkeypatch: Any, tmp_path: Path) -> None:
    engine = _Engine()

    def fake_classify(*args: Any, **kwargs: Any) -> tuple[str, dict[str, Any]]:
        return (
            "queued_task",
            {
                "agent_type": "writer",
                "description": "write the weekly recap",
                "keywords": ["weekly", "recap"],
                "output_path": "outputs/weekly.md",
            },
        )

    monkeypatch.setattr(
        "engineering_hub.journaler.chat_router.classify_journaler_intent",
        fake_classify,
    )

    result = route_natural_language_task(
        "Queue a weekly recap for tonight",
        engine=engine,  # type: ignore[arg-type]
        delegator=_Delegator(),  # type: ignore[arg-type]
        mode="immediate",
        pending_tasks_file=tmp_path / "pending-tasks.org",
        run_agent_command=lambda cmd: "unused",
    )

    assert result is not None
    assert result.dispatched is False
    assert len(engine.task_planner.proposals) == 1
    assert engine.task_planner.proposals[0].agent_type == "technical-writer"
    assert "pending-tasks.org" in result.response
