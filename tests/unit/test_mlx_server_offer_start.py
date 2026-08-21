"""Unit tests for mlx_lm.server launch / offer helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from engineering_hub.agents.mlx_server import (
    offer_start_mlx_server_if_needed,
    parse_mlx_server_base_url,
    resolve_mlx_lm_server_command,
    wait_for_mlx_server,
)
from engineering_hub.config.settings import Settings


def test_parse_mlx_server_base_url() -> None:
    assert parse_mlx_server_base_url("http://127.0.0.1:8081/v1") == ("127.0.0.1", 8081)
    assert parse_mlx_server_base_url("http://localhost:9000") == ("localhost", 9000)
    assert parse_mlx_server_base_url("https://example.com/v1") == ("example.com", 443)


def test_offer_skips_when_disabled() -> None:
    settings = Settings(mlx_server_enabled=False)
    assert (
        offer_start_mlx_server_if_needed(settings, "model", isatty=True) is False
    )


@patch("engineering_hub.agents.mlx_server.probe_mlx_server", return_value=True)
def test_offer_returns_true_when_already_up(mock_probe: MagicMock) -> None:
    settings = Settings(mlx_server_enabled=True)
    assert offer_start_mlx_server_if_needed(settings, "model", isatty=True) is True
    mock_probe.assert_called()


@patch("engineering_hub.agents.mlx_server.probe_mlx_server", return_value=False)
def test_offer_skips_when_not_tty(mock_probe: MagicMock) -> None:
    settings = Settings(mlx_server_enabled=True, mlx_server_offer_start=True)
    assert (
        offer_start_mlx_server_if_needed(settings, "model", isatty=False) is False
    )


@patch("engineering_hub.agents.mlx_server.probe_mlx_server", return_value=False)
def test_offer_skips_when_offer_start_false(mock_probe: MagicMock) -> None:
    settings = Settings(mlx_server_enabled=True, mlx_server_offer_start=False)
    assert offer_start_mlx_server_if_needed(settings, "model", isatty=True) is False


@patch("engineering_hub.agents.mlx_server.wait_for_mlx_server", return_value=True)
@patch("engineering_hub.agents.mlx_server.start_mlx_lm_server")
@patch("engineering_hub.agents.mlx_server.probe_mlx_server", return_value=False)
def test_offer_yes_starts_server(
    mock_probe: MagicMock,
    mock_start: MagicMock,
    mock_wait: MagicMock,
    tmp_path: Path,
) -> None:
    settings = Settings(mlx_server_enabled=True, mlx_server_offer_start=True)
    mock_start.return_value = MagicMock(pid=12345)

    ready = offer_start_mlx_server_if_needed(
        settings,
        "mlx-community/test",
        isatty=True,
        state_dir=tmp_path,
        prompt_fn=lambda _msg: "y",
    )
    assert ready is True
    mock_start.assert_called_once()
    mock_wait.assert_called_once()
    kwargs = mock_start.call_args.kwargs
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 8081


@patch("engineering_hub.agents.mlx_server.start_mlx_lm_server")
@patch("engineering_hub.agents.mlx_server.probe_mlx_server", return_value=False)
def test_offer_no_does_not_start(
    mock_probe: MagicMock,
    mock_start: MagicMock,
) -> None:
    settings = Settings(mlx_server_enabled=True)
    ready = offer_start_mlx_server_if_needed(
        settings,
        "mlx-community/test",
        isatty=True,
        prompt_fn=lambda _msg: "n",
    )
    assert ready is False
    mock_start.assert_not_called()


@patch("engineering_hub.agents.mlx_server.probe_mlx_server", side_effect=[False, True])
@patch("engineering_hub.agents.mlx_server.time.sleep")
def test_wait_for_mlx_server_polls(mock_sleep: MagicMock, mock_probe: MagicMock) -> None:
    assert wait_for_mlx_server("http://127.0.0.1:8081/v1", timeout_s=5.0) is True
    assert mock_probe.call_count == 2


def test_resolve_mlx_lm_server_command_prefers_path() -> None:
    with patch(
        "engineering_hub.agents.mlx_server.shutil.which",
        return_value="/opt/bin/mlx_lm.server",
    ):
        assert resolve_mlx_lm_server_command() == ["/opt/bin/mlx_lm.server"]
