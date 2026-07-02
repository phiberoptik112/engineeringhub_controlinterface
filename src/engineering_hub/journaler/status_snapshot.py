"""Shared Journaler status snapshots and suggestion rules."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

DAEMON_STATUS_FILENAME = "daemon_status.json"


def daemon_status_path(state_dir: Path) -> Path:
    """Return the daemon heartbeat/status sidecar path."""
    return state_dir.expanduser().resolve() / DAEMON_STATUS_FILENAME


def write_status_file(state_dir: Path, payload: dict[str, Any]) -> None:
    """Atomically write the daemon heartbeat/status sidecar."""
    state_dir = state_dir.expanduser().resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    data = dict(payload)
    data.setdefault("last_heartbeat", datetime.now().isoformat(timespec="seconds"))
    path = daemon_status_path(state_dir)
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)


def collect_status_snapshot(
    state_dir: Path,
    *,
    chat_host: str,
    chat_port: int,
    model_path: str = "",
    chat_enabled: bool = True,
    heartbeat_stale_after_sec: int = 30,
    http_timeout_sec: float = 0.4,
    use_http: bool = True,
) -> dict[str, Any]:
    """Collect a merged live/file-backed Journaler status snapshot.

    The daemon's HTTP endpoint is authoritative when available.  The
    ``daemon_status.json`` heartbeat and scanner cache keep status useful when
    HTTP chat is disabled or the daemon is not reachable.
    """
    resolved_state_dir = state_dir.expanduser().resolve()
    scan_state = _read_json(resolved_state_dir / "state.json")
    context_cache = _read_json(resolved_state_dir / "context_cache.json")
    heartbeat = _read_json(daemon_status_path(resolved_state_dir))
    http_status: dict[str, Any] = {}
    http_error = ""

    if use_http and chat_enabled:
        http_status, http_error = _fetch_http_status(
            chat_host,
            chat_port,
            timeout_sec=http_timeout_sec,
        )

    heartbeat_age = _seconds_since(heartbeat.get("last_heartbeat"))
    heartbeat_fresh = (
        heartbeat_age is not None and heartbeat_age <= heartbeat_stale_after_sec
    )
    http_online = bool(http_status)
    daemon_online = http_online or heartbeat_fresh

    engine_status = _merged_dict(
        heartbeat.get("engine"),
        http_status.get("engine"),
    )

    tracked_files = _first_int(
        heartbeat.get("tracked_files"),
        len(scan_state.get("file_mtimes", {})) if scan_state else None,
    )
    pending_tasks = _first_int(
        http_status.get("pending_tasks"),
        heartbeat.get("pending_tasks"),
        len(context_cache.get("pending_tasks", [])) if context_cache else None,
    )
    completed_tasks = _first_int(
        http_status.get("completed_tasks"),
        heartbeat.get("completed_tasks"),
        len(context_cache.get("completed_tasks", [])) if context_cache else None,
    )
    stale_tasks = _first_int(
        http_status.get("stale_tasks"),
        heartbeat.get("stale_tasks"),
        len(context_cache.get("stale_tasks", [])) if context_cache else None,
    )

    snapshot: dict[str, Any] = {
        "state_dir": str(resolved_state_dir),
        "daemon_online": daemon_online,
        "status": "online" if daemon_online else "offline",
        "http_online": http_online,
        "http_error": http_error,
        "heartbeat_fresh": heartbeat_fresh,
        "heartbeat_age_seconds": heartbeat_age,
        "heartbeat_stale_after_seconds": heartbeat_stale_after_sec,
        "pid": heartbeat.get("pid"),
        "started_at": heartbeat.get("started_at", ""),
        "last_heartbeat": heartbeat.get("last_heartbeat", ""),
        "last_event": heartbeat.get("last_event", ""),
        "uptime": http_status.get("uptime") or heartbeat.get("uptime", ""),
        "model_path": (
            http_status.get("model_path")
            or heartbeat.get("model_path")
            or model_path
        ),
        "model_loaded": _first_value(
            http_status.get("model_loaded"),
            heartbeat.get("model_loaded"),
        ),
        "chat_enabled": chat_enabled,
        "chat_url": (
            heartbeat.get("chat_url")
            or f"http://{chat_host}:{chat_port}"
        ),
        "last_scan": _first_value(
            http_status.get("last_scan"),
            heartbeat.get("last_scan"),
            context_cache.get("last_scan"),
            scan_state.get("last_scan"),
            "",
        ),
        "scan_interval_min": _first_int(heartbeat.get("scan_interval_min")),
        "tracked_files": tracked_files,
        "pending_tasks": pending_tasks,
        "completed_tasks": completed_tasks,
        "stale_tasks": stale_tasks,
        "history": http_status.get("history") or heartbeat.get("history", ""),
        "engine": engine_status,
        "context_cache_present": bool(context_cache),
        "scan_state_present": bool(scan_state),
    }
    snapshot["suggestions"] = build_status_suggestions(snapshot)
    return snapshot


def build_status_suggestions(snapshot: dict[str, Any]) -> list[str]:
    """Return cheap deterministic operational suggestions for a status view."""
    suggestions: list[str] = []

    if not snapshot.get("daemon_online"):
        suggestions.append(
            "Daemon appears offline; start it with `engineering-hub journaler start`."
        )
    elif not snapshot.get("http_online") and snapshot.get("chat_enabled"):
        suggestions.append(
            "HTTP chat endpoint is not reachable; check the daemon log or chat port."
        )

    model_loaded = snapshot.get("model_loaded")
    if model_loaded is False:
        suggestions.append("Model is not loaded yet; wait for startup or inspect daemon logs.")

    last_scan = str(snapshot.get("last_scan") or "")
    if not last_scan:
        suggestions.append("No org-roam scan has completed yet; run `journaler scan` or wait.")
    else:
        scan_age = _seconds_since(last_scan)
        interval = _first_int(snapshot.get("scan_interval_min")) or 10
        stale_after = max(interval * 120, 1800)
        if scan_age is not None and scan_age > stale_after:
            suggestions.append(
                "Last scan is stale; verify the daemon is still scanning the workspace."
            )

    utilization = _utilization_percent(snapshot.get("engine", {}).get("utilization"))
    if utilization is not None:
        if utilization >= 85:
            suggestions.append(
                "Context utilization is high; consider `/clear --summarize` soon."
            )
        elif utilization >= 70:
            suggestions.append(
                "Context is filling up; review `/budget` before loading more files."
            )

    stale_tasks = _first_int(snapshot.get("stale_tasks")) or 0
    if stale_tasks > 0:
        suggestions.append(f"Review {stale_tasks} stale task(s) in the recent journal window.")

    pending_tasks = _first_int(snapshot.get("pending_tasks")) or 0
    if pending_tasks >= 10:
        suggestions.append("Pending queue is large; triage `/tasks` before adding more work.")
    elif pending_tasks > 0:
        suggestions.append("Pending tasks are available; use `/tasks` to review the queue.")

    if not suggestions:
        suggestions.append("No immediate action suggested; daemon status looks healthy.")
    return suggestions


def _fetch_http_status(
    host: str,
    port: int,
    *,
    timeout_sec: float,
) -> tuple[dict[str, Any], str]:
    url = f"http://{host}:{port}/status"
    try:
        with urlopen(url, timeout=timeout_sec) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        return {}, f"HTTP {exc.code}"
    except (OSError, URLError) as exc:
        return {}, str(exc)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {}, f"invalid JSON: {exc}"
    if not isinstance(data, dict):
        return {}, "status response was not an object"
    return data, ""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _merged_dict(*values: Any) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for value in values:
        if isinstance(value, dict):
            merged.update(value)
    return merged


def _first_value(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _first_int(*values: Any) -> int | None:
    value = _first_value(*values)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _seconds_since(raw: Any) -> float | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return max(0.0, (datetime.now() - dt.replace(tzinfo=None)).total_seconds())


def _utilization_percent(raw: Any) -> int | None:
    if isinstance(raw, str):
        text = raw.strip().rstrip("%")
        try:
            return int(float(text))
        except ValueError:
            return None
    if isinstance(raw, (int, float)):
        value = float(raw)
        if 0 <= value <= 1:
            value *= 100
        return int(value)
    return None
