"""LaTeX validation workflow: compile .tex files with pdflatex and report errors.

The validator copies the source to a temporary directory so auxiliary files
(*.aux, *.log, *.toc, etc.) never pollute the output directory.  pdflatex is
run twice to resolve TOC and cross-references.  The resulting log is parsed for
fatal errors and warnings.

Optional agent correction loop: on compile failure, the error lines and the
original .tex source are sent back to the LaTeX-writer agent which returns a
corrected document.  The corrected source is validated again (up to
``max_attempts`` times).
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engineering_hub.agents.worker import AgentWorker

logger = logging.getLogger(__name__)

# Regex patterns for pdflatex log parsing
_ERROR_RE = re.compile(r"^!(.*)", re.MULTILINE)
_FATAL_RE = re.compile(
    r"^! (.*?)\n(?:l\.(\d+).*)?", re.MULTILINE | re.DOTALL
)
_WARNING_RE = re.compile(
    r"^(?:LaTeX Warning|Package \w+ Warning|Class \w+ Warning|Overfull|Underfull):(.*)",
    re.MULTILINE,
)
_MISSING_FILE_RE = re.compile(r"! LaTeX Error: File `(.+?)' not found")


@dataclass
class LatexValidationResult:
    """Outcome of a single pdflatex compile attempt."""

    success: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    pdf_path: Path | None = None
    log_text: str = ""

    def summary(self) -> str:
        """One-line human-readable summary."""
        if self.success:
            pdf = f" → {self.pdf_path}" if self.pdf_path else ""
            warn_note = f" ({len(self.warnings)} warning(s))" if self.warnings else ""
            return f"LaTeX compiled successfully{warn_note}{pdf}"
        err_count = len(self.errors)
        return (
            f"LaTeX compile failed: {err_count} error(s). "
            "Run /validate-latex <path> to see details."
        )

    def formatted_errors(self, max_errors: int = 10) -> str:
        """Return errors formatted for display, capped at *max_errors*."""
        if not self.errors:
            return "(no errors recorded)"
        shown = self.errors[:max_errors]
        lines = [f"  [{i + 1}] {e}" for i, e in enumerate(shown)]
        if len(self.errors) > max_errors:
            lines.append(f"  … and {len(self.errors) - max_errors} more error(s)")
        return "\n".join(lines)

    def formatted_warnings(self, max_warnings: int = 5) -> str:
        """Return warnings formatted for display, capped at *max_warnings*."""
        if not self.warnings:
            return "(no warnings)"
        shown = self.warnings[:max_warnings]
        lines = [f"  [{i + 1}] {w.strip()}" for i, w in enumerate(shown)]
        if len(self.warnings) > max_warnings:
            lines.append(f"  … and {len(self.warnings) - max_warnings} more warning(s)")
        return "\n".join(lines)


class LatexValidator:
    """Validate a .tex file by compiling it with pdflatex.

    pdflatex must be installed and on PATH (e.g. via a TeX Live or MacTeX
    installation).  When pdflatex is not found, ``validate()`` returns a
    failure result with an informative error rather than raising.
    """

    def __init__(self, pdflatex_path: str | None = None) -> None:
        self._pdflatex = pdflatex_path or shutil.which("pdflatex") or "pdflatex"

    def is_available(self) -> bool:
        """Return True if pdflatex is on PATH."""
        return shutil.which(self._pdflatex) is not None

    def validate(self, tex_path: Path) -> LatexValidationResult:
        """Compile *tex_path* with pdflatex (twice, for TOC/refs).

        The source file is copied to a temporary directory.  On success the
        generated PDF is also copied back alongside the source.

        Returns a :class:`LatexValidationResult`.
        """
        tex_path = tex_path.expanduser().resolve()

        if not tex_path.exists():
            return LatexValidationResult(
                success=False,
                errors=[f"File not found: {tex_path}"],
            )

        if not self.is_available():
            return LatexValidationResult(
                success=False,
                errors=[
                    "pdflatex not found on PATH. "
                    "Install TeX Live (https://www.tug.org/texlive/) or MacTeX "
                    "(https://www.tug.org/mactex/) to enable LaTeX validation."
                ],
            )

        with tempfile.TemporaryDirectory(prefix="latex_validator_") as tmp_str:
            tmp = Path(tmp_str)
            src = tmp / tex_path.name
            shutil.copy2(tex_path, src)

            log_text = ""
            returncode = 0

            # Run pdflatex twice (first pass: compile; second pass: resolve TOC/refs)
            for pass_num in range(1, 3):
                try:
                    proc = subprocess.run(
                        [
                            self._pdflatex,
                            "-interaction=nonstopmode",
                            "-halt-on-error",
                            str(src),
                        ],
                        cwd=tmp,
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                    returncode = proc.returncode
                    log_text = proc.stdout + proc.stderr
                    if returncode != 0:
                        logger.debug(
                            "pdflatex pass %d exited with code %d", pass_num, returncode
                        )
                        break
                except subprocess.TimeoutExpired:
                    return LatexValidationResult(
                        success=False,
                        errors=["pdflatex timed out after 60 seconds."],
                    )
                except FileNotFoundError:
                    return LatexValidationResult(
                        success=False,
                        errors=[
                            f"pdflatex executable not found at '{self._pdflatex}'. "
                            "Install TeX Live or MacTeX."
                        ],
                    )
                except OSError as exc:
                    return LatexValidationResult(
                        success=False,
                        errors=[f"Failed to run pdflatex: {exc}"],
                    )

            # Also read the .log file if it exists (more complete than stdout)
            log_file = tmp / tex_path.with_suffix(".log").name
            if log_file.exists():
                try:
                    log_text = log_file.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    pass

            errors = _parse_errors(log_text)
            warnings = _parse_warnings(log_text)

            pdf_src = tmp / tex_path.with_suffix(".pdf").name
            pdf_dest: Path | None = None

            if returncode == 0 and pdf_src.exists():
                pdf_dest = tex_path.with_suffix(".pdf")
                shutil.copy2(pdf_src, pdf_dest)

            success = returncode == 0 and not errors
            return LatexValidationResult(
                success=success,
                errors=errors,
                warnings=warnings,
                pdf_path=pdf_dest,
                log_text=log_text,
            )

    def fix_with_agent(
        self,
        tex_path: Path,
        validation_result: LatexValidationResult,
        worker: AgentWorker,
        max_attempts: int = 2,
    ) -> LatexValidationResult:
        """Attempt to fix compile errors by sending them back to the agent.

        Feeds the error list and original .tex source to the LaTeX-writer agent,
        writes the corrected output over *tex_path*, and re-validates.  Repeats
        up to *max_attempts* times.

        Returns the final :class:`LatexValidationResult` (success or last failure).
        """
        from engineering_hub.agents.prompts import PromptLoader
        from engineering_hub.core.constants import AgentType

        try:
            system_prompt = worker._prompt_loader.get_prompt(AgentType.LATEX_WRITER)
        except Exception as exc:
            logger.warning("Could not load latex-writer prompt for fix loop: %s", exc)
            return validation_result

        current_result = validation_result

        for attempt in range(1, max_attempts + 1):
            logger.info(
                "LaTeX fix attempt %d/%d for %s", attempt, max_attempts, tex_path.name
            )

            try:
                original_source = tex_path.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning("Cannot read .tex source for fix attempt: %s", exc)
                break

            error_block = current_result.formatted_errors()
            fix_prompt = (
                "The following LaTeX source failed to compile with pdflatex. "
                "Fix all errors listed below and return the corrected, fully "
                "compilable LaTeX source. Output raw LaTeX only — no markdown "
                "fences, no prose outside the document.\n\n"
                f"## Compile Errors\n\n{error_block}\n\n"
                f"## Original Source\n\n{original_source}"
            )

            try:
                from engineering_hub.core.exceptions import LLMBackendError

                corrected = worker._backend.complete(
                    system_prompt, fix_prompt, worker.max_tokens
                )
            except LLMBackendError as exc:
                logger.warning("LLM fix call failed on attempt %d: %s", attempt, exc)
                break

            # Postprocess and overwrite
            clean = worker._postprocess_output(corrected, ".tex")
            try:
                tex_path.write_text(clean, encoding="utf-8")
            except OSError as exc:
                logger.warning("Cannot write corrected .tex source: %s", exc)
                break

            current_result = self.validate(tex_path)
            if current_result.success:
                logger.info(
                    "LaTeX fix succeeded on attempt %d for %s", attempt, tex_path.name
                )
                return current_result

            logger.info(
                "Fix attempt %d still has %d error(s) in %s",
                attempt,
                len(current_result.errors),
                tex_path.name,
            )

        return current_result


# ---------------------------------------------------------------------------
# Log parsing helpers
# ---------------------------------------------------------------------------


def _parse_errors(log_text: str) -> list[str]:
    """Extract fatal error lines from a pdflatex log."""
    errors: list[str] = []
    for match in _ERROR_RE.finditer(log_text):
        msg = match.group(1).strip()
        if msg and not msg.startswith("="):
            errors.append(msg)
    return errors


def _parse_warnings(log_text: str) -> list[str]:
    """Extract warning lines from a pdflatex log."""
    warnings: list[str] = []
    for match in _WARNING_RE.finditer(log_text):
        msg = match.group(1).strip()
        if msg:
            warnings.append(msg)
    return warnings
