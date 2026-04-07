"""Pydantic data models for report template skeletons."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field


class SectionPattern(BaseModel):
    """A heading/section observed across the source document corpus."""

    heading: str
    level: int
    frequency: float = Field(
        description="Fraction of source docs containing this section (0.0–1.0)"
    )
    typical_content_type: str = Field(
        default="prose",
        description="Dominant content type: prose, table, list, or boilerplate",
    )
    boilerplate_text: str | None = Field(
        default=None,
        description="Verbatim text that appears identically across multiple source docs",
    )


class StyleSpec(BaseModel):
    """Font / paragraph properties extracted from a DOCX style definition."""

    name: str
    font_name: str | None = None
    font_size_pt: float | None = None
    bold: bool = False
    italic: bool = False
    color_rgb: str | None = Field(
        default=None, description="Hex RGB, e.g. '2E3A4F'"
    )
    space_before_pt: float | None = None
    space_after_pt: float | None = None
    line_spacing: float | None = None


class HeaderFooterSpec(BaseModel):
    """Header or footer content extracted from a reference document."""

    text: str | None = None
    image_paths: list[str] = Field(default_factory=list)
    has_page_numbers: bool = False


class TablePattern(BaseModel):
    """A recurring table structure observed across source documents."""

    label: str = Field(description="Descriptive label, e.g. 'Test Results Summary'")
    column_headers: list[str] = Field(default_factory=list)
    frequency: float = Field(
        default=0.0, description="Fraction of source docs containing this table"
    )


class ReportSkeleton(BaseModel):
    """Aggregate template extracted from a corpus of .docx reports."""

    name: str
    source_doc_count: int
    sections: list[SectionPattern] = Field(default_factory=list)
    styles: dict[str, StyleSpec] = Field(
        default_factory=dict, description="style_name -> StyleSpec"
    )
    header: HeaderFooterSpec = Field(default_factory=HeaderFooterSpec)
    footer: HeaderFooterSpec = Field(default_factory=HeaderFooterSpec)
    page_margins_inches: dict[str, float] = Field(
        default_factory=dict,
        description="Keys: top, bottom, left, right",
    )
    table_patterns: list[TablePattern] = Field(default_factory=list)
    reference_docx_path: str = Field(
        default="",
        description="Path to the stripped reference .docx preserving styles/headers",
    )

    def save(self, path: Path) -> None:
        """Serialize the skeleton to a JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> ReportSkeleton:
        """Deserialize a skeleton from a JSON file."""
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.model_validate(data)

    def format_for_agent(self) -> str:
        """Render the skeleton as a markdown-ish block for injection into agent context."""
        lines: list[str] = [
            f"## Report Template: {self.name}",
            f"(derived from {self.source_doc_count} source documents)",
            "",
            "### Required Sections",
        ]

        required = [s for s in self.sections if s.frequency >= 0.5]
        optional = [s for s in self.sections if s.frequency < 0.5]

        for sec in required:
            prefix = "#" * sec.level
            pct = int(sec.frequency * 100)
            line = (
                f"- `{prefix} {sec.heading}` "
                f"(present in {pct}% of reports, content: {sec.typical_content_type})"
            )
            lines.append(line)
            if sec.boilerplate_text:
                lines.append(f"  > Boilerplate: {sec.boilerplate_text[:200]}")

        if optional:
            lines.extend(["", "### Optional Sections"])
            for sec in optional:
                prefix = "#" * sec.level
                pct = int(sec.frequency * 100)
                lines.append(
                    f"- `{prefix} {sec.heading}` ({pct}%, {sec.typical_content_type})"
                )

        if self.table_patterns:
            lines.extend(["", "### Common Table Structures"])
            for tp in self.table_patterns:
                cols = " | ".join(tp.column_headers) if tp.column_headers else "(no headers)"
                lines.append(f"- **{tp.label}**: {cols}")

        return "\n".join(lines)
