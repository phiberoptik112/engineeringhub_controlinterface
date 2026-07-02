"""Tests for Journaler monitor and activity-log config loading."""

from __future__ import annotations

from pathlib import Path

from engineering_hub.config.settings import Settings


def test_journaler_monitor_and_activity_log_config_loads(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    log_path = tmp_path / "roam" / "journaler-activity.org"
    config_path.write_text(
        """
journaler:
  monitor_refresh_sec: 1.5
  status_heartbeat_stale_sec: 45
  activity_log:
    enabled: true
    mode: dedicated_file
    path: "{log_path}"
    heading: "Daemon Events"
    include_suggestions: false
""".format(log_path=log_path),
        encoding="utf-8",
    )

    settings = Settings.from_yaml(config_path)

    assert settings.journaler_monitor_refresh_sec == 1.5
    assert settings.journaler_status_heartbeat_stale_sec == 45
    assert settings.journaler_activity_log_enabled is True
    assert settings.journaler_activity_log_mode == "dedicated_file"
    assert settings.journaler_activity_log_path == log_path
    assert settings.journaler_activity_log_heading == "Daemon Events"
    assert settings.journaler_activity_log_include_suggestions is False
