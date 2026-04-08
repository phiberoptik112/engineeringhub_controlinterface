"""Serialization layer for passing agent tasks into Docker containers.

A TaskPayload bundles everything a containerized task runner needs:
the parsed task, pre-built context, system prompt, and backend config.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from engineering_hub.core.models import ParsedTask


@dataclass
class BackendConfig:
    """Minimal backend configuration carried into the container."""

    provider: str  # "anthropic" or "ollama"
    model: str = ""
    max_tokens: int = 4096

    # Ollama-specific
    ollama_host: str = "http://ollama:11434"
    ollama_timeout: int = 120
    ollama_temp: float = 0.7
    ollama_top_p: float = 0.9

    # Anthropic-specific
    anthropic_model: str = "claude-sonnet-4-5-20250929"


@dataclass
class TaskPayload:
    """Self-contained execution unit for a containerised agent task."""

    task: dict[str, Any]
    context: str
    system_prompt: str
    backend: BackendConfig
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        parsed_task: ParsedTask,
        context: str,
        system_prompt: str,
        backend: BackendConfig,
        metadata: dict[str, Any] | None = None,
    ) -> TaskPayload:
        return cls(
            task=parsed_task.model_dump(mode="json"),
            context=context,
            system_prompt=system_prompt,
            backend=backend,
            metadata=metadata or {},
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, raw: str) -> TaskPayload:
        data = json.loads(raw)
        data["backend"] = BackendConfig(**data["backend"])
        return cls(**data)

    def write(self, path: Path) -> None:
        path.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def read(cls, path: Path) -> TaskPayload:
        return cls.from_json(path.read_text(encoding="utf-8"))

    def reconstruct_task(self) -> ParsedTask:
        return ParsedTask.model_validate(self.task)
