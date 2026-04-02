"""File ingest action — converts PDF/DOCX to markdown and stages for agents."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from engineering_hub.core.models import IngestResult

logger = logging.getLogger(__name__)

SOURCE_PATH_PATTERN = re.compile(
    r"(?:from|in)\s+([^\s\]]+)|"
    r"source[_\-]?docs[/\\]?|"
    r"([~/][^\s\]]*(?:/source_docs)?)",
    re.IGNORECASE,
)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def _docling_available() -> bool:
    try:
        import docling.document_converter  # noqa: F401

        return True
    except ImportError:
        return False


class FileIngestAction:
    """Converts PDF/DOCX files to markdown and writes to staging directory."""

    def __init__(
        self,
        output_dir: Path,
        manifest_name: str = "manifest.json",
    ) -> None:
        self.output_dir = Path(output_dir)
        self.manifest_name = manifest_name
        self._docling_converter: Any | None = None

    def _get_docling_converter(self) -> Any:
        """Lazily initialise the Docling DocumentConverter (expensive import)."""
        if self._docling_converter is None:
            from docling.document_converter import DocumentConverter

            self._docling_converter = DocumentConverter()
        return self._docling_converter

    def execute_from_description(
        self,
        description: str,
        project_id: int | None,
    ) -> IngestResult:
        """Execute ingest from a task description."""
        source_path = self._extract_source_path(description)
        if not source_path:
            return IngestResult(
                success=False,
                error_message="Could not extract source path from description",
            )

        resolved = Path(source_path).expanduser().resolve()
        if not resolved.exists():
            return IngestResult(
                success=False,
                error_message=f"Source path does not exist: {resolved}",
            )

        return self.execute(source_paths=[str(resolved)], project_id=project_id or 0)

    def execute(
        self,
        source_paths: list[str],
        project_id: int,
    ) -> IngestResult:
        """Ingest files from source paths into staging directory."""
        staging_dir = self.output_dir / "staging" / f"project-{project_id}"
        staging_dir.mkdir(parents=True, exist_ok=True)

        results: list[dict] = []
        converted_docs: dict[str, tuple[str, Any | None]] = {}
        files_to_convert: list[Path] = []

        for path_str in source_paths:
            path = Path(path_str).expanduser().resolve()
            if path.is_file():
                files_to_convert.append(path)
            elif path.is_dir():
                for ext in ("*.pdf", "*.docx", "*.md", "*.txt"):
                    files_to_convert.extend(path.glob(ext))

        for src_path in files_to_convert:
            try:
                content, docling_doc = self._convert_to_markdown_with_doc(src_path)
                if content is None:
                    continue
                stem = src_path.stem
                staged_path = staging_dir / f"{stem}.md"
                staged_path.write_text(content, encoding="utf-8")

                sections = self._extract_sections(content)

                results.append({
                    "original_name": src_path.name,
                    "staged_path": str(staged_path.relative_to(self.output_dir)),
                    "sections": sections,
                })
                converted_docs[src_path.name] = (content, docling_doc)
                logger.info(f"Ingested {src_path.name} -> {staged_path.name}")
            except Exception as e:
                logger.warning(f"Failed to convert {src_path}: {e}")
                results.append({
                    "original_name": src_path.name,
                    "error": str(e),
                })

        manifest_path = staging_dir / self.manifest_name
        manifest_path.write_text(
            json.dumps({"files": results, "project_id": project_id}, indent=2),
            encoding="utf-8",
        )

        return IngestResult(
            success=len([r for r in results if "error" not in r]) > 0,
            files_converted=len([r for r in results if "error" not in r]),
            manifest_path=str(manifest_path),
            converted_docs=converted_docs,
        )

    def _extract_source_path(self, description: str) -> str | None:
        """Extract source path from task description."""
        match = re.search(r"\bfrom\s+([^\s\]]+)", description, re.IGNORECASE)
        if match:
            return match.group(1).strip().rstrip("/")

        match = re.search(r"\bin\s+([^\s\]]+)", description, re.IGNORECASE)
        if match:
            return match.group(1).strip().rstrip("/")

        match = re.search(r"([~/][^\s\]]+)", description)
        if match:
            return match.group(1).strip().rstrip("/")

        return None

    # ------------------------------------------------------------------
    # Conversion
    # ------------------------------------------------------------------

    def _convert_to_markdown(self, path: Path) -> str | None:
        """Convert file to markdown. Returns None if format not supported."""
        content, _ = self._convert_to_markdown_with_doc(path)
        return content

    def _convert_to_markdown_with_doc(
        self, path: Path
    ) -> tuple[str | None, Any | None]:
        """Convert file to markdown, returning (text, DoclingDocument | None).

        The DoclingDocument is only available when Docling handles the conversion
        and is needed downstream by the HybridChunker.
        """
        suffix = path.suffix.lower()

        if suffix in (".pdf", ".docx") and _docling_available():
            try:
                return self._convert_with_docling(path)
            except Exception as exc:
                logger.warning(f"Docling conversion failed for {path.name}, using fallback: {exc}")

        if suffix == ".pdf":
            return self._pdf_to_markdown_fallback(path), None
        if suffix == ".docx":
            return self._docx_to_markdown_fallback(path), None
        if suffix in (".md", ".txt"):
            return path.read_text(encoding="utf-8", errors="replace"), None
        return None, None

    def _convert_with_docling(self, path: Path) -> tuple[str, Any]:
        """Convert a PDF or DOCX via Docling. Returns (markdown, DoclingDocument)."""
        converter = self._get_docling_converter()
        result = converter.convert(source=str(path))
        doc = result.document
        markdown = doc.export_to_markdown()
        logger.debug(f"Docling converted {path.name} ({len(markdown)} chars)")
        return markdown, doc

    # ------------------------------------------------------------------
    # Fallback converters (pypdf / python-docx)
    # ------------------------------------------------------------------

    @staticmethod
    def _pdf_to_markdown_fallback(path: Path) -> str:
        """Extract text from PDF using pypdf (no table/OCR support)."""
        from pypdf import PdfReader

        reader = PdfReader(path)
        parts = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text()
            if text:
                parts.append(f"## Page {i + 1}\n\n{text}")
        return "\n\n".join(parts) if parts else "(No text extracted)"

    @staticmethod
    def _docx_to_markdown_fallback(path: Path) -> str:
        """Convert DOCX to markdown using python-docx (paragraphs only)."""
        from docx import Document

        doc = Document(path)
        parts = []
        for para in doc.paragraphs:
            if para.style.name.startswith("Heading"):
                level = int(para.style.name[-1]) if para.style.name[-1].isdigit() else 1
                parts.append(f"{'#' * level} {para.text}")
            elif para.text.strip():
                parts.append(para.text)
        return "\n\n".join(parts) if parts else "(Empty document)"

    # ------------------------------------------------------------------
    # Section extraction for manifest
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_sections(markdown: str) -> list[dict[str, str]]:
        """Pull heading-delimited sections from converted markdown for the manifest."""
        sections: list[dict[str, str]] = []
        current_title = "Content"
        current_lines: list[str] = []

        for line in markdown.splitlines(keepends=True):
            m = _HEADING_RE.match(line)
            if m:
                if current_lines:
                    body = "".join(current_lines).strip()
                    preview = body[:500] + "..." if len(body) > 500 else body
                    sections.append({"title": current_title, "content": preview})
                    current_lines = []
                current_title = m.group(2).strip()
            else:
                current_lines.append(line)

        if current_lines:
            body = "".join(current_lines).strip()
            if body:
                preview = body[:500] + "..." if len(body) > 500 else body
                sections.append({"title": current_title, "content": preview})

        if not sections:
            preview = markdown[:500] + "..." if len(markdown) > 500 else markdown
            sections.append({"title": "Content", "content": preview})

        return sections
