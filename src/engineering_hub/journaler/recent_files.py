"""Workspace-wide "recently created files" scanner for ``/load_recent``.

Walks a set of roots (org-roam tree, agent outputs, journaler state, inputs,
conversation exports, zettelkasten proposals) and ranks files by creation
time (``st_birthtime`` when the filesystem provides it, falling back to
``st_mtime``). Shared by the interactive REPL and the Textual TUI so the
scan/rank logic lives in one place.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

# Directory names that never hold user-reviewable content worth surfacing.
_SKIP_DIR_NAMES: frozenset[str] = frozenset(
    {".git", "__pycache__", "node_modules", ".venv", ".mypy_cache", ".pytest_cache"}
)


@dataclass
class RecentFile:
    """A single recently created/modified file discovered during a scan."""

    path: Path
    created_ts: float
    modified_ts: float
    root_label: str


def _file_timestamps(stat_result: os.stat_result) -> tuple[float, float]:
    """Return ``(created_ts, modified_ts)`` using birthtime when available."""
    modified = float(stat_result.st_mtime)
    created = float(getattr(stat_result, "st_birthtime", stat_result.st_ctime))
    return created, modified


def collect_recent_files(
    roots: list[tuple[str, Path]],
    extensions: frozenset[str],
    limit: int = 5,
    days: int | None = 30,
) -> list[RecentFile]:
    """Scan *roots* and return up to *limit* files sorted by creation time desc.

    Args:
        roots: ``(label, path)`` pairs to walk. Missing paths are skipped.
        extensions: Lowercase suffixes (incl. dot) to include.
        limit: Max number of files to return. ``<= 0`` returns ``[]``.
        days: Only include files created within the last N days. ``None`` or
            ``<= 0`` disables the cutoff.
    """
    if limit <= 0:
        return []

    cutoff_ts: float | None = None
    if days is not None and days > 0:
        cutoff_ts = time.time() - days * 86400.0

    best: dict[Path, RecentFile] = {}
    for label, root in roots:
        if root is None:
            continue
        try:
            root_resolved = root.expanduser().resolve()
        except OSError:
            continue
        if not root_resolved.is_dir():
            continue

        stack: list[Path] = [root_resolved]
        while stack:
            current = stack.pop()
            try:
                iterator = os.scandir(current)
            except OSError:
                continue
            with iterator:
                for child in iterator:
                    try:
                        if child.is_dir(follow_symlinks=False):
                            if child.name not in _SKIP_DIR_NAMES and not (
                                child.name.startswith(".") and child.name != "."
                            ):
                                stack.append(Path(child.path))
                            continue
                        if not child.is_file(follow_symlinks=False):
                            continue
                    except OSError:
                        continue

                    child_path = Path(child.path)
                    if child_path.suffix.lower() not in extensions:
                        continue
                    try:
                        stat_result = child.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    created, modified = _file_timestamps(stat_result)
                    if cutoff_ts is not None and created < cutoff_ts:
                        continue

                    resolved = child_path.resolve()
                    existing = best.get(resolved)
                    if existing is None or created > existing.created_ts:
                        best[resolved] = RecentFile(
                            path=child_path,
                            created_ts=created,
                            modified_ts=modified,
                            root_label=label,
                        )

    ranked = sorted(best.values(), key=lambda rf: rf.created_ts, reverse=True)
    return ranked[:limit]


def default_recent_roots(
    config: object | None = None,
    settings: object | None = None,
) -> list[tuple[str, Path]]:
    """Build the default ``(label, path)`` scan roots from config/settings.

    Accepts a ``JournalerConfig`` and/or ``Settings`` object (either may be
    ``None``); reads attributes defensively so partial wiring still works.
    """
    roots: list[tuple[str, Path]] = []
    seen: set[Path] = set()

    def _add(label: str, value: object) -> None:
        if not isinstance(value, Path):
            return
        try:
            key = value.expanduser()
        except (OSError, RuntimeError):
            return
        if key in seen:
            return
        seen.add(key)
        roots.append((label, value))

    org_roam_dir = getattr(config, "org_roam_dir", None)
    workspace_dir = getattr(config, "workspace_dir", None)
    state_dir = getattr(config, "state_dir", None)

    # Optional override list from settings: list of paths.
    override = getattr(settings, "journaler_load_recent_roots", None)
    if override:
        for entry in override:
            if isinstance(entry, (str, Path)):
                _add("custom", Path(entry))
        if roots:
            return roots

    _add("org-roam", org_roam_dir)
    if isinstance(workspace_dir, Path):
        _add("outputs", workspace_dir / "outputs")
    _add("state", state_dir)

    inputs_dir = getattr(settings, "resolved_inputs_dir", None)
    _add("inputs", inputs_dir)

    if isinstance(org_roam_dir, Path):
        _add("exports", org_roam_dir / "conversation_exports")

    zettel_dir = getattr(settings, "zettelkasten_resolved_proposal_dir", None)
    _add("zettel", zettel_dir)

    return roots
