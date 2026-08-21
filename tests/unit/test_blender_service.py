"""Unit tests for Blender Lab MCP service (mocked — no Blender required)."""

from __future__ import annotations

from unittest.mock import patch

from engineering_hub.blender import service as blender_service
from engineering_hub.blender.lab_client import LabMCPError
from engineering_hub.config.settings import Settings


def _settings(**overrides: object) -> Settings:
    base = {
        "blender_enabled": True,
        "blender_host": "127.0.0.1",
        "blender_port": 9876,
    }
    base.update(overrides)
    return Settings(**base)


def test_resolve_blender_endpoint_from_settings() -> None:
    settings = _settings(blender_host="localhost", blender_port=9999)
    assert blender_service.resolve_blender_endpoint(settings) == "localhost:9999"


@patch("engineering_hub.blender.service.lab_execute")
def test_health_check_connected(mock_lab_execute: object) -> None:
    mock_lab_execute.return_value = {
        "status": "ok",
        "result": {"ok": True, "version": "5.1.0"},
    }

    result = blender_service.health_check(settings=_settings())
    assert result["connected"] is True
    assert result["endpoint"] == "127.0.0.1:9876"
    assert result["blender_version"] == "5.1.0"
    assert "blender_execute" in result["sample_tools"]
    mock_lab_execute.assert_called_once()


@patch("engineering_hub.blender.service.lab_execute")
def test_health_check_offline(mock_lab_execute: object) -> None:
    mock_lab_execute.side_effect = LabMCPError(
        "Lab MCP connection failed (127.0.0.1:9876): refused"
    )

    result = blender_service.health_check(settings=_settings())
    assert result["connected"] is False
    assert "refused" in result["error"]


def test_list_tools_returns_local_catalog() -> None:
    payload = blender_service.list_tools(settings=_settings())
    assert payload["status"] == "ok"
    assert payload["backend"] == "lab-mcp"
    names = [t["name"] for t in payload["tools"]]
    assert names == ["blender_health", "blender_list_tools", "blender_execute"]


@patch("engineering_hub.blender.service.lab_execute")
def test_execute_success(mock_lab_execute: object) -> None:
    mock_lab_execute.return_value = {
        "status": "ok",
        "result": {"objects": ["Cube", "Camera"]},
    }

    payload = blender_service.execute(
        "import bpy\nresult = {'objects': [o.name for o in bpy.data.objects]}",
        settings=_settings(),
    )
    assert payload["success"] is True
    assert payload["result"]["objects"] == ["Cube", "Camera"]
    mock_lab_execute.assert_called_once()
    call_kwargs = mock_lab_execute.call_args
    assert call_kwargs.kwargs["strict_json"] is True
    assert call_kwargs.kwargs["port"] == 9876


@patch("engineering_hub.blender.service.lab_execute")
def test_execute_lab_error_status(mock_lab_execute: object) -> None:
    mock_lab_execute.return_value = {
        "status": "error",
        "message": "Traceback: NameError",
    }

    payload = blender_service.execute("bad()", settings=_settings())
    assert payload["success"] is False
    assert "NameError" in payload["error"]


def test_execute_empty_code() -> None:
    payload = blender_service.execute("  ", settings=_settings())
    assert payload["success"] is False
    assert "non-empty" in payload["error"]


def test_health_check_disabled() -> None:
    result = blender_service.health_check(settings=_settings(blender_enabled=False))
    assert result["enabled"] is False
    assert result["connected"] is False


def test_format_status_offline_hint() -> None:
    with patch(
        "engineering_hub.blender.service.lab_execute",
        side_effect=LabMCPError("connection refused"),
    ):
        report = blender_service.format_status_report(settings=_settings())
    assert "lab-mcp" in report
    assert "9876" in report
    assert "Lab MCP" in report
