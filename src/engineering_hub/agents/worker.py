"""Agent worker for executing tasks via a pluggable LLM backend."""

import logging
import re
from datetime import datetime
from pathlib import Path

from engineering_hub.agents.backends import AnthropicBackend, LLMBackend
from engineering_hub.agents.prompts import PromptLoader
from engineering_hub.agents.registry import AgentRegistry
from engineering_hub.agents.style_loader import LatexStyle, StyleLoader
from engineering_hub.core.constants import AgentType
from engineering_hub.core.exceptions import AgentExecutionError, LLMBackendError
from engineering_hub.core.models import ParsedTask, TaskResult

logger = logging.getLogger(__name__)


class AgentWorker:
    """Worker that executes agent tasks via a pluggable LLM backend."""

    def __init__(
        self,
        backend: LLMBackend,
        prompts_dir: Path | None = None,
        output_dir: Path | None = None,
        max_tokens: int = 4096,
        styles_dir: Path | None = None,
        templates_dir: Path | None = None,
    ) -> None:
        self._backend = backend
        self.max_tokens = max_tokens
        self.output_dir = output_dir or Path("outputs")

        self._prompt_loader = PromptLoader(prompts_dir or Path("prompts"))
        self._registry = AgentRegistry()
        self._style_loader = StyleLoader(
            styles_dir=styles_dir or Path("latex-styles"),
            templates_dir=templates_dir or Path("latex-templates"),
        )

    @classmethod
    def from_anthropic(
        cls,
        api_key: str,
        model: str = "claude-sonnet-4-5-20250929",
        prompts_dir: Path | None = None,
        output_dir: Path | None = None,
        max_tokens: int = 4096,
        styles_dir: Path | None = None,
        templates_dir: Path | None = None,
    ) -> "AgentWorker":
        """Convenience constructor that creates an AnthropicBackend internally."""
        backend = AnthropicBackend(api_key=api_key, model=model)
        return cls(
            backend=backend,
            prompts_dir=prompts_dir,
            output_dir=output_dir,
            max_tokens=max_tokens,
            styles_dir=styles_dir,
            templates_dir=templates_dir,
        )

    def execute(self, task: ParsedTask, context: str) -> TaskResult:
        """Execute a task with the appropriate agent.

        Args:
            task: The task to execute
            context: Formatted project context

        Returns:
            TaskResult with success status and outputs
        """
        agent_type = task.agent_type

        if not self._registry.is_enabled(agent_type):
            return TaskResult(
                task=task,
                success=False,
                error_message=f"Agent type '{agent_type.value}' is not enabled",
            )

        try:
            system_prompt = self._prompt_loader.get_prompt(agent_type)

            # For LaTeX tasks, handle --list-styles, --style, and --template flags.
            cleaned_description = task.description
            if agent_type == AgentType.LATEX_WRITER:
                list_styles_result = self._handle_list_styles(task)
                if list_styles_result is not None:
                    return list_styles_result
                system_prompt, cleaned_description = self._apply_style_override(
                    system_prompt, task.description
                )

            user_message = self._build_user_message(
                task, context, override_description=cleaned_description
            )

            logger.info(f"Executing {agent_type.value} agent for task: {task.description[:50]}...")
            response = self._backend.complete(system_prompt, user_message, self.max_tokens)

            output_path = self._write_output(task, response)

            # Post-compile validation for LaTeX outputs
            validation_note = ""
            if agent_type == AgentType.LATEX_WRITER and output_path.suffix.lower() == ".tex":
                validation_note = self._validate_latex_output(output_path)

            logger.info(f"Task completed successfully, output: {output_path}")
            full_response = response if not validation_note else f"{response}\n\n{validation_note}"
            return TaskResult(
                task=task,
                success=True,
                output_path=str(output_path),
                agent_response=full_response,
            )

        except LLMBackendError as e:
            logger.error(f"LLM backend error: {e}")
            return TaskResult(
                task=task,
                success=False,
                error_message=f"LLM backend error: {e}",
            )
        except Exception as e:
            logger.error(f"Agent execution failed: {e}")
            return TaskResult(
                task=task,
                success=False,
                error_message=str(e),
            )

    # ------------------------------------------------------------------
    # Style / template helpers
    # ------------------------------------------------------------------

    def _handle_list_styles(self, task: ParsedTask) -> TaskResult | None:
        """Return a TaskResult listing available styles if ``--list-styles`` is present.

        Returns ``None`` if the flag is absent and normal execution should proceed.
        """
        if "--list-styles" not in task.description:
            return None

        style_names = self._style_loader.list_styles()
        template_names = self._style_loader.list_templates()
        lines = [
            "## Available LaTeX Styles",
            "",
            "Use `--style <name>` in your `/agent latex-writer` invocation.",
            "",
        ]
        for name in style_names:
            lines.append(f"- `{name}`")
        lines += [
            "",
            "## Available LaTeX Preamble Templates",
            "",
            "Use `--template <stem>` to load a raw `.tex` preamble partial.",
            "",
        ]
        for stem in template_names:
            lines.append(f"- `{stem}`")

        listing = "\n".join(lines)
        return TaskResult(task=task, success=True, agent_response=listing)

    # Flag patterns for --style and --template
    _STYLE_FLAG_RE = re.compile(r"--style\s+(\S+)")
    _TEMPLATE_FLAG_RE = re.compile(r"--template\s+(\S+)")

    def _apply_style_override(
        self, system_prompt: str, description: str
    ) -> tuple[str, str]:
        """Resolve any ``--style``/``--template`` flags and inject into the system prompt.

        Returns:
            A tuple of ``(modified_system_prompt, cleaned_description)`` where
            flags have been stripped from the description.
        """
        style_match = self._STYLE_FLAG_RE.search(description)
        template_match = self._TEMPLATE_FLAG_RE.search(description)

        if not style_match and not template_match:
            return system_prompt, description

        style: LatexStyle | None = None
        try:
            if template_match:
                stem = template_match.group(1)
                style = self._style_loader.load_template(stem)
                logger.info("LaTeX template override: %s", stem)
            elif style_match:
                name = style_match.group(1)
                style = self._style_loader.load(name)
                logger.info("LaTeX style override: %s", name)
        except FileNotFoundError as exc:
            logger.warning("Style/template not found — using default preamble: %s", exc)
            style = None

        cleaned = self._STYLE_FLAG_RE.sub("", description)
        cleaned = self._TEMPLATE_FLAG_RE.sub("", cleaned).strip()

        if style is None:
            return system_prompt, cleaned

        modified_prompt = self._inject_style(system_prompt, style)
        return modified_prompt, cleaned

    @staticmethod
    def _inject_style(system_prompt: str, style: LatexStyle) -> str:
        """Replace the ``<preamble_template>`` block in *system_prompt* with *style*.

        If no ``<preamble_template>`` tag pair is found the rendered preamble is
        appended as a clearly labelled override block so the agent still sees it.
        """
        override_lines = [
            f"<preamble_template>",
            f"% STYLE OVERRIDE: {style.display_name}",
            style.preamble_tex,
            "</preamble_template>",
        ]
        if style.section_structure:
            override_lines += [
                "",
                "<section_structure_hint>",
                "SECTION STRUCTURE HINT — apply in preference to the default output_format skeleton:",
                style.section_structure.strip(),
                "</section_structure_hint>",
            ]
        override_block = "\n".join(override_lines)

        start_tag = "<preamble_template>"
        end_tag = "</preamble_template>"
        start_idx = system_prompt.find(start_tag)
        end_idx = system_prompt.find(end_tag)

        if start_idx != -1 and end_idx != -1:
            return (
                system_prompt[:start_idx]
                + override_block
                + system_prompt[end_idx + len(end_tag):]
            )

        logger.warning(
            "Could not find <preamble_template> block in system prompt — "
            "appending style override at end."
        )
        return system_prompt + "\n\n" + override_block

    # ------------------------------------------------------------------
    # Message building
    # ------------------------------------------------------------------

    def _build_user_message(
        self,
        task: ParsedTask,
        context: str,
        override_description: str | None = None,
    ) -> str:
        """Build the user message for the agent."""
        description = override_description if override_description is not None else task.description
        parts = [
            context,
            "",
            "---",
            "",
            "## Your Task",
            "",
            f"**Task**: {description}",
        ]

        if task.context:
            parts.append(f"**Additional Context**: {task.context}")

        if task.deliverable:
            parts.append(f"**Deliverable**: Create output at {task.deliverable}")

        if task.agent_type == AgentType.LATEX_WRITER:
            parts.extend(
                [
                    "",
                    "Please complete this task based on the project context above.",
                    "Output raw LaTeX source only — no markdown fences, no prose outside the document.",
                ]
            )
        else:
            parts.extend(
                [
                    "",
                    "Please complete this task based on the project context above.",
                    "Provide your response in a structured markdown format.",
                ]
            )

        return "\n".join(parts)

    @staticmethod
    def _postprocess_output(response: str, ext: str) -> str:
        """Strip markdown code fences and extract clean content from LLM output.

        For .tex files: extracts the substring from the first \\documentclass to
        the last \\end{document} (inclusive), guaranteeing compilable LaTeX regardless
        of any prose or fence markers the model may have added.

        For .md files: strips a single outer code fence if present.

        Logs a warning when stripping was necessary so the issue is visible in logs.
        """
        if ext == ".tex":
            # Check if the response is already clean (starts with \documentclass)
            stripped = response.strip()
            if stripped.startswith("\\documentclass"):
                return stripped

            # Try to extract clean LaTeX content between \documentclass and \end{document}
            start = response.find("\\documentclass")
            end = response.rfind("\\end{document}")
            if start != -1 and end != -1:
                logger.warning(
                    "LaTeX agent output contained non-LaTeX content "
                    "(markdown fences or prose) — stripping automatically. "
                    "Check the prompt or add no-fence instruction."
                )
                return response[start : end + len("\\end{document}")].strip()

            # Fallback: strip markdown fences only
            fence_pattern = re.compile(
                r"^```(?:latex|tex)?\s*\n(.*?)\n```\s*$", re.DOTALL | re.MULTILINE
            )
            match = fence_pattern.search(response)
            if match:
                logger.warning(
                    "LaTeX agent output was wrapped in a markdown code fence — stripping."
                )
                return match.group(1).strip()

            logger.warning(
                "LaTeX agent output does not appear to be valid LaTeX "
                "(no \\documentclass found). Writing raw response."
            )
            return response

        if ext == ".md":
            # Strip a single outer code fence (matches postprocess_model_org behaviour)
            stripped = response.strip()
            if stripped.startswith("```") and stripped.endswith("```"):
                inner = stripped[3:]
                if "\n" in inner:
                    # Drop the opening language tag line (e.g. "markdown\n")
                    inner = inner.split("\n", 1)[1]
                if inner.endswith("```"):
                    inner = inner[: -len("```")]
                logger.debug("Stripped outer markdown fence from .md agent output.")
                return inner.strip()

        return response

    def _write_output(self, task: ParsedTask, response: str) -> Path:
        """Write agent response to output file."""
        if task.deliverable:
            output_path = self.output_dir / task.deliverable.lstrip("/")
        else:
            agent_dirs = {
                AgentType.RESEARCH: "research",
                AgentType.TECHNICAL_WRITER: "docs",
                AgentType.STANDARDS_CHECKER: "analysis",
                AgentType.REF_ENGINEER: "reviews",
                AgentType.EVALUATOR: "analysis",
                AgentType.TECHNICAL_REVIEWER: "reviews",
                AgentType.LATEX_WRITER: "latex",
                AgentType.PANNING_FOR_GOLD: "panning",
            }
            agent_extensions = {
                AgentType.LATEX_WRITER: ".tex",
            }
            agent_dir = agent_dirs.get(task.agent_type, "outputs")
            ext = agent_extensions.get(task.agent_type, ".md")
            project_id = task.project_id or "unknown"

            desc_slug = "".join(
                c if c.isalnum() or c == "-" else "-"
                for c in task.description[:30].lower()
            )
            desc_slug = "-".join(filter(None, desc_slug.split("-")))

            if ext == ".tex":
                timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                filename = f"project-{project_id}-{desc_slug}-{timestamp}{ext}"
            else:
                filename = f"project-{project_id}-{desc_slug}{ext}"

            output_path = self.output_dir / agent_dir / filename

        output_path.parent.mkdir(parents=True, exist_ok=True)
        clean_response = self._postprocess_output(response, output_path.suffix.lower())
        output_path.write_text(clean_response, encoding="utf-8")
        logger.debug(f"Wrote output to {output_path}")

        return output_path

    def _validate_latex_output(self, tex_path: Path) -> str:
        """Run pdflatex on *tex_path* and return a one-line validation summary.

        Returns an empty string if pdflatex is not installed (validation is
        skipped silently so offline/CI environments are unaffected).
        """
        from engineering_hub.agents.latex_validator import LatexValidator

        validator = LatexValidator()
        if not validator.is_available():
            logger.debug("pdflatex not found — skipping LaTeX validation.")
            return ""

        logger.info("Validating LaTeX output: %s", tex_path.name)
        result = validator.validate(tex_path)

        if result.success:
            logger.info("LaTeX validation passed for %s", tex_path.name)
        else:
            logger.warning(
                "LaTeX validation failed for %s: %d error(s)",
                tex_path.name,
                len(result.errors),
            )

        return f"[LaTeX Validation] {result.summary()}"

    def run_weekly_review(self, context: str, output_path: Path) -> str:
        """Run the weekly reviewer agent with pre-built context.

        Unlike execute(), this method takes the full context string directly
        (no ParsedTask). The weekly reviewer prompt is loaded from
        prompts/weekly-reviewer.txt.

        Args:
            context: Pre-built context string (journal entries + agent work)
            output_path: Where to write the review report

        Returns:
            The agent response text

        Raises:
            AgentExecutionError: If the LLM call or file write fails
        """
        config = self._registry.get_config(AgentType.WEEKLY_REVIEWER)
        max_tokens = config.max_tokens if config else 6000

        system_prompt = self._prompt_loader.get_prompt(AgentType.WEEKLY_REVIEWER)

        logger.info("Running weekly reviewer agent...")
        try:
            response = self._backend.complete(system_prompt, context, max_tokens)
        except LLMBackendError as e:
            raise AgentExecutionError(f"LLM error during weekly review: {e}") from e

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(response, encoding="utf-8")
        logger.info(f"Weekly review written to {output_path}")

        return response

    def test_connection(self) -> bool:
        """Test the LLM backend connection.

        Returns:
            True if connection is successful
        """
        return self._backend.test_connection()
