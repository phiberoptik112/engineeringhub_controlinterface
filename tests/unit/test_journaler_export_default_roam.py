"""Tests for /export default org-roam file target in chat."""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path

from rich.console import Console

from engineering_hub.cli import _execute_journaler_export
from engineering_hub.config.settings import Settings
from engineering_hub.journaler.constants import JOURNALER_CONVERSATION_EXPORT_DIRNAME


def test_chat_default_export_writes_under_conversation_exports(tmp_path: Path) -> None:
    roam = tmp_path / "org-roam"
    journals = roam / "journals"
    journals.mkdir(parents=True)
    state = tmp_path / ".journaler"
    state.mkdir()
    jsonl = state / "conversation.jsonl"
    jsonl.write_text(
        json.dumps({"timestamp": "t1", "role": "user", "content": "hello"}) + "\n",
        encoding="utf-8",
    )

    settings = Settings(workspace_dir=tmp_path, org_journal_dir=journals)
    args = argparse.Namespace(
        jsonl=None,
        summarize=False,
        export_format="raw",
        output=None,
        note=None,
        heading="Journaler export",
        find_title=None,
        new_node=None,
    )
    buf = io.StringIO()
    rc = _execute_journaler_export(
        args,
        settings=settings,
        config=object(),
        spec=object(),
        log=Console(file=buf, width=120),
        body_to_stdout=False,
        chat_default_roam_export=True,
    )
    assert rc == 0
    export_dir = roam / JOURNALER_CONVERSATION_EXPORT_DIRNAME
    assert export_dir.is_dir()
    org_files = list(export_dir.glob("*.org"))
    assert len(org_files) == 1
    text = org_files[0].read_text(encoding="utf-8")
    assert "#+title: Journaler chat export" in text
    assert "hello" in text
