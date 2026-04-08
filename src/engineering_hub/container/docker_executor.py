"""Host-side Docker container lifecycle manager.

Spawns ephemeral containers for each agent task, manages volumes,
environment injection, resource limits, concurrency, and cleanup.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from engineering_hub.container.resource_limits import ResourceLimits
from engineering_hub.container.task_payload import BackendConfig, TaskPayload
from engineering_hub.core.models import ParsedTask, TaskResult

if TYPE_CHECKING:
    from engineering_hub.config.settings import Settings

logger = logging.getLogger(__name__)


class DockerExecutorError(Exception):
    """Raised when Docker operations fail."""


class DockerExecutor:
    """Manages Docker container lifecycle for agent task execution."""

    def __init__(self, settings: Settings) -> None:
        self._image = settings.docker_task_image
        self._network = settings.docker_network
        self._limits = ResourceLimits.from_settings(settings)
        self._max_concurrent = settings.docker_max_concurrent
        self._timeout = settings.docker_task_timeout
        self._semaphore = threading.Semaphore(self._max_concurrent)

        self._provider = settings.llm_provider.lower()
        self._anthropic_api_key = settings.anthropic_api_key.get_secret_value()
        self._ollama_host = settings.docker_ollama_host
        self._settings = settings

    def execute_task(
        self,
        task: ParsedTask,
        context: str,
        system_prompt: str,
    ) -> TaskResult:
        """Run a task inside an ephemeral Docker container.

        Blocks until the container exits or times out.
        """
        backend_config = self._build_backend_config()
        payload = TaskPayload.build(
            parsed_task=task,
            context=context,
            system_prompt=system_prompt,
            backend=backend_config,
        )

        acquired = self._semaphore.acquire(timeout=self._timeout)
        if not acquired:
            return TaskResult(
                task=task,
                success=False,
                error_message=(
                    f"Timed out waiting for a container slot "
                    f"(max_concurrent={self._max_concurrent})"
                ),
            )

        tmp_dir = None
        try:
            tmp_dir = Path(tempfile.mkdtemp(prefix="ehub-task-"))
            task_dir = tmp_dir / "task"
            output_dir = tmp_dir / "output"
            task_dir.mkdir()
            output_dir.mkdir()

            payload.write(task_dir / "payload.json")

            cmd = self._build_docker_cmd(task_dir, output_dir)
            logger.info(f"Launching container for task: {task.description[:60]}...")
            logger.debug(f"Docker command: {' '.join(cmd)}")

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout + 30,
            )

            if result.returncode != 0:
                stderr = result.stderr.strip()
                logger.error(f"Container exited {result.returncode}: {stderr[:500]}")
                return self._read_result_or_error(
                    task, output_dir, fallback_error=stderr[:500]
                )

            return self._read_result(task, output_dir)

        except subprocess.TimeoutExpired:
            logger.error(f"Container timed out after {self._timeout}s")
            return TaskResult(
                task=task,
                success=False,
                error_message=f"Container timed out after {self._timeout} seconds",
            )
        except FileNotFoundError:
            return TaskResult(
                task=task,
                success=False,
                error_message="Docker CLI not found. Is Docker installed and in PATH?",
            )
        except Exception as exc:
            logger.error(f"Docker execution failed: {exc}")
            return TaskResult(
                task=task,
                success=False,
                error_message=f"Docker execution failed: {exc}",
            )
        finally:
            self._semaphore.release()
            if tmp_dir and tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)

    def _build_backend_config(self) -> BackendConfig:
        if self._provider == "anthropic":
            return BackendConfig(
                provider="anthropic",
                model=self._settings.anthropic_model,
                max_tokens=self._settings.max_tokens,
                anthropic_model=self._settings.anthropic_model,
            )
        elif self._provider == "ollama":
            return BackendConfig(
                provider="ollama",
                model=self._settings.ollama_chat_model,
                max_tokens=self._settings.max_tokens,
                ollama_host=self._ollama_host,
                ollama_timeout=self._settings.ollama_chat_timeout,
                ollama_temp=self._settings.ollama_temp,
                ollama_top_p=self._settings.ollama_top_p,
            )
        else:
            raise DockerExecutorError(
                f"Provider '{self._provider}' cannot run in a container. "
                "Only 'anthropic' and 'ollama' are supported."
            )

    def _build_docker_cmd(self, task_dir: Path, output_dir: Path) -> list[str]:
        cmd = [
            "docker", "run", "--rm",
            f"--network={self._network}",
            "-v", f"{task_dir}:/task:ro",
            "-v", f"{output_dir}:/output",
        ]

        cmd.extend(self._limits.to_docker_args())

        if self._provider == "anthropic" and self._anthropic_api_key:
            cmd.extend(["-e", f"ANTHROPIC_API_KEY={self._anthropic_api_key}"])
        elif self._provider == "ollama":
            cmd.extend(["-e", f"OLLAMA_HOST={self._ollama_host}"])

        cmd.append(self._image)
        return cmd

    def _read_result(self, task: ParsedTask, output_dir: Path) -> TaskResult:
        result_path = output_dir / "result.json"
        if not result_path.is_file():
            return TaskResult(
                task=task,
                success=False,
                error_message="Container did not produce result.json",
            )
        try:
            data = json.loads(result_path.read_text(encoding="utf-8"))
            return TaskResult(
                task=task,
                success=data.get("success", False),
                agent_response=data.get("response", ""),
                error_message=data.get("error") or None,
            )
        except (json.JSONDecodeError, KeyError) as exc:
            return TaskResult(
                task=task,
                success=False,
                error_message=f"Failed to parse container result: {exc}",
            )

    def _read_result_or_error(
        self, task: ParsedTask, output_dir: Path, fallback_error: str
    ) -> TaskResult:
        """Try reading result.json; fall back to the provided error string."""
        result = self._read_result(task, output_dir)
        if result.error_message and "did not produce" in result.error_message:
            return TaskResult(
                task=task,
                success=False,
                error_message=fallback_error,
            )
        return result

    def build_image(self, context_dir: Path | None = None) -> subprocess.CompletedProcess:
        """Build the task runner image from Dockerfile.task-runner."""
        ctx = context_dir or Path.cwd()
        dockerfile = ctx / "Dockerfile.task-runner"
        if not dockerfile.is_file():
            raise DockerExecutorError(f"Dockerfile not found: {dockerfile}")

        cmd = [
            "docker", "build",
            "-f", str(dockerfile),
            "-t", self._image,
            str(ctx),
        ]
        logger.info(f"Building image {self._image} from {dockerfile}")
        return subprocess.run(cmd, check=True)

    def prune_containers(self) -> str:
        """Remove stopped task containers and dangling images."""
        result = subprocess.run(
            ["docker", "container", "prune", "-f",
             "--filter", "label=engineering-hub-task"],
            capture_output=True, text=True,
        )
        return result.stdout.strip()

    def status(self) -> dict:
        """Return info about running task containers and image availability."""
        info: dict = {
            "image": self._image,
            "network": self._network,
            "provider": self._provider,
            "max_concurrent": self._max_concurrent,
        }

        try:
            result = subprocess.run(
                ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}\t{{.Size}}",
                 "--filter", f"reference={self._image}"],
                capture_output=True, text=True, timeout=10,
            )
            info["image_available"] = bool(result.stdout.strip())
            info["image_size"] = result.stdout.strip().split("\t")[-1] if result.stdout.strip() else None
        except Exception:
            info["image_available"] = False

        try:
            result = subprocess.run(
                ["docker", "ps", "--format", "{{.Names}}\t{{.Status}}",
                 "--filter", "ancestor=" + self._image],
                capture_output=True, text=True, timeout=10,
            )
            running = [
                line.split("\t") for line in result.stdout.strip().splitlines() if line
            ]
            info["running_containers"] = [
                {"name": r[0], "status": r[1] if len(r) > 1 else "unknown"}
                for r in running
            ]
        except Exception:
            info["running_containers"] = []

        return info
