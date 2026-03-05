"""Agent worker for executing Claude API calls."""

import logging
from pathlib import Path

import anthropic

from engineering_hub.agents.prompts import PromptLoader
from engineering_hub.agents.registry import AgentRegistry
from engineering_hub.core.constants import AgentType
from engineering_hub.core.exceptions import AgentExecutionError
from engineering_hub.core.models import ParsedTask, TaskResult

logger = logging.getLogger(__name__)


class AgentWorker:
    """Worker that executes agent tasks via Claude API."""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-5-20250929",
        prompts_dir: Path | None = None,
        output_dir: Path | None = None,
        max_tokens: int = 4000,
    ) -> None:
        """Initialize the agent worker.

        Args:
            api_key: Anthropic API key
            model: Claude model to use
            prompts_dir: Directory containing prompt files
            output_dir: Directory for output files
            max_tokens: Maximum tokens for responses
        """
        self.model = model
        self.max_tokens = max_tokens
        self.output_dir = output_dir or Path("outputs")

        # Initialize Anthropic client
        self._client = anthropic.Anthropic(api_key=api_key)

        # Initialize prompt loader and registry
        self._prompt_loader = PromptLoader(prompts_dir or Path("prompts"))
        self._registry = AgentRegistry()

    def execute(self, task: ParsedTask, context: str) -> TaskResult:
        """Execute a task with the appropriate agent.

        Args:
            task: The task to execute
            context: Formatted project context

        Returns:
            TaskResult with success status and outputs
        """
        agent_type = task.agent_type

        # Check if agent is enabled
        if not self._registry.is_enabled(agent_type):
            return TaskResult(
                task=task,
                success=False,
                error_message=f"Agent type '{agent_type.value}' is not enabled",
            )

        try:
            # Get system prompt
            system_prompt = self._prompt_loader.get_prompt(agent_type)

            # Build user message with context and task
            user_message = self._build_user_message(task, context)

            # Execute Claude API call
            logger.info(f"Executing {agent_type.value} agent for task: {task.description[:50]}...")
            response = self._call_claude(system_prompt, user_message)

            # Write output to file
            output_path = self._write_output(task, response)

            logger.info(f"Task completed successfully, output: {output_path}")
            return TaskResult(
                task=task,
                success=True,
                output_path=str(output_path),
                agent_response=response,
            )

        except anthropic.APIError as e:
            logger.error(f"Claude API error: {e}")
            return TaskResult(
                task=task,
                success=False,
                error_message=f"Claude API error: {e}",
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

    def _call_claude(self, system_prompt: str, user_message: str) -> str:
        """Make the Claude API call.

        Args:
            system_prompt: System prompt for the agent
            user_message: User message with context and task

        Returns:
            Agent response text
        """
        message = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )

        # Extract text from response
        text_parts = []
        for block in message.content:
            if hasattr(block, "text"):
                text_parts.append(block.text)

        return "\n".join(text_parts)

    def _write_output(self, task: ParsedTask, response: str) -> Path:
        """Write agent response to output file.

        Args:
            task: The task
            response: Agent response text

        Returns:
            Path to the output file
        """
        # Determine output path
        if task.deliverable:
            # Use specified path (relative to output dir)
            output_path = self.output_dir / task.deliverable.lstrip("/")
        else:
            # Generate default path
            agent_dirs = {
                AgentType.RESEARCH: "research",
                AgentType.TECHNICAL_WRITER: "docs",
                AgentType.STANDARDS_CHECKER: "analysis",
                AgentType.REF_ENGINEER: "reviews",
                AgentType.EVALUATOR: "analysis",
                AgentType.TECHNICAL_REVIEWER: "reviews",
            }
            agent_dir = agent_dirs.get(task.agent_type, "outputs")
            project_id = task.project_id or "unknown"

            # Create filename from description
            desc_slug = "".join(
                c if c.isalnum() or c == "-" else "-"
                for c in task.description[:30].lower()
            )
            desc_slug = "-".join(filter(None, desc_slug.split("-")))

            output_path = self.output_dir / agent_dir / f"project-{project_id}-{desc_slug}.md"

        # Ensure directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Write response (raw for .tex, no markdown wrapping)
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
            AgentExecutionError: If the Claude API call or file write fails
        """
        config = self._registry.get_config(AgentType.WEEKLY_REVIEWER)
        max_tokens = config.max_tokens if config else 6000

        system_prompt = self._prompt_loader.get_prompt(AgentType.WEEKLY_REVIEWER)

        logger.info("Running weekly reviewer agent...")
        try:
            message = self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": context}],
            )
        except anthropic.APIError as e:
            raise AgentExecutionError(f"Claude API error during weekly review: {e}") from e

        text_parts = [block.text for block in message.content if hasattr(block, "text")]
        response = "\n".join(text_parts)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(response, encoding="utf-8")
        logger.info(f"Weekly review written to {output_path}")

        return response

    def test_connection(self) -> bool:
        """Test the Claude API connection.

        Returns:
            True if connection is successful
        """
        try:
            # Simple test call
            self._client.messages.create(
                model=self.model,
                max_tokens=10,
                messages=[{"role": "user", "content": "Hi"}],
            )
            return True
        except Exception as e:
            logger.error(f"Claude API connection test failed: {e}")
            return False
