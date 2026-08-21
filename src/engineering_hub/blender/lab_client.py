"""Blender Lab MCP TCP client — null-byte-delimited JSON execute bridge.

Talks to the official Blender Lab MCP add-on (default ``127.0.0.1:9876``).
Protocol: send ``{"type":"execute","code":"...","strict_json":true}\\0``,
read until ``\\0``, parse JSON response.
"""

from __future__ import annotations

import json
import socket
from typing import Any

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9876
_RECV_CHUNK = 4096
_MAX_RESPONSE_BYTES = 10 * 1024 * 1024


class LabMCPError(Exception):
    """Raised when the Lab MCP TCP bridge fails or returns an error."""


def execute(
    code: str,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    timeout_s: float = 10.0,
    strict_json: bool = True,
) -> dict[str, Any]:
    """Execute Python inside Blender via the Lab MCP TCP bridge.

    The code should assign a dict to ``result`` when ``strict_json`` is True.
    Returns the parsed JSON response from the add-on.
    """
    request = {
        "type": "execute",
        "code": code,
        "strict_json": strict_json,
    }
    payload = (json.dumps(request) + "\0").encode("utf-8")

    try:
        with socket.create_connection((host, port), timeout=timeout_s) as sock:
            sock.settimeout(timeout_s)
            sock.sendall(payload)
            response_bytes = _recv_until_null(sock)
    except OSError as exc:
        raise LabMCPError(f"Lab MCP connection failed ({host}:{port}): {exc}") from exc

    try:
        response = json.loads(response_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LabMCPError(f"Invalid JSON from Lab MCP: {exc}") from exc

    if not isinstance(response, dict):
        raise LabMCPError(f"Lab MCP response must be an object, got {type(response).__name__}")
    return response


def _recv_until_null(sock: socket.socket) -> bytes:
    buffer = bytearray()
    while True:
        chunk = sock.recv(_RECV_CHUNK)
        if not chunk:
            break
        buffer.extend(chunk)
        if len(buffer) > _MAX_RESPONSE_BYTES:
            raise LabMCPError("Lab MCP response exceeded size limit")
        null_at = buffer.find(b"\0")
        if null_at >= 0:
            return bytes(buffer[:null_at])
    raise LabMCPError("Lab MCP closed connection before completing response")
