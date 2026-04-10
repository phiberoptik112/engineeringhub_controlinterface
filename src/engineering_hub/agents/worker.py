"""Agent worker for executing tasks via a pluggable LLM backend."""

import logging
from pathlib import Path

from engineering_hub.agents.backends import AnthropicBackend, LLMBackend
from engineering_hub.agents.prompts import PromptLoader
from engineering_hub.agents.registry import AgentRegistry
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
    ) -> None:
        self._backend = backend
        self.max_tokens = max_tokens
        self.output_dir = output_dir or Path("outputs")

        self._prompt_loader = PromptLoader(prompts_dir or Path("prompts"))
        self._registry = AgentRegistry()

    @classmethod
    def from_anthropic(
        cls,
        api_key: str,
        model: str = "claude-sonnet-4-5-20250929",
        prompts_dir: Path | None = None,
        output_dir: Path | None = None,
        max_tokens: int = 4096,
    ) -> "AgentWorker":
        """Convenience constructor that creates an AnthropicBackend internally."""
        backend = AnthropicBackend(api_key=api_key, model=model)
        return cls(
            backend=backend,
            prompts_dir=prompts_dir,
            output_dir=output_dir,
            max_tokens=max_tokens,
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
            user_message = self._build_user_message(task, context)

            logger.info(f"Executing {agent_type.value} agent for task: {task.description[:50]}...")
            response = self._backend.complete(system_prompt, user_message, self.max_tokens)

            output_path = self._write_output(task, response)

            logger.info(f"Task completed successfully, output: {output_path}")
            return TaskResult(
                task=task,
                success=True,
                output_path=str(output_path),
                agent_response=response,
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

    def _build_user_message(self, task: ParsedTask, context: str) -> str:
        """Build the user message for the agent."""
        parts = [
            context,
            "",
            "---",
            "",
            "## Your Task",
            "",
            f"**Task**: {task.description}",
        ]

        if task.context:
            parts.append(f"**Additional Context**: {task.context}")

        if task.deliverable:
            parts.append(f"**Deliverable**: Create output at {task.deliverable}")

        parts.extend(
            [
                "",
                "Please complete this task based on the project context above.",
                "Provide your response in a structured markdown format.",
            ]
        )

        return "\n".join(parts)

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

            output_path = self.output_dir / agent_dir / f"project-{project_id}-{desc_slug}{ext}"

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(response, encoding="utf-8")
        logger.debug(f"Wrote output to {output_path}")

        return output_path

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
