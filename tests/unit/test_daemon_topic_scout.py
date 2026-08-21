from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from engineering_hub.journaler.context import JournalContext
from engineering_hub.journaler.daemon import (
    TOPIC_HINTS_JOURNAL_HEADING,
    JournalerConfig,
    _coordination_scan,
    _tick,
    _topic_scout_tick,
)
from engineering_hub.journaler.models import ContextSnapshot
from engineering_hub.journaler.org_writer import read_section_body


def _minimal_config(
    tmp_path: Path,
    *,
    append_to_journal: bool = True,
) -> JournalerConfig:
    journal_dir = tmp_path / "journals"
    journal_dir.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = workspace / ".journaler"
    state.mkdir(parents=True)
    return JournalerConfig(
        model_path="test-model",
        org_roam_dir=tmp_path / "roam",
        journal_dir=journal_dir,
        workspace_dir=workspace,
        state_dir=state,
        proactive_topic_scout_enabled=True,
        proactive_topic_scout_max_tokens=256,
        briefing_append_to_journal=append_to_journal,
    )


def test_load_topic_hints_in_get_current_context(tmp_path: Path) -> None:
    config = _minimal_config(tmp_path)
    hints_dir = config.state_dir / "topic_hints"
    hints_dir.mkdir(parents=True)
    today = date.today().isoformat()
    (hints_dir / f"{today}.md").write_text(
        f"# Topic hints — {today}\n\n### Conversation starters\n- Review ASTM scope\n",
        encoding="utf-8",
    )
    ctx = JournalContext(
        org_roam_dir=config.org_roam_dir,
        journal_dir=config.journal_dir,
        workspace_dir=config.workspace_dir,
        memory_service=None,
        state_dir=config.state_dir,
    )
    text = ctx.get_current_context()
    assert "### Topic hints (auto)" in text
    assert "Review ASTM scope" in text


def test_topic_scout_writes_hint_file(tmp_path: Path) -> None:
    config = _minimal_config(tmp_path)
    context = JournalContext(
        org_roam_dir=config.org_roam_dir,
        journal_dir=config.journal_dir,
        workspace_dir=config.workspace_dir,
        memory_service=None,
        state_dir=config.state_dir,
    )
    engine = MagicMock()
    engine._raw_complete.return_value = (
        "### Conversation starters\n- Follow up on client call\n"
        "### Suggested agent commands\n`/agent research ASTM E336`"
    )
    snapshot = ContextSnapshot(
        has_significant_changes=True,
        change_summary="journal 2026-06-05.org: +1 entry (Client call)",
    )

    result = _topic_scout_tick(config, context, engine, snapshot)

    assert result is not None
    hint_path = config.state_dir / "topic_hints" / f"{date.today().isoformat()}.md"
    assert hint_path.is_file()
    assert "Conversation starters" in hint_path.read_text(encoding="utf-8")

    today_path = config.journal_dir / f"{date.today().isoformat()}.org"
    body = read_section_body(today_path, TOPIC_HINTS_JOURNAL_HEADING)
    assert "Follow up on client call" in body
    assert "Conversation starters" in body
    assert "_Generated " in body
    assert "journal 2026-06-05.org: +1 entry (Client call)" in body


def test_topic_scout_skips_journal_when_flag_disabled(tmp_path: Path) -> None:
    config = _minimal_config(tmp_path, append_to_journal=False)
    context = JournalContext(
        org_roam_dir=config.org_roam_dir,
        journal_dir=config.journal_dir,
        workspace_dir=config.workspace_dir,
        memory_service=None,
        state_dir=config.state_dir,
    )
    engine = MagicMock()
    engine._raw_complete.return_value = "### Conversation starters\n- Hint one\n"
    snapshot = ContextSnapshot(
        has_significant_changes=True,
        change_summary="journal today.org: +1 entry (Note)",
    )

    result = _topic_scout_tick(config, context, engine, snapshot)

    assert result is not None
    hint_path = config.state_dir / "topic_hints" / f"{date.today().isoformat()}.md"
    assert hint_path.is_file()
    today_path = config.journal_dir / f"{date.today().isoformat()}.org"
    assert not today_path.exists()


