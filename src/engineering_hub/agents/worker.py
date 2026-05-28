"""Agent worker for executing tasks via a pluggable LLM backend."""

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from engineering_hub.agents.backends import AnthropicBackend, LLMBackend
from engineering_hub.agents.prompts import PromptLoader
from engineering_hub.agents.registry import AgentRegistry, ModelClass
from engineering_hub.agents.style_loader import LatexStyle, StyleLoader
from engineering_hub.agents.tools import ToolContext, resolve_tools
from engineering_hub.core.constants import AgentType
from engineering_hub.core.exceptions import AgentExecutionError, LLMBackendError
from engineering_hub.core.models import ParsedTask, TaskResult
from engineering_hub.diagnostics.prompt_addendum import DIAGNOSTIC_CONTEXT_AUDIT_ADDENDUM
from engineering_hub.zettelkasten.linking import suggest_links
from engineering_hub.zettelkasten.proposals import write_proposal_batch
from engineering_hub.zettelkasten.response_parser import parse_curator_response
from engineering_hub.zettelkasten.models import ProposalBatch
from engineering_hub.zettelkasten.state import ZettelkastenState, new_batch_id

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 10


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
        corpus_service: Any | None = None,
        memory_service: Any | None = None,
        diagnostic_context_audit: bool = False,
        proposal_dir: Path | None = None,
        zettel_state_path: Path | None = None,
        org_journal_dir: Path | None = None,
    ) -> None:
        self._backend = backend
        self.max_tokens = max_tokens
        self.output_dir = output_dir or Path("outputs")
        self.diagnostic_context_audit = diagnostic_context_audit

        self._prompt_loader = PromptLoader(prompts_dir or Path("prompts"))
        self._registry = AgentRegistry()
        self._style_loader = StyleLoader(
            styles_dir=styles_dir or Path("latex-styles"),
            templates_dir=templates_dir or Path("latex-templates"),
        )
        self._corpus_service = corpus_service
        self._memory_service = memory_service
        self._proposal_dir = proposal_dir
        self._zettel_state_path = zettel_state_path
        self._org_journal_dir = org_journal_dir

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
        corpus_service: Any | None = None,
        memory_service: Any | None = None,
        proposal_dir: Path | None = None,
        zettel_state_path: Path | None = None,
        org_journal_dir: Path | None = None,
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
            corpus_service=corpus_service,
            memory_service=memory_service,
            diagnostic_context_audit=False,
            proposal_dir=proposal_dir,
            zettel_state_path=zettel_state_path,
            org_journal_dir=org_journal_dir,
        )

    def execute(self, task: ParsedTask, context: str) -> TaskResult:
        """Execute a task with the appropriate agent.

        Args:
            task: The task to execute
            context: Formatted project context

        Returns:
            TaskResult with success status and outputs
        """
        return self.execute_with_options(task, context)

    def execute_with_options(
        self,
        task: ParsedTask,
        context: str,
        *,
        anthropic_web_search: bool = False,
        anthropic_web_search_tool_version: str = "web_search_20250305",
        anthropic_web_search_max_uses: int = 3,
    ) -> TaskResult:
        """Execute a task with optional backend-specific execution options."""
        agent_type = task.agent_type

        if not self._registry.is_enabled(agent_type):
            return TaskResult(
                task=task,
                success=False,
                error_message=f"Agent type '{agent_type.value}' is not enabled",
            )

        config = self._registry.get_config(agent_type)

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

            if self.diagnostic_context_audit:
                system_prompt = (
                    system_prompt.rstrip() + "\n\n" + DIAGNOSTIC_CONTEXT_AUDIT_ADDENDUM
                )

            user_message = self._build_user_message(
                task, context, override_description=cleaned_description
            )

            logger.info(f"Executing {agent_type.value} agent for task: {task.description[:50]}...")

            use_tools = (
                config is not None
                and config.model_class == ModelClass.TOOL_USE
                and config.tools
                and hasattr(self._backend, "complete_with_tools")
            )
            if anthropic_web_search and isinstance(self._backend, AnthropicBackend):
                logger.info("Using Anthropic server-side web search fallback for agent task")
                max_tok = config.max_tokens if config else self.max_tokens
                response = self._backend.complete_with_web_search(
                    system_prompt,
                    user_message,
                    max_tok,
                    tool_version=anthropic_web_search_tool_version,
                    max_uses=anthropic_web_search_max_uses,
                )
            elif use_tools:
                response = self._execute_with_tools(
                    task=task,
                    system_prompt=system_prompt,
                    user_message=user_message,
                    tool_names=config.tools,  # type: ignore[union-attr]
                    max_tokens=config.max_tokens,  # type: ignore[union-attr]
                )
            else:
                max_tok = config.max_tokens if config else self.max_tokens
                response = self._backend.complete(system_prompt, user_message, max_tok)

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
        except NotImplementedError as e:
            logger.warning(
                "Backend does not support tool calling, falling back to single-shot: %s", e
            )
            max_tok = config.max_tokens if config else self.max_tokens
            no_tool_note = (
                "\n\n[Note: Tool calling is not available in this backend. "
                "Do not attempt to call tools or emit tool call syntax. "
                "Provide your best answer using only the context and knowledge "
                "already provided.]\n"
            )
            augmented_system = system_prompt + no_tool_note
            try:
                response = self._backend.complete(augmented_system, user_message, max_tok)
                output_path = self._write_output(task, response)
                return TaskResult(
                    task=task,
                    success=True,
                    output_path=str(output_path),
                    agent_response=response,
                )
            except Exception as fallback_err:
                return TaskResult(
                    task=task,
                    success=False,
                    error_message=str(fallback_err),
                )
        except Exception as e:
            logger.error(f"Agent execution failed: {e}")
            return TaskResult(
                task=task,
                success=False,
                error_message=str(e),
            )

    # ------------------------------------------------------------------
    # Agentic tool-use loop
    # ------------------------------------------------------------------

    def _execute_with_tools(
        self,
        task: ParsedTask,
        system_prompt: str,
        user_message: str,
        tool_names: list[str],
        max_tokens: int,
    ) -> str:
        """Drive an agentic tool-call loop until the model stops requesting tools
        or MAX_TOOL_ITERATIONS is reached."""

        tool_defs = resolve_tools(tool_names)
        if not tool_defs:
            logger.debug("No resolvable tools for %s — falling back to single-shot.", task.agent)
            return self._backend.complete(system_prompt, user_message, max_tokens)

        tool_schemas = [t.schema for t in tool_defs]
        handler_map = {t.schema["name"]: t.handler for t in tool_defs}

        tool_ctx = ToolContext(
            corpus_service=self._corpus_service,
            memory_service=self._memory_service,
            output_dir=self.output_dir,
            project_id=task.project_id if isinstance(task.project_id, int) else None,
        )

        messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
        response = None

        complete_with_tools = getattr(self._backend, "complete_with_tools")
        for _iteration in range(MAX_TOOL_ITERATIONS):
            response = complete_with_tools(
                system=system_prompt,
                messages=messages,
                tools=tool_schemas,
                max_tokens=max_tokens,
            )

            if response.stop_reason != "tool_use" or not response.tool_calls:
                return response.text or ""

            # Build the assistant content block from the raw response
            assistant_content = response.raw.get("content", [])
            messages.append({"role": "assistant", "content": assistant_content})

            tool_results = []
            for call in response.tool_calls:
                handler = handler_map.get(call.name)
                if handler is None:
                    result_text = f"Unknown tool '{call.name}' — not available."
                    logger.warning("Tool call to unregistered tool: %s", call.name)
                else:
                    try:
                        result_text = handler(call.arguments, tool_ctx)
                        logger.debug("Tool %s → %d chars", call.name, len(result_text))
                    except Exception as exc:
                        result_text = f"Tool '{call.name}' raised an error: {exc}"
                        logger.error("Tool %s failed: %s", call.name, exc)

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": result_text,
                })

            messages.append({"role": "user", "content": tool_results})

        logger.warning(
            "Agent %s hit MAX_TOOL_ITERATIONS (%d) — returning partial output.",
            task.agent_type,
            MAX_TOOL_ITERATIONS,
        )
        return (response.text if response else "") or (
            "(Agent reached iteration limit without producing final output.)"
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
            "<preamble_template>",
            f"% STYLE OVERRIDE: {style.display_name}",
            style.preamble_tex,
            "</preamble_template>",
        ]
        if style.section_structure:
            override_lines += [
                "",
                "<section_structure_hint>",
                "SECTION STRUCTURE HINT — apply in preference to the "
                "default output_format skeleton:",
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
                    "Output raw LaTeX source only — no markdown fences, "
                    "no prose outside the document.",
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

        if ext in (".md", ".org"):
            # Strip a single outer code fence (matches postprocess_model_org behaviour)
            stripped = response.strip()
            if stripped.startswith("```") and stripped.endswith("```"):
                inner = stripped[3:]
                if "\n" in inner:
                    # Drop the opening language tag line (e.g. "markdown\n", "org\n")
                    inner = inner.split("\n", 1)[1]
                if inner.endswith("```"):
                    inner = inner[: -len("```")]
                logger.debug("Stripped outer code fence from %s agent output.", ext)
                return inner.strip()

        return response

    def _write_output(self, task: ParsedTask, response: str) -> Path:
        """Write agent response to output file."""
        # Zettelkasten curator: route through the proposals pipeline when configured.
        if task.agent_type == AgentType.ZETTELKASTEN_CURATOR and self._proposal_dir:
            return self._write_zettel_proposal(task, response)

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
                AgentType.ZETTELKASTEN_CURATOR: "zettelkasten",
            }
            agent_extensions = {
                AgentType.LATEX_WRITER: ".tex",
                AgentType.ZETTELKASTEN_CURATOR: ".org",
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

    def _write_zettel_proposal(self, task: ParsedTask, response: str) -> Path:
        """Parse an LLM curator response and write it through the proposals pipeline.

        Produces a JSON sidecar and an org review buffer via write_proposal_batch().
        Falls back to the plain .org dump if parsing yields no notes.
        """
        assert self._proposal_dir is not None

        notes = parse_curator_response(
            response,
            task_description=task.description,
            org_journal_dir=self._org_journal_dir,
        )

        if not notes:
            logger.warning(
                "Zettelkasten curator response yielded no parseable notes — "
                "falling back to plain .org dump."
            )
            fallback_dir = self._proposal_dir
            fallback_dir.mkdir(parents=True, exist_ok=True)
            slug = "".join(
                c if c.isalnum() or c == "-" else "-"
                for c in task.description[:30].lower()
            )
            slug = "-".join(filter(None, slug.split("-")))
            fallback_path = fallback_dir / f"{new_batch_id()}-{slug}-raw.org"
            fallback_path.write_text(
                self._postprocess_output(response, ".org"), encoding="utf-8"
            )
            return fallback_path

        # Enrich each note with semantic link suggestions from the memory store.
        for note in notes:
            if not note.links and self._memory_service is not None:
                note.links = suggest_links(
                    note.body,
                    self._memory_service,
                    top_k=5,
                    threshold=0.75,
                )

        state: ZettelkastenState | None = None
        if self._zettel_state_path is not None:
            state = ZettelkastenState.load(self._zettel_state_path)

        batch = ProposalBatch(
            batch_id=new_batch_id(),
            created_at=datetime.now().isoformat(timespec="seconds"),
            notes=notes,
            source_count=len(notes),
        )

        _json_path, org_path = write_proposal_batch(
            batch, self._proposal_dir, state=state
        )

        if state is not None and self._zettel_state_path is not None:
            state.save(self._zettel_state_path)

        logger.info(
            "Zettelkasten proposal batch %s written: %d note(s) → %s",
            batch.batch_id,
            len(notes),
            org_path,
        )
        return org_path

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
