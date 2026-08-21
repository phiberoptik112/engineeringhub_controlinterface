"""Unit tests for Blender Lab MCP TCP client."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from engineering_hub.blender.lab_client import LabMCPError, execute


def test_execute_sends_null_delimited_json_and_parses_response() -> None:
    response = {"status": "ok", "result": {"n": 1}}
    mock_sock = MagicMock()
    mock_sock.recv.side_effect = [
        (json.dumps(response) + "\0").encode("utf-8"),
    ]

    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_sock
    mock_cm.__exit__.return_value = None

    with patch(
        "engineering_hub.blender.lab_client.socket.create_connection",
        return_value=mock_cm,
    ) as mock_connect:
        result = execute(
            "result = {'n': 1}",
            host="127.0.0.1",
            port=9876,
            timeout_s=5.0,
            strict_json=True,
        )

    mock_connect.assert_called_once_with(("127.0.0.1", 9876), timeout=5.0)
    sent = mock_sock.sendall.call_args[0][0]
    assert sent.endswith(b"\0")
    request = json.loads(sent[:-1].decode("utf-8"))
    assert request["type"] == "execute"
    assert request["strict_json"] is True
    assert "result = {'n': 1}" in request["code"]
    assert result == response


def test_execute_connection_error_raises_lab_mcp_error() -> None:
    with patch(
        "engineering_hub.blender.lab_client.socket.create_connection",
        side_effect=ConnectionRefusedError("refused"),
    ):
        with pytest.raises(LabMCPError, match="connection failed"):
            execute("result = {}", host="127.0.0.1", port=9876)


def test_execute_incomplete_response_raises() -> None:
    mock_sock = MagicMock()
    mock_sock.recv.side_effect = [b'{"status":"ok"', b""]

    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_sock
    mock_cm.__exit__.return_value = None

    with patch(
        "engineering_hub.blender.lab_client.socket.create_connection",
        return_value=mock_cm,
    ):
        with pytest.raises(LabMCPError, match="before completing"):
            execute("result = {}")