def test_topic_scout_appends_to_existing_journal_section(tmp_path: Path) -> None:
    config = _minimal_config(tmp_path)
    context = JournalContext(
        org_roam_dir=config.org_roam_dir,
        journal_dir=config.journal_dir,
        workspace_dir=config.workspace_dir,
        memory_service=None,
        state_dir=config.state_dir,
    )
    engine = MagicMock()
    snapshot = ContextSnapshot(
        has_significant_changes=True,
        change_summary="journal today.org: +1 entry (Note)",
    )

    engine._raw_complete.return_value = "### Conversation starters\n- First hint\n"
    _topic_scout_tick(config, context, engine, snapshot)
    engine._raw_complete.return_value = "### Conversation starters\n- Second hint\n"
    _topic_scout_tick(config, context, engine, snapshot)

    today_path = config.journal_dir / f"{date.today().isoformat()}.org"
    raw = today_path.read_text(encoding="utf-8")
    assert raw.count(f"* {TOPIC_HINTS_JOURNAL_HEADING}") == 1
    body = read_section_body(today_path, TOPIC_HINTS_JOURNAL_HEADING)
    assert "First hint" in body
    assert "Second hint" in body
    assert body.index("First hint") < body.index("Second hint")
    assert body.count("_Generated ") == 2


def test_tick_skips_scout_when_not_significant(tmp_path: Path) -> None:
    config = _minimal_config(tmp_path)
    context = MagicMock()
    context.scan.return_value = ContextSnapshot(
        has_significant_changes=False,
        change_summary="no content changes",
    )
    context.get_current_context.return_value = "## Current Context"
    engine = MagicMock()

    with patch(
        "engineering_hub.journaler.daemon._topic_scout_tick"
    ) as mock_scout:
        _tick(
            config=config,
            context=context,
            engine=engine,
            system_template="{context_snapshot}",
        )
        mock_scout.assert_not_called()


def test_tick_runs_scout_when_significant(tmp_path: Path) -> None:
    config = _minimal_config(tmp_path)
    context = MagicMock()
    context.scan.return_value = ContextSnapshot(
        has_significant_changes=True,
        change_summary="journal today.org: +1 entry (Note)",
    )
    context.get_current_context.return_value = "## Current Context"
    engine = MagicMock()

    with patch(
        "engineering_hub.journaler.daemon._topic_scout_tick"
    ) as mock_scout:
        _tick(
            config=config,
            context=context,
            engine=engine,
            system_template="{context_snapshot}",
        )
        mock_scout.assert_called_once()


def test_coordination_scan_delegates_via_mlx(tmp_path: Path) -> None:
    config = _minimal_config(tmp_path)
    context = MagicMock()
    context.get_briefing_context.return_value = "## Briefing context"
    delegator = MagicMock()
    delegator.delegate.return_value = "**Coordination Analyst — completed via local MLX**\n\nCore ask: ..."

    _coordination_scan(config=config, context=context, delegator=delegator)

    delegator.delegate.assert_called_once()
    call_kw = delegator.delegate.call_args
    assert call_kw[0][0] == "coordination-analyst"
    assert call_kw[1]["backend"] == "mlx"

    today_str = date.today().isoformat()
    output_path = config.state_dir / "outputs" / "coordination" / f"{today_str}.md"
    assert output_path.is_file()
    assert "Core ask" in output_path.read_text(encoding="utf-8")


def test_coordination_scan_skips_without_delegator(tmp_path: Path) -> None:
    config = _minimal_config(tmp_path)
    context = MagicMock()

    _coordination_scan(config=config, context=context, delegator=None)

    context.scan.assert_not_called()
