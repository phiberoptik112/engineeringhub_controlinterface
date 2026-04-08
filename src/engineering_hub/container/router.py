"""Task routing layer: decides whether to execute locally or in a container.

MLX tasks always run locally (requires Apple Silicon Metal).
Anthropic and Ollama tasks can be routed to Docker containers when enabled.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from engineering_hub.agents.prompts import PromptLoader
from engineering_hub.container.docker_executor import DockerExecutor
from engineering_hub.core.models import ParsedTask, TaskResult

if TYPE_CHECKING:
    from engineering_hub.agents.worker import AgentWorker
    from engineering_hub.config.settings import Settings

logger = logging.getLogger(__name__)

_CONTAINERISABLE_PROVIDERS = frozenset({"anthropic", "ollama"})


class TaskRouter:
    """Routes agent tasks to local execution or Docker containers."""

    def __init__(self, settings: Settings, agent_worker: AgentWorker) -> None:
        self._docker_enabled = settings.docker_enabled
        self._provider = settings.llm_provider.lower()
        self._local_worker = agent_worker
        self._prompt_loader = PromptLoader(settings.prompts_dir)
        self._docker_executor: DockerExecutor | None = None

        if self._docker_enabled and self._provider in _CONTAINERISABLE_PROVIDERS:
            self._docker_executor = DockerExecutor(settings)
            logger.info(
                f"Docker execution enabled (provider={self._provider}, "
                f"image={settings.docker_task_image})"
            )
        elif self._docker_enabled and self._provider not in _CONTAINERISABLE_PROVIDERS:
            logger.warning(
                f"Docker execution requested but provider '{self._provider}' "
                f"cannot run in containers — falling back to local execution."
            )

    @property
    def is_containerised(self) -> bool:
        return self._docker_executor is not None

    def execute(self, task: ParsedTask, context: str) -> TaskResult:
        """Execute a task via the appropriate path."""
        if self._should_containerise(task):
            return self._execute_in_container(task, context)
        return self._local_worker.execute(task, context)

    def _should_containerise(self, task: ParsedTask) -> bool:
        return (
            self._docker_executor is not None
            and self._provider in _CONTAINERISABLE_PROVIDERS
        )

    def _execute_in_container(self, task: ParsedTask, context: str) -> TaskResult:
        assert self._docker_executor is not None
        system_prompt = self._prompt_loader.get_prompt(task.agent_type)
        user_message = self._local_worker._build_user_message(task, context)

        logger.info(f"Routing to Docker container: {task.description[:60]}...")
        return self._docker_executor.execute_task(
            task=task,
            context=user_message,
            system_prompt=system_prompt,
        )

    def docker_status(self) -> dict | None:
        if self._docker_executor:
            return self._docker_executor.status()
        return None
