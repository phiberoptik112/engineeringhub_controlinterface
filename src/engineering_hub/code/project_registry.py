"""Registry of local code repositories the code-engineer agent may operate on.

A "code project" is a local git checkout on disk, keyed by a short name that is
used as the task ``project_id`` (e.g. ``/agent code-engineer <task> --project myrepo``).
The registry is built from ``Settings.code_projects``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


class CodeProjectError(Exception):
    """Raised when a code project cannot be resolved or is invalid."""


@dataclass(frozen=True)
class CodeProject:
    """A registered local git repository the code-engineer can operate on."""

    name: str
    path: Path
    default_branch: str = "main"
    test_cmd: str | None = None
    provider: str | None = None  # per-repo override of pi.provider
    model: str | None = None  # per-repo override of pi.model
    tools: str | None = None  # per-repo override of pi.default_tools

    def validate(self) -> None:
        """Raise :class:`CodeProjectError` if the path is not a git repo on disk."""
        if not self.path.exists():
            raise CodeProjectError(
                f"Code project '{self.name}': path does not exist: {self.path}"
            )
        if not self.path.is_dir():
            raise CodeProjectError(
                f"Code project '{self.name}': path is not a directory: {self.path}"
            )
        if not (self.path / ".git").exists():
            raise CodeProjectError(
                f"Code project '{self.name}': not a git repository (no .git): {self.path}"
            )


class CodeProjectRegistry:
    """Name -> :class:`CodeProject` lookup built from ``Settings.code_projects``."""

    def __init__(self, raw: dict[str, dict[str, Any]] | None) -> None:
        self._projects: dict[str, CodeProject] = {}
        for name, cfg in (raw or {}).items():
            cfg = cfg or {}
            path_str = cfg.get("path")
            if not path_str:
                # Skip malformed entries rather than crashing registry construction.
                continue
            self._projects[name] = CodeProject(
                name=name,
                path=Path(str(path_str)).expanduser(),
                default_branch=cfg.get("default_branch", "main"),
                test_cmd=cfg.get("test_cmd"),
                provider=cfg.get("provider"),
                model=cfg.get("model"),
                tools=cfg.get("tools"),
            )

    def __len__(self) -> int:
        return len(self._projects)

    def names(self) -> list[str]:
        """Return the sorted list of registered project names."""
        return sorted(self._projects)

    def resolve(self, project_id: int | str | None) -> CodeProject:
        """Resolve a project id to a validated :class:`CodeProject`.

        Raises:
            CodeProjectError: if the id is unknown or the repo is invalid.
        """
        key = str(project_id) if project_id is not None else ""
        project = self._projects.get(key)
        if project is None:
            available = ", ".join(self.names()) or "(none configured)"
            raise CodeProjectError(
                f"Unknown code project '{project_id}'. Registered: {available}"
            )
        project.validate()
        return project
