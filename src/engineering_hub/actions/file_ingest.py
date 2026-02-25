"""File ingest action — converts PDF/DOCX to markdown and stages for agents."""

import json
import logging
import re
from pathlib import Path

from pypdf import PdfReader

from engineering_hub.core.models import IngestResult

logger = logging.getLogger(__name__)

# Match "from ~/path" or "from /path" or "source_docs/"
SOURCE_PATH_PATTERN = re.compile(
    r"(?:from|in)\s+([^\s\]]+)|"
    r"source[_\-]?docs[/\\]?|"
    r"([~/][^\s\]]*(?:/source_docs)?)",
    re.IGNORECASE,
)


class FileIngestAction:
    """Converts PDF/DOCX files to markdown and writes to staging directory."""

    def __init__(
        self,
        output_dir: Path,
        manifest_name: str = "manifest.json",
    ) -> None:
        """Initialize the ingest action.

        Args:
            output_dir: Base output directory (e.g. workspace/outputs)
            manifest_name: Filename for manifest.json in each project staging dir
        """
        self.output_dir = Path(output_dir)
        self.manifest_name = manifest_name

    def execute_from_description(
        self,
        description: str,
        project_id: int | None,
    ) -> IngestResult:
        """Execute ingest from a task description.

        Extracts source path from description like:
        "Ingest source docs for [[django://project/25]] from ~/projects/hoonani/source_docs/"

        Args:
            description: Task description text
            project_id: Project ID for staging directory

        Returns:
            IngestResult with success status and manifest path
        """
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
        """Ingest files from source paths into staging directory.

        Args:
            source_paths: List of paths (files or directories)
            project_id: Project ID for staging directory

        Returns:
            IngestResult with files converted and manifest path
        """
        staging_dir = self.output_dir / "staging" / f"project-{project_id}"
        staging_dir.mkdir(parents=True, exist_ok=True)

        results: list[dict] = []
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
                content = self._convert_to_markdown(src_path)
                if content is None:
                    continue
                stem = src_path.stem
                staged_path = staging_dir / f"{stem}.md"
                staged_path.write_text(content, encoding="utf-8")
                results.append({
                    "original_name": src_path.name,
                    "staged_path": str(staged_path.relative_to(self.output_dir)),
                    "sections": [{"title": "Content", "content": content[:500] + "..." if len(content) > 500 else content}],
                })
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
        )

    def _extract_source_path(self, description: str) -> str | None:
        """Extract source path from task description."""
        # Try "from ~/path" or "from /path"
        match = re.search(r"\bfrom\s+([^\s\]]+)", description, re.IGNORECASE)
        if match:
            return match.group(1).strip().rstrip("/")

        # Try "in ~/path"
        match = re.search(r"\bin\s+([^\s\]]+)", description, re.IGNORECASE)
        if match:
            return match.group(1).strip().rstrip("/")

        # Try path-like at end
        match = re.search(r"([~/][^\s\]]+)", description)
        if match:
            return match.group(1).strip().rstrip("/")

        return None

    def _convert_to_markdown(self, path: Path) -> str | None:
        """Convert file to markdown. Returns None if format not supported."""
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return self._pdf_to_markdown(path)
        if suffix == ".docx":
            return self._docx_to_markdown(path)
        if suffix in (".md", ".txt"):
            return path.read_text(encoding="utf-8", errors="replace")
        return None

    def _pdf_to_markdown(self, path: Path) -> str:
        """Extract text from PDF."""
        reader = PdfReader(path)
        parts = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text()
            if text:
                parts.append(f"## Page {i + 1}\n\n{text}")
        return "\n\n".join(parts) if parts else "(No text extracted)"

    def _docx_to_markdown(self, path: Path) -> str:
        """Convert DOCX to markdown."""
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
