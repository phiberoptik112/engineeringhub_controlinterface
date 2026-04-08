"""Per-container resource limits for Docker task execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engineering_hub.config.settings import Settings


@dataclass
class ResourceLimits:
    """Docker resource constraints for a task container."""

    cpus: float = 2.0
    memory: str = "2g"
    timeout_seconds: int = 300

    @classmethod
    def from_settings(cls, settings: Settings) -> ResourceLimits:
        return cls(
            cpus=settings.docker_cpu_limit,
            memory=settings.docker_memory_limit,
            timeout_seconds=settings.docker_task_timeout,
        )

    def to_docker_args(self) -> list[str]:
        """Return ``docker run`` flags for these limits."""
        return [
            f"--cpus={self.cpus}",
            f"--memory={self.memory}",
            f"--stop-timeout={self.timeout_seconds}",
        ]
