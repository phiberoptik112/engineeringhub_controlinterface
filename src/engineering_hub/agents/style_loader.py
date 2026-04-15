"""LaTeX style and template loader for the latex-writer agent.

Resolves named style profiles (``latex-styles/*.yaml``) and raw preamble
partials (``latex-templates/*.tex``) into a rendered LaTeX preamble string
that can be injected into the agent system prompt at invocation time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_PREAMBLE_TEMPLATE = """\
\\documentclass[{class_options}]{{{document_class}}}

{package_lines}
{custom_commands}
\\title{{{title}}}
\\author{{{author}}}
\\date{{{date}}}"""


@dataclass
class LatexStyle:
    """A resolved LaTeX style ready for prompt injection."""

    name: str
    display_name: str
    description: str
    preamble_tex: str
    section_structure: str = ""


@dataclass
class _StyleSpec:
    """Internal representation of a ``latex-styles/*.yaml`` file."""

    name: str
    display_name: str
    description: str
    document_class: str = "report"
    class_options: str = "12pt,letterpaper"
    template_file: str | None = None
    packages: list[dict] = field(default_factory=list)
    custom_commands: list[str] = field(default_factory=list)
    title_block: dict = field(default_factory=dict)
    section_structure: str = ""


class StyleLoader:
    """Loads and resolves LaTeX style profiles and preamble templates.

    Args:
        styles_dir: Directory containing ``*.yaml`` style profile files.
        templates_dir: Directory containing ``*.tex`` preamble partial files.
    """

    def __init__(self, styles_dir: Path, templates_dir: Path) -> None:
        self._styles_dir = styles_dir
        self._templates_dir = templates_dir

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_styles(self) -> list[str]:
        """Return the names of all available style profiles."""
        names: list[str] = []
        for path in sorted(self._styles_dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                names.append(data.get("name", path.stem))
            except Exception:
                names.append(path.stem)
        return names

    def list_templates(self) -> list[str]:
        """Return the stems of all available raw preamble template files."""
        return sorted(p.stem for p in self._templates_dir.glob("*.tex"))

    def load(self, name: str) -> LatexStyle:
        """Load and render a named style profile.

        Args:
            name: The ``name`` field of the YAML file (or its stem).

        Returns:
            A :class:`LatexStyle` with a fully rendered ``preamble_tex``.

        Raises:
            FileNotFoundError: If no matching style profile is found.
        """
        spec = self._find_spec(name)
        preamble = self._render_preamble(spec)
        return LatexStyle(
            name=spec.name,
            display_name=spec.display_name,
            description=spec.description,
            preamble_tex=preamble,
            section_structure=spec.section_structure,
        )

    def load_template(self, stem: str) -> LatexStyle:
        """Load a raw ``.tex`` preamble partial directly by file stem.

        Args:
            stem: Filename without extension (e.g. ``preamble-consulting``).

        Returns:
            A :class:`LatexStyle` whose ``preamble_tex`` is the raw file content.

        Raises:
            FileNotFoundError: If the template file does not exist.
        """
        tex_path = self._templates_dir / f"{stem}.tex"
        if not tex_path.exists():
            available = self.list_templates()
            raise FileNotFoundError(
                f"LaTeX template '{stem}' not found in {self._templates_dir}. "
                f"Available: {available}"
            )
        preamble = tex_path.read_text(encoding="utf-8").strip()
        return LatexStyle(
            name=stem,
            display_name=stem,
            description=f"Raw preamble from {tex_path.name}",
            preamble_tex=preamble,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_spec(self, name: str) -> _StyleSpec:
        for path in self._styles_dir.glob("*.yaml"):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except Exception as exc:
                logger.warning("Could not parse style YAML %s: %s", path, exc)
                continue
            if data.get("name", path.stem) == name:
                return self._parse_spec(data)
        available = self.list_styles()
        raise FileNotFoundError(
            f"LaTeX style '{name}' not found in {self._styles_dir}. "
            f"Available: {available}"
        )

    @staticmethod
    def _parse_spec(data: dict) -> _StyleSpec:
        return _StyleSpec(
            name=data.get("name", "unknown"),
            display_name=data.get("display_name", data.get("name", "Unknown")),
            description=data.get("description", ""),
            document_class=data.get("document_class", "report"),
            class_options=data.get("class_options", "12pt,letterpaper"),
            template_file=data.get("template_file"),
            packages=data.get("packages", []),
            custom_commands=data.get("custom_commands", []),
            title_block=data.get("title_block", {}),
            section_structure=data.get("section_structure", ""),
        )

    def _render_preamble(self, spec: _StyleSpec) -> str:
        """Render the LaTeX preamble string from a style spec.

        If the spec references a ``template_file``, that file's raw content is
        returned as-is (after stripping whitespace).  Otherwise the preamble is
        assembled from the YAML fields.
        """
        if spec.template_file:
            tex_path = self._templates_dir / spec.template_file
            if not tex_path.exists():
                logger.warning(
                    "Style '%s' references template_file '%s' which does not exist — "
                    "falling back to YAML-assembled preamble.",
                    spec.name,
                    spec.template_file,
                )
            else:
                return tex_path.read_text(encoding="utf-8").strip()

        return self._assemble_preamble(spec)

    @staticmethod
    def _assemble_preamble(spec: _StyleSpec) -> str:
        """Build a LaTeX preamble from YAML fields."""
        lines: list[str] = []
        lines.append(
            f"\\documentclass[{spec.class_options}]{{{spec.document_class}}}"
        )
        lines.append("")

        for pkg in spec.packages:
            if isinstance(pkg, str):
                lines.append(f"\\usepackage{{{pkg}}}")
            elif isinstance(pkg, dict):
                pkg_name = pkg.get("name", "")
                options = pkg.get("options", "")
                if options:
                    lines.append(f"\\usepackage[{options}]{{{pkg_name}}}")
                else:
                    lines.append(f"\\usepackage{{{pkg_name}}}")
            else:
                logger.warning("Unrecognised package entry in style '%s': %r", spec.name, pkg)

        if spec.custom_commands:
            lines.append("")
            for cmd in spec.custom_commands:
                lines.append(cmd)

        title_block = spec.title_block
        title = title_block.get("title", "TITLE")
        author = title_block.get("author", "FIRM NAME \\\\ Acoustic Engineering Consulting")
        date = title_block.get("date", "\\today")

        lines.append("")
        lines.append(f"\\title{{{title}}}")
        lines.append(f"\\author{{{author}}}")
        lines.append(f"\\date{{{date}}}")

        return "\n".join(lines)
