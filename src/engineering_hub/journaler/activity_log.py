"""Org-mode activity logging for the Journaler daemon."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from engineering_hub.journaler.org_writer import (
    _create_journal_file,
    _org_timestamp,
    _today_journal_path,
    append_to_heading,
)

logger = logging.getLogger(__name__)


@dataclass
class ActivityLogConfig:
    """Configuration for the Doom Emacs/org-readable activity stream."""

    enabled: bool = False
    mode: str = "daily_journal"
    path: Path | None = None
    heading: str = "Journaler Activity"
    include_suggestions: bool = True


class JournalerActivityLog:
    """Append daemon activity events to a daily journal or dedicated org file."""

    def __init__(
        self,
        config: ActivityLogConfig,
        *,
        org_roam_dir: Path,
        journal_dir: Path,
    ) -> None:
        self.config = config
        self.org_roam_dir = org_roam_dir.expanduser().resolve()
        self.journal_dir = journal_dir.expanduser().resolve()

    def append_event(
        self,
        event: str,
        title: str,
        *,
        details: dict[str, Any] | None = None,
        suggestions: list[str] | None = None,
        properties: dict[str, Any] | None = None,
        level: str = "info",
    ) -> None:
        """Append one org-mode event entry when activity logging is enabled."""
        if not self.config.enabled:
            return

        try:
            target = self._resolve_target_path()
            body = self._format_event(
                event,
                title,
                details=details,
                suggestions=suggestions,
                properties=properties,
                level=level,
            )
            ok, message = append_to_heading(
                target,
                self.config.heading,
                body,
                create_heading_if_missing=True,
            )
            if not ok:
                logger.warning("Journaler activity log append failed: %s", message)
        except OSError as exc:
            logger.warning("Journaler activity log append failed: %s", exc)

    def _resolve_target_path(self) -> Path:
        mode = (self.config.mode or "daily_journal").strip().lower()
        if mode == "dedicated_file":
            target = self.config.path or (self.org_roam_dir / "journaler-activity.org")
            target = target.expanduser().resolve()
            self._ensure_dedicated_file(target)
            return target

        target = _today_journal_path(self.journal_dir)
        _create_journal_file(target)
        return target.expanduser().resolve()

    def _ensure_dedicated_file(self, path: Path) -> None:
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        title = path.stem.replace("-", " ").title()
        path.write_text(
            ":PROPERTIES:\n"
            f":ID:       journaler-activity-{datetime.now().strftime('%Y%m%d%H%M%S')}\n"
            ":END:\n"
            f"#+title: {title}\n"
            "#+filetags: :journaler:activity:\n"
            f"#+created: {_org_timestamp()}\n\n"
            f"* {self.config.heading}\n",
            encoding="utf-8",
        )

    def _format_event(
        self,
        event: str,
        title: str,
        *,
        details: dict[str, Any] | None,
        suggestions: list[str] | None,
        properties: dict[str, Any] | None,
        level: str,
    ) -> str:
        event_tag = _org_tag(event)
        level_tag = _org_tag(level)
        lines = [
            f"** {_org_timestamp()} {title.strip()} :journaler:{level_tag}:{event_tag}:",
            ":PROPERTIES:",
            f":EVENT: {event}",
            f":LEVEL: {level}",
        ]
        for key, value in sorted((properties or {}).items()):
            prop_key = _org_property_key(key)
            lines.append(f":{prop_key}: {_stringify(value)}")
        lines.append(":END:")

        for key, value in sorted((details or {}).items()):
            lines.append(f"- {key}: {_stringify(value)}")

        if self.config.include_suggestions and suggestions:
            lines.append("")
            lines.append("*** Suggestions")
            for suggestion in suggestions:
                lines.append(f"- [ ] {suggestion}")

        return "\n".join(lines)


def _org_tag(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_@#%]", "_", value.strip().lower())
    return cleaned.strip("_") or "event"


def _org_property_key(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", value.strip().upper())
    return cleaned.strip("_") or "PROPERTY"


def _stringify(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value)
    if isinstance(value, dict):
        return ", ".join(f"{key}={val}" for key, val in sorted(value.items()))
    return str(value)
