from __future__ import annotations

from pathlib import Path

from engineering_hub.journaler.org_parser import extract_tasks_with_provenance


def test_extract_tasks_with_provenance_finds_deep_checkbox(tmp_path: Path) -> None:
    org = tmp_path / "2026-06-10.org"
    padding = "y" * 800
    org.write_text(f"* Section\n{padding}\n- [ ] Below the fold task\n", encoding="utf-8")
    lines = extract_tasks_with_provenance(org)
    texts = [line.text for line in lines]
    assert "Below the fold task" in texts
    assert any(line.status == "pending" for line in lines if line.text == "Below the fold task")
