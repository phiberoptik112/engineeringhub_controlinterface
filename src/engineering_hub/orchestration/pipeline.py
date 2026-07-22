"""Multi-stage agent pipeline for automated report section drafting.

Pipeline stages are executed sequentially; each stage's output is forwarded as
additional context to the next stage. The standards-checker stage can trigger a
bounded loop back to the technical-writer when it detects non-compliance.

Calculation boundary
--------------------
The pipeline is a *writing and review* tool only. All numeric computations
(dB deltas, compliance margins, averages) must be performed by external
Python scripts before the pipeline is invoked. The technical-writer is
permitted to flag which metrics are most relevant to the section goal, but it
must not derive or modify any numeric values.

Default stage order
-------------------
  1. technical-writer  — draft prose around pre-computed tables
  2. standards-checker — audit for HDOH/FAA/ASHRAE compliance
                         (loops back to writer if NON-COMPLIANT, bounded by max_retries)
  3. technical-reviewer — review for professional tone and defensibility
  4. latex-writer      — format as LaTeX
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engineering_hub.context.data_gatherer import DataBundle
    from engineering_hub.journaler.delegator import AgentDelegator

logger = logging.getLogger(__name__)

# Detects compliance verdict in standards-checker output.
# Matches "NON-COMPLIANT", "non-compliant", "noncompliant", etc.
_NON_COMPLIANT_RE = re.compile(r"\bnon[\s-]?compliant\b", re.IGNORECASE)
_COMPLIANT_RE = re.compile(r"\bcompliant\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class PipelineStage:
    """Configuration for one stage in the pipeline."""

    agent_type: str
    description_template: str
    deliverable: str | None = None
    loop_back_on: str | None = None
    max_retries: int = 0


@dataclass
class StageResult:
    """Result from executing one pipeline stage."""

    agent_type: str
    description: str
    response: str
    loop_triggered: bool = False
    retries_used: int = 0
    failed: bool = False
    failure_reason: str | None = None


@dataclass
class PipelineResult:
    """Aggregated result from a full pipeline run."""

    section: str
    stages: list[StageResult] = field(default_factory=list)
    final_text: str = ""
    failed: bool = False
    failure_reason: str | None = None
    artifact_path: Path | None = None

    def format_summary(self) -> str:
        """Return a human-readable pipeline summary for display in the Journaler."""
        lines: list[str] = [
            f"## Pipeline Result — {self.section}",
            "",
        ]

        if self.failed:
            lines += [
                f"**Pipeline failed**: {self.failure_reason or 'unknown error'}",
                "",
            ]

        for i, s in enumerate(self.stages, 1):
            status = "FAILED" if s.failed else ("LOOP-BACK" if s.loop_triggered else "OK")
            retry_note = f" ({s.retries_used} retries)" if s.retries_used else ""
            lines.append(f"{i}. **{s.agent_type}** — {status}{retry_note}")
            if s.failed:
                lines.append(f"   - Reason: {s.failure_reason}")

        if self.artifact_path:
            lines += ["", f"Output saved to: `{self.artifact_path}`"]

        if not self.failed:
            lines += ["", "---", "", self.final_text]

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Default pipeline specification
# ---------------------------------------------------------------------------

DEFAULT_PIPELINE_SPEC: list[PipelineStage] = [
    PipelineStage(
        agent_type="technical-writer",
        description_template=(
            "Draft section '{section}' using the pre-computed result tables provided in "
            "the data bundle below. Identify which metrics (e.g. Leq day/night, predicted "
            "receptor levels) are most relevant to the project compliance goal and highlight "
            "them in the prose. Do not compute, modify, or derive any numeric values — use "
            "the numbers exactly as supplied."
        ),
        deliverable="markdown",
    ),
    PipelineStage(
        agent_type="standards-checker",
        description_template=(
            "Audit the following draft of section '{section}' for compliance with the "
            "applicable regulatory limits (HDOH, FAA, ASHRAE, or project-specific criteria "
            "referenced in the data bundle). For each claim, state whether it is COMPLIANT "
            "or NON-COMPLIANT and cite the relevant limit. If any items are NON-COMPLIANT, "
            "list specific corrections required."
        ),
        loop_back_on="NON-COMPLIANT",
        max_retries=2,
    ),
    PipelineStage(
        agent_type="technical-reviewer",
        description_template=(
            "Review the following draft of section '{section}' for professional tone, "
            "clarity, and defensibility. The section has already been audited for standards "
            "compliance — focus on narrative quality, appropriate hedging language, and "
            "whether conclusions are clearly supported by the data presented."
        ),
    ),
    PipelineStage(
        agent_type="latex-writer",
        description_template=(
            "Format the following reviewed draft of section '{section}' as LaTeX. "
            "Present any result tables as proper LaTeX tabular environments with appropriate "
            "column headers and footnotes. Wrap prose in the correct sectioning commands. "
            "Do not add or modify any numeric values."
        ),
        deliverable="latex",
    ),
]


# ---------------------------------------------------------------------------
# Pipeline executor
# ---------------------------------------------------------------------------


class AgentPipeline:
    """Execute a sequence of agent stages to produce a drafted report section.

    Usage::

        pipeline = AgentPipeline()
        bundle = DataGatherer(output_dir).gather(project_id, section_hint)
        result = pipeline.run(
            section="6.0 Potential Noise Impacts",
            data_bundle=bundle,
            delegator=delegator,
            project_id=42,
        )
        print(result.format_summary())

    The pipeline respects the ``max_retries`` setting on each stage. If the
    standards-checker output contains a ``loop_back_on`` keyword and retries remain,
    the technical-writer re-runs with the audit notes appended to its context.
    After exhausting retries the pipeline writes a ``PIPELINE_FAILED`` artifact
    containing all stage outputs so the engineer can resolve the issue manually.
    """

    def __init__(
        self,
        stages: list[PipelineStage] | None = None,
        output_dir: Path | None = None,
    ) -> None:
        """
        Args:
            stages: Ordered list of :class:`PipelineStage` objects. Defaults to
                :data:`DEFAULT_PIPELINE_SPEC`.
            output_dir: Directory for writing the final artifact. Defaults to
                ``outputs/pipeline/``.
        """
        self.stages = stages if stages is not None else list(DEFAULT_PIPELINE_SPEC)
        self.output_dir = Path(output_dir) if output_dir else Path("outputs") / "pipeline"

    def run(
        self,
        section: str,
        data_bundle: DataBundle,
        delegator: AgentDelegator,
        project_id: int | str | None = None,
        backend: str = "auto",
        loop_limit: int | None = None,
    ) -> PipelineResult:
        """Run the full pipeline and return a :class:`PipelineResult`.

        Args:
            section: Human-readable section identifier (e.g. ``"6.0 Noise Impacts"``).
            data_bundle: Pre-processed data files assembled by :class:`DataGatherer`.
            delegator: Live :class:`AgentDelegator` for executing each stage.
            project_id: Optional Django project ID passed through to each agent.
            backend: Backend selection (``"auto"``, ``"mlx"``, or ``"claude"``).
            loop_limit: Override the ``max_retries`` of all stages. ``None`` keeps
                each stage's own configured value.

        Returns:
            :class:`PipelineResult` with stage outputs and the final artifact text.
        """
        result = PipelineResult(section=section)
        data_context = data_bundle.as_context_block()
        accumulated_context = data_context

        # Index the writer stage for loop-back (use last writer before checker by default)
        writer_stage_idx = self._find_writer_idx()

        stage_idx = 0
        while stage_idx < len(self.stages):
            stage = self.stages[stage_idx]
            effective_max_retries = loop_limit if loop_limit is not None else stage.max_retries

            description = stage.description_template.replace("{section}", section)

            logger.info("Pipeline: running stage %d/%d: %s", stage_idx + 1, len(self.stages), stage.agent_type)

            # Execute stage (with loop-back on the checker stage)
            stage_result, accumulated_context = self._run_stage(
                stage=stage,
                description=description,
                accumulated_context=accumulated_context,
                data_context=data_context,
                writer_stage_idx=writer_stage_idx,
                stage_idx=stage_idx,
                delegator=delegator,
                project_id=project_id,
                backend=backend,
                section=section,
                effective_max_retries=effective_max_retries,
            )

            result.stages.append(stage_result)

            if stage_result.failed:
                result.failed = True
                result.failure_reason = stage_result.failure_reason
                artifact_path = self._write_failed_artifact(section, result, accumulated_context)
                result.artifact_path = artifact_path
                return result

            stage_idx += 1

        result.final_text = accumulated_context
        artifact_path = self._write_artifact(section, result)
        result.artifact_path = artifact_path
        return result

    # ------------------------------------------------------------------
    # Stage execution
    # ------------------------------------------------------------------

    def _run_stage(
        self,
        stage: PipelineStage,
        description: str,
        accumulated_context: str,
        data_context: str,
        writer_stage_idx: int,
        stage_idx: int,
        delegator: AgentDelegator,
        project_id: int | str | None,
        backend: str,
        section: str,
        effective_max_retries: int,
    ) -> tuple[StageResult, str]:
        """Execute a single stage, handling loop-back if configured.

        Returns:
            Tuple of (StageResult, updated_accumulated_context).
        """
        retries_used = 0
        current_context = accumulated_context
        writer_response: str | None = None

        while True:
            full_context = self._build_stage_context(stage, description, current_context)
            response = delegator.delegate(
                agent_type=stage.agent_type,
                description=description,
                project_id=project_id,
                backend=backend,
                journaler_context=full_context,
            )

            if self._is_delegation_error(response):
                stage_result = StageResult(
                    agent_type=stage.agent_type,
                    description=description,
                    response=response,
                    failed=True,
                    failure_reason=response,
                )
                return stage_result, current_context

            # Check for loop-back trigger on checker stages
            if stage.loop_back_on and _NON_COMPLIANT_RE.search(response):
                if retries_used < effective_max_retries:
                    logger.info(
                        "Pipeline: standards-checker found NON-COMPLIANT — looping back to writer "
                        "(retry %d/%d)",
                        retries_used + 1,
                        effective_max_retries,
                    )
                    audit_notes = self._extract_audit_notes(response)
                    # Re-run the writer with the audit notes appended
                    writer_stage = self.stages[writer_stage_idx]
                    writer_desc = writer_stage.description_template.replace("{section}", section)
                    writer_context = self._build_writer_retry_context(
                        data_context=data_context,
                        audit_notes=audit_notes,
                    )
                    writer_response = delegator.delegate(
                        agent_type=writer_stage.agent_type,
                        description=writer_desc,
                        project_id=project_id,
                        backend=backend,
                        journaler_context=writer_context,
                    )
                    if self._is_delegation_error(writer_response):
                        stage_result = StageResult(
                            agent_type=writer_stage.agent_type,
                            description=writer_desc,
                            response=writer_response,
                            failed=True,
                            failure_reason=writer_response,
                        )
                        return stage_result, current_context

                    # Use the new writer output as context for the re-run checker
                    current_context = (
                        f"{data_context}\n\n"
                        f"---\n\n## Revised Draft (after audit)\n\n{writer_response}"
                    )
                    retries_used += 1
                    continue
                else:
                    logger.warning(
                        "Pipeline: standards-checker still NON-COMPLIANT after %d retries — "
                        "failing pipeline.",
                        effective_max_retries,
                    )
                    stage_result = StageResult(
                        agent_type=stage.agent_type,
                        description=description,
                        response=response,
                        loop_triggered=True,
                        retries_used=retries_used,
                        failed=True,
                        failure_reason=(
                            f"Section '{section}' remains NON-COMPLIANT after "
                            f"{effective_max_retries} revision(s). "
                            "Review the audit notes manually."
                        ),
                    )
                    return stage_result, response

            # Stage passed (or no loop-back configured)
            stage_result = StageResult(
                agent_type=stage.agent_type,
                description=description,
                response=response,
                loop_triggered=retries_used > 0,
                retries_used=retries_used,
            )
            new_context = self._forward_context(stage, response, current_context)
            return stage_result, new_context

    def _build_stage_context(self, stage: PipelineStage, description: str, accumulated: str) -> str:
        """Wrap the accumulated context with the stage description for the agent prompt."""
        return (
            f"{accumulated}\n\n"
            f"---\n\n"
            f"**Task**: {description}"
        )

    def _build_writer_retry_context(self, data_context: str, audit_notes: str) -> str:
        """Build context for a writer retry, including audit findings."""
        return (
            f"{data_context}\n\n"
            f"---\n\n"
            f"## Standards Audit — Items Requiring Revision\n\n"
            f"{audit_notes}\n\n"
            f"---\n\n"
            f"Please revise the draft to address the non-compliant items listed above. "
            f"Use the pre-computed data tables provided. Do not modify any numeric values."
        )

    def _forward_context(self, stage: PipelineStage, response: str, previous: str) -> str:
        """Build the context block passed to the next stage."""
        label = stage.agent_type.replace("-", " ").title()
        return (
            f"{previous}\n\n"
            f"---\n\n"
            f"## {label} Output\n\n"
            f"{response}"
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_writer_idx(self) -> int:
        """Return the index of the last technical-writer stage before any checker."""
        for i, s in enumerate(self.stages):
            if s.loop_back_on:
                # Return the stage immediately before
                return max(0, i - 1)
        return 0

    @staticmethod
    def _is_delegation_error(response: str) -> bool:
        """Detect delegation failure responses from AgentDelegator."""
        low = response.lower()
        return (
            low.startswith("unknown agent type")
            or low.startswith("agent task failed")
            or low.startswith("no agent backend")
            or low.startswith("agent execution failed")
        )

    @staticmethod
    def _extract_audit_notes(checker_response: str) -> str:
        """Extract the non-compliant items from a standards-checker response."""
        lines = checker_response.splitlines()
        audit_lines: list[str] = []
        for line in lines:
            if _NON_COMPLIANT_RE.search(line) or line.strip().startswith("-") or line.strip().startswith("*"):
                audit_lines.append(line)
        return "\n".join(audit_lines) if audit_lines else checker_response

    def _write_artifact(self, section: str, result: PipelineResult) -> Path:
        """Write the final pipeline output to disk and return the path."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        slug = re.sub(r"[^\w]+", "_", section.lower()).strip("_")[:40]
        path = self.output_dir / f"pipeline_{slug}_{ts}.md"
        path.write_text(result.final_text, encoding="utf-8")
        logger.info("Pipeline: artifact written to %s", path)
        return path

    def _write_failed_artifact(
        self, section: str, result: PipelineResult, last_context: str
    ) -> Path:
        """Write a PIPELINE_FAILED artifact so engineers can debug manually."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        slug = re.sub(r"[^\w]+", "_", section.lower()).strip("_")[:40]
        path = self.output_dir / f"PIPELINE_FAILED_{slug}_{ts}.md"
        body_lines = [
            f"# PIPELINE FAILED — {section}",
            "",
            f"**Reason**: {result.failure_reason}",
            "",
            "## Stage Outputs",
            "",
        ]
        for s in result.stages:
            body_lines += [
                f"### {s.agent_type}",
                "",
                s.response,
                "",
            ]
        body_lines += ["---", "", "## Last Context State", "", last_context]
        path.write_text("\n".join(body_lines), encoding="utf-8")
        logger.warning("Pipeline: FAILED artifact written to %s", path)
        return path
