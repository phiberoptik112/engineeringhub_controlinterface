"""Configuration loading utilities."""

import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


def load_yaml_config(config_path: Path) -> dict:
    """Load YAML configuration file.

    Args:
        config_path: Path to the YAML config file

    Returns:
        Configuration dictionary
    """
    if not config_path.exists():
        logger.warning(f"Config file not found: {config_path}")
        return {}

    try:
        with open(config_path) as f:
            config = yaml.safe_load(f)
            return config or {}
    except yaml.YAMLError as e:
        logger.error(f"Failed to parse config file: {e}")
        return {}


def find_config_file() -> Path | None:
    """Find the configuration file.

    Searches in order:
    1. ENGINEERING_HUB_CONFIG environment variable
    2. ./config.yaml
    3. ~/.config/engineering-hub/config.yaml
    4. ~/org-roam/engineering-hub/config.yaml

    Returns:
        Path to config file if found, None otherwise
    """
    import os

    # Check environment variable
    env_config = os.environ.get("ENGINEERING_HUB_CONFIG")
    if env_config:
        path = Path(env_config).expanduser()
        if path.exists():
            return path

    # Check common locations
    locations = [
        Path.cwd() / "config.yaml",
        Path.home() / ".config" / "engineering-hub" / "config.yaml",
        Path.home() / "org-roam" / "engineering-hub" / "config.yaml",
    ]

    for location in locations:
        if location.exists():
            logger.debug(f"Found config at: {location}")
            return location

    return None
