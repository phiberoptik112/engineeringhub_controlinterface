"""Tests for morning briefing markdown formatting."""

from __future__ import annotations

import pytest

from engineering_hub.journaler.prompts import format_briefing_markdown


def test_format_briefing_markdown_normalizes_numbered_sections() -> None:
    raw = """\
1. **Cross-Journal Trends** — recurring themes
- **Topic A** — detail one
- **Topic B** — detail two
2. **Yesterday in Context**
- **Change** — detail
"""
    out = format_briefing_markdown(raw)
    assert "## Cross-Journal Trends" in out
    assert "## Yesterday in Context" in out
    assert "1. **Cross-Journal Trends**" not in out


def test_format_briefing_markdown_adds_topic_spacing() -> None:
    raw = """\
## Cross-Journal Trends
- **Topic A** — first
- **Topic B** — second
"""
    out = format_briefing_markdown(raw)
    assert "- **Topic A** — first\n\n- **Topic B** — second" in out


def test_format_briefing_markdown_adds_section_spacing() -> None:
    raw = """\
## Cross-Journal Trends
- **Topic A** — first

## Yesterday in Context
- **Change** — second
"""
    out = format_briefing_markdown(raw)
    assert "first\n\n\n## Yesterday in Context" in out


def test_format_briefing_markdown_strips_fences() -> None:
    raw = """\
```markdown
## Quick Stats
- **Pending** — 3 tasks
```
"""
    out = format_briefing_markdown(raw)
    assert out.startswith("## Quick Stats")
    assert "```" not in out


@pytest.mark.parametrize(
    "raw, expected_substr",
    [
        (
            "**Needs Attention**\n- **Stale task** — overdue",
            "## Needs Attention",
        ),
        (
            "## Today's Agenda\n### Client report\n- **Draft** — finish intro",
            "### Client report\n\n- **Draft** — finish intro",
        ),
    ],
)
def test_format_briefing_markdown_variants(raw: str, expected_substr: str) -> None:
    out = format_briefing_markdown(raw)
    assert expected_substr in out
