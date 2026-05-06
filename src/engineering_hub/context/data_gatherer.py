"""DataGatherer: collect and classify pre-processed result files for the drafting pipeline.

This module is intentionally read-only with respect to numeric data. It locates
files that external scripts and simulators have already produced, reads them
verbatim, and groups them by semantic category so the technical-writer agent
receives a labelled context bundle without any calculations being performed here.

Scanned locations (in order):
  1. outputs/staging/project-{id}/   — files ingested via FileIngestAction
  2. Any extra_dirs passed at construction time (e.g. a dedicated results folder)

Classification is heuristic: first-match on keyword sets in the file's name
and first 4 KB of content. Files that match no category land in ``unclassified``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Keyword sets for heuristic classification (checked against filename + head)
# ---------------------------------------------------------------------------

_FIELD_RESULTS_KEYWORDS: frozenset[str] = frozenset(
    {
        "leq", "l90", "l10", "l50", "laeq", "lmax", "lmin",
        "ambient", "background", "field measurement", "monitoring",
        "site survey", "spl", "sound level", "dba", "dbc",
        "octave band", "1/3 octave",
    }
)

_SIMULATION_KEYWORDS: frozenset[str] = frozenset(
    {
        "cadnaa", "cadna", "predicted", "prediction", "simulation",
        "modeled", "propagation", "receiver", "source model",
        "noise map", "attenuation", "barrier insertion loss",
        "raynoise", "soundplan", "noisemap",
    }
)

_EQUIPMENT_KEYWORDS: frozenset[str] = frozenset(
    {
        "swl", "sound power", "sound power level", "manufacturer",
        "equipment", "hvac", "rtu", "rooftop unit", "chiller",
        "cooling tower", "fan", "compressor", "condenser",
        "datasheet", "spec sheet", "basis of design", "bod",
        "mitsubishi", "carrier", "trane", "daikin",
    }
)

_REGULATORY_KEYWORDS: frozenset[str] = frozenset(
    {
        "hdoh", "faa", "ashrae", "zoning", "ordinance", "regulation",
        "noise limit", "noise criteria", "noise standard", "permissible",
        "decibel limit", "db limit", "class a", "class b", "class c",
        "day-night", "ldn", "cnel", "community noise",
    }
)

# Head read limit for classification (bytes)
_HEAD_BYTES = 4096


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class DataFile:
    """A single pre-processed result file, read verbatim."""

    path: Path
    label: str  # field_results | simulation_output | equipment_specs | regulatory | unclassified
    raw_text: str

    def as_context_section(self) -> str:
        """Render the file as a labelled markdown block for agent context."""
        heading = self.label.replace("_", " ").title()
        return f"### {heading}: `{self.path.name}`\n\n```\n{self.raw_text.strip()}\n```"


@dataclass
class DataBundle:
    """Collection of pre-processed result files, organised by category.

    All numeric values are as produced by external scripts or simulators.
    No calculations are performed here.
    """

    section_hint: str
    field_results: list[DataFile] = field(default_factory=list)
    simulation_output: list[DataFile] = field(default_factory=list)
    equipment_specs: list[DataFile] = field(default_factory=list)
    regulatory: list[DataFile] = field(default_factory=list)
    unclassified: list[DataFile] = field(default_factory=list)

    @property
    def all_files(self) -> list[DataFile]:
        return (
            self.field_results
            + self.simulation_output
            + self.equipment_specs
            + self.regulatory
            + self.unclassified
        )

    @property
    def is_empty(self) -> bool:
        return len(self.all_files) == 0

    def summary_line(self) -> str:
        counts = {
            "field_results": len(self.field_results),
            "simulation_output": len(self.simulation_output),
            "equipment_specs": len(self.equipment_specs),
            "regulatory": len(self.regulatory),
            "unclassified": len(self.unclassified),
        }
        parts = [f"{v} {k.replace('_', ' ')}" for k, v in counts.items() if v]
        return ", ".join(parts) if parts else "no files found"

    def as_context_block(self) -> str:
        """Format the bundle as a structured markdown context block for agent prompts."""
        if self.is_empty:
            return (
                "## Pre-Processed Data Bundle\n\n"
                "_No data files were found in the staging directory. "
                "Ensure external scripts have written their result files before running the pipeline._"
            )

        sections: list[str] = [
            f"## Pre-Processed Data Bundle — {self.section_hint or 'Report Section'}",
            "",
            "> All numeric values below were produced by external Python scripts or simulators.",
            "> Do not compute, modify, or derive additional numbers from this data.",
            "",
        ]

        ordered = [
            ("Field Measurement Results", self.field_results),
            ("Simulation / Model Output", self.simulation_output),
            ("Equipment Specifications", self.equipment_specs),
            ("Regulatory Criteria", self.regulatory),
            ("Additional Reference Files", self.unclassified),
        ]

        for category_name, files in ordered:
            if not files:
                continue
            sections.append(f"### {category_name}")
            sections.append("")
            for df in files:
                sections.append(df.as_context_section())
                sections.append("")

        return "\n".join(sections)


# ---------------------------------------------------------------------------
# Gatherer
# ---------------------------------------------------------------------------


class DataGatherer:
    """Locate and classify pre-processed result files for a given project.

    The gatherer never performs any arithmetic on the data it reads. Its sole
    responsibility is to find files, read them verbatim, and assign each one
    to a semantic category based on keyword matching against the filename and
    the first ``_HEAD_BYTES`` bytes of content.
    """

    #: File extensions considered readable as plain text
    TEXT_EXTENSIONS: frozenset[str] = frozenset(
        {".txt", ".csv", ".md", ".markdown", ".tsv", ".log", ".tex", ".rst"}
    )

    def __init__(
        self,
        output_dir: Path,
        extra_dirs: list[Path] | None = None,
    ) -> None:
        """
        Args:
            output_dir: Base output directory containing ``staging/`` subdirectory.
            extra_dirs: Additional directories to scan (e.g. a dedicated results folder).
        """
        self.output_dir = Path(output_dir)
        self.extra_dirs: list[Path] = [Path(d) for d in (extra_dirs or [])]

    def gather(
        self,
        project_id: int | str,
        section_hint: str = "",
    ) -> DataBundle:
        """Collect and classify pre-processed result files for *project_id*.

        Args:
            project_id: Django project ID (used to locate the staging subdirectory).
            section_hint: Human-readable label for the report section being drafted.
                          Stored in the bundle for display purposes only.

        Returns:
            A :class:`DataBundle` with files grouped by category. The bundle may be
            empty if no readable files were found; callers should check ``bundle.is_empty``.
        """
        bundle = DataBundle(section_hint=section_hint)

        scan_dirs: list[Path] = []

        staging = self.output_dir / "staging" / f"project-{project_id}"
        if staging.exists():
            scan_dirs.append(staging)
        else:
            logger.warning(
                "DataGatherer: staging directory not found for project %s: %s",
                project_id,
                staging,
            )

        for extra in self.extra_dirs:
            if extra.exists():
                scan_dirs.append(extra)
            else:
                logger.debug("DataGatherer: extra_dir does not exist, skipping: %s", extra)

        if not scan_dirs:
            logger.warning(
                "DataGatherer: no directories to scan for project %s. "
                "Run file ingest or supply extra_dirs with result files.",
                project_id,
            )
            return bundle

        seen: set[Path] = set()
        for scan_dir in scan_dirs:
            for path in sorted(scan_dir.rglob("*")):
                if not path.is_file():
                    continue
                resolved = path.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)

                if path.suffix.lower() not in self.TEXT_EXTENSIONS:
                    logger.debug("DataGatherer: skipping non-text file: %s", path.name)
                    continue

                data_file = self._load_file(path)
                if data_file is None:
                    continue

                category = self._classify(path, data_file.raw_text)
                data_file.label = category
                getattr(bundle, category).append(data_file)

        logger.info(
            "DataGatherer: gathered %s for project %s (%s)",
            bundle.summary_line(),
            project_id,
            section_hint or "no section hint",
        )
        return bundle

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_file(self, path: Path) -> DataFile | None:
        """Read file content verbatim. Returns None on read error."""
        try:
            raw_text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("DataGatherer: could not read %s: %s", path, exc)
            return None

        if not raw_text.strip():
            logger.debug("DataGatherer: skipping empty file: %s", path.name)
            return None

        return DataFile(path=path, label="unclassified", raw_text=raw_text)

    def _classify(self, path: Path, raw_text: str) -> str:
        """Assign a category label based on filename and content keywords.

        Returns one of: ``field_results``, ``simulation_output``,
        ``equipment_specs``, ``regulatory``, ``unclassified``.
        """
        search_text = (path.name + " " + raw_text[:_HEAD_BYTES]).lower()

        # Check most-specific categories first so distinctive keywords
        # (e.g. "hdoh", "swl", "cadnaa") take precedence over the broad
        # "dba" signal that appears in many contexts.
        if any(kw in search_text for kw in _REGULATORY_KEYWORDS):
            return "regulatory"
        if any(kw in search_text for kw in _SIMULATION_KEYWORDS):
            return "simulation_output"
        if any(kw in search_text for kw in _EQUIPMENT_KEYWORDS):
            return "equipment_specs"
        if any(kw in search_text for kw in _FIELD_RESULTS_KEYWORDS):
            return "field_results"
        return "unclassified"
