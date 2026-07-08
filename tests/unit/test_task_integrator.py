"""Unit tests for the Task-Integrator inline call-and-response loop."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from engineering_hub.journaler.org_writer import (
    _today_journal_path,
    read_section_body,
)
from engineering_hub.journaler.task_integrator import (
    _REPLY_MARKER,
    Proposal,
    TaskIntegrator,
)


def _fake_delegator() -> MagicMock:
    delegator = MagicMock()
    delegator.list_skills.return_value = [
        SimpleNamespace(
            agent_type="research",
            display_name="Research",
            description="Research and summarize technical topics.",
        ),
        SimpleNamespace(
            agent_type="technical-writer",
            display_name="Technical Writer",
            description="Draft long-form reports and protocols.",
        ),
    ]
    canonical = {"research": "research", "technical-writer": "technical-writer"}
    delegator.resolve_agent_type.side_effect = lambda name: canonical.get(name.lower())
    return delegator


def _write_journal(journal_dir: Path, body: str) -> Path:
    journal_dir.mkdir(parents=True, exist_ok=True)
    path = _today_journal_path(journal_dir)
    path.write_text(body, encoding="utf-8")
    return path


def _build_integrator(
    tmp_path: Path,
    engine: MagicMock,
    *,
    weekdays_only: bool = True,
) -> TaskIntegrator:
    journal_dir = tmp_path / "journals"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    return TaskIntegrator(
        engine=engine,
        delegator=_fake_delegator(),
        journal_dir=journal_dir,
        state_dir=state_dir,
        weekdays_only=weekdays_only,
    )


_BASE_NOTE = (
    ":PROPERTIES:\n:ID: abc\n:END:\n"
    "#+title: today\n\n"
    "* Notes\n"
    "I need to test the new N4v3 horn geometry timing against the old build.\n\n"
    "* Overnight Agent Tasks\n"
    "- [ ] @research: a pre-existing managed task that must be ignored\n\n"
)


def test_intake_excludes_managed_sections(tmp_path: Path) -> None:
    engine = MagicMock()
    integrator = _build_integrator(tmp_path, engine)
    _write_journal(tmp_path / "journals", _BASE_NOTE)

    chunks = integrator._read_intake_chunks()
    joined = "\n".join(text for text, _ in chunks)

    assert "N4v3 horn geometry" in joined
    assert "pre-existing managed task" not in joined


def test_interview_writes_question_block_and_state(tmp_path: Path) -> None:
    engine = MagicMock()
    engine._raw_complete.return_value = (
        '{"topic": "Horn timing test", '
        '"questions": ["Which horn build?", "What is the acceptance threshold?"]}'
    )
    integrator = _build_integrator(tmp_path, engine)
    path = _write_journal(tmp_path / "journals", _BASE_NOTE)

    result = integrator.run_cycle()

    assert result.questions_asked == 1
    assert result.awaiting_reply == 1
    body = read_section_body(path, "Agent Conversation")
    assert ":CONV_ID:" in body
    assert "Which horn build?" in body
    assert _REPLY_MARKER in body

    state = integrator._store.load()
    assert len(state.conversations) == 1
    assert state.conversations[0].status == "awaiting_reply"


def test_dedup_does_not_reinterview(tmp_path: Path) -> None:
    engine = MagicMock()
    engine._raw_complete.return_value = (
        '{"topic": "Horn timing test", "questions": ["Which horn build?"]}'
    )
    integrator = _build_integrator(tmp_path, engine)
    _write_journal(tmp_path / "journals", _BASE_NOTE)

    integrator.run_cycle()
    calls_after_first = engine._raw_complete.call_count

    # No reply added and no new intake -> second cycle must not call the model.
    integrator.run_cycle()
    assert engine._raw_complete.call_count == calls_after_first


def test_reply_detection_and_resolution(tmp_path: Path) -> None:
    engine = MagicMock()
    engine._raw_complete.side_effect = [
        '{"topic": "Horn timing test", "questions": ["Which horn build?"]}',
        '{"agent_type": "research", "description": "Summarize horn timing acceptance criteria"}',
    ]
    integrator = _build_integrator(tmp_path, engine)
    path = _write_journal(tmp_path / "journals", _BASE_NOTE)

    integrator.run_cycle()  # interview

    # User types a reply inline under the marker.
    raw = path.read_text(encoding="utf-8")
    raw = raw.replace(
        _REPLY_MARKER,
        _REPLY_MARKER + "\nUse N4v3; threshold within 5%.",
        1,
    )
    path.write_text(raw, encoding="utf-8")

    result = integrator.run_cycle()  # resolution

    assert result.proposals_written == 1
    body = read_section_body(path, "Agent Conversation")
    assert "Proposed tasks" in body
    assert "- [ ] @research: Summarize horn timing acceptance criteria" in body

    state = integrator._store.load()
    assert state.conversations[0].status == "proposed"
    assert state.conversations[0].proposals[0].agent_type == "research"


def test_approval_queues_to_overnight(tmp_path: Path) -> None:
    engine = MagicMock()
    engine._raw_complete.side_effect = [
        '{"topic": "Horn timing test", "questions": ["Which horn build?"]}',
        '{"agent_type": "research", "description": "Summarize horn timing acceptance criteria"}',
    ]
    integrator = _build_integrator(tmp_path, engine, weekdays_only=False)
    path = _write_journal(tmp_path / "journals", _BASE_NOTE)

    integrator.run_cycle()
    raw = path.read_text(encoding="utf-8").replace(
        _REPLY_MARKER, _REPLY_MARKER + "\nUse N4v3.", 1
    )
    path.write_text(raw, encoding="utf-8")
    integrator.run_cycle()  # resolution -> proposal block written

    # User ticks the proposal checkbox to approve.
    raw = path.read_text(encoding="utf-8").replace(
        "- [ ] @research: Summarize horn timing acceptance criteria",
        "- [x] @research: Summarize horn timing acceptance criteria",
        1,
    )
    path.write_text(raw, encoding="utf-8")

    result = integrator.run_cycle()  # approval phase

    assert result.tasks_queued == 1
    overnight = read_section_body(path, "Overnight Agent Tasks")
    assert "- [ ] @research: Summarize horn timing acceptance criteria" in overnight

    state = integrator._store.load()
    assert state.conversations[0].status == "queued"
    assert state.conversations[0].proposals[0].queued is True


def test_weekday_gating_blocks_weekend_queueing(tmp_path: Path) -> None:
    engine = MagicMock()
    integrator = _build_integrator(tmp_path, engine, weekdays_only=True)
    path = _write_journal(tmp_path / "journals", _BASE_NOTE)

    # Seed a proposed conversation + an approved proposal block directly.
    from engineering_hub.journaler.task_integrator import ConversationEntry, DayState

    entry = ConversationEntry(
        id="deadbeef",
        source_hash="h",
        topic="Horn timing test",
        status="proposed",
        questions=["Which horn build?"],
        proposals=[Proposal(agent_type="research", description="Summarize acceptance criteria")],
    )
    state = DayState(conversations=[entry])
    integrator._store.save(state)
    integrator._write_proposal_block(entry)

    # Approve inline.
    raw = path.read_text(encoding="utf-8").replace(
        "- [ ] @research: Summarize acceptance criteria",
        "- [x] @research: Summarize acceptance criteria",
        1,
    )
    path.write_text(raw, encoding="utf-8")

    class _SaturdayDate(date):
        @classmethod
        def today(cls):  # type: ignore[override]
            return cls(2026, 6, 13)  # Saturday

    with patch("engineering_hub.journaler.task_integrator.date", _SaturdayDate):
        queued = integrator._phase_approval(state)

    assert queued == 0
    overnight = read_section_body(path, "Overnight Agent Tasks")
    assert "Summarize acceptance criteria" not in overnight
