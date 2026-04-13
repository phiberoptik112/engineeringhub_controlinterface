"""Load and save hub capture templates from/to YAML files."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from engineering_hub.capture.models import CaptureTemplate

logger = logging.getLogger(__name__)


def load_capture_templates(templates_dir: Path) -> dict[str, CaptureTemplate]:
    """Load all ``*.yaml`` files from *templates_dir* into a name-keyed dict."""
    templates: dict[str, CaptureTemplate] = {}
    if not templates_dir.exists():
        logger.warning("Capture templates directory not found: %s", templates_dir)
        return templates

    for path in sorted(templates_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            tpl = CaptureTemplate.from_dict(data)
            if not tpl.name:
                tpl.name = path.stem
            tpl.source = "yaml"
            templates[tpl.name] = tpl
            logger.debug("Loaded capture template: %s (%s)", tpl.name, tpl.display_name)
        except Exception as exc:
            logger.warning("Failed to load capture template from %s: %s", path.name, exc)

    logger.info("Loaded %d capture template(s) from %s", len(templates), templates_dir)
    return templates


def save_capture_template(template: CaptureTemplate, templates_dir: Path) -> Path:
    """Write a single capture template to a YAML file.

    Returns the path of the written file.
    """
    templates_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{template.name}.yaml"
    path = templates_dir / filename

    data = template.to_dict()
    # Remove internal-only fields from YAML output
    data.pop("source", None)

    path.write_text(
        yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    logger.info("Saved capture template to %s", path)
    return path


def _default_capture_templates_dir() -> Path:
    """Walk up from this file to find the repo root's ``capture_templates/`` directory."""
    here = Path(__file__).resolve()
    for parent in [here.parent, here.parent.parent, here.parent.parent.parent,
                   here.parent.parent.parent.parent]:
        candidate = parent / "capture_templates"
        if candidate.is_dir():
            return candidate
    return Path("capture_templates")
