"""Rich live monitor for the Journaler daemon."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from rich.console import Console, Group
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from engineering_hub.journaler.status_snapshot import collect_status_snapshot


def run_monitor(
    *,
    state_dir: Path,
    chat_host: str,
    chat_port: int,
    model_path: str,
    chat_enabled: bool,
    refresh_seconds: float,
    heartbeat_stale_after_sec: int,
    once: bool = False,
    use_http: bool = True,
    console: Console | None = None,
) -> int:
    """Run the live Journaler monitor until interrupted."""
    out = console or Console()

    def _snapshot() -> dict[str, Any]:
        return collect_status_snapshot(
            state_dir,
            chat_host=chat_host,
            chat_port=chat_port,
            model_path=model_path,
            chat_enabled=chat_enabled,
            heartbeat_stale_after_sec=heartbeat_stale_after_sec,
            use_http=use_http,
        )

    if once:
        out.print(render_status_snapshot(_snapshot()))
        return 0

    try:
        with Live(
            render_status_snapshot(_snapshot()),
            console=out,
            refresh_per_second=max(1, int(1 / max(refresh_seconds, 0.2))),
            screen=False,
        ) as live:
            while True:
                time.sleep(refresh_seconds)
                live.update(render_status_snapshot(_snapshot()))
    except KeyboardInterrupt:
        out.print("\n[dim]Journaler monitor stopped.[/dim]")
    return 0


def render_status_snapshot(snapshot: dict[str, Any]) -> Group:
    """Render a Journaler status snapshot as Rich displayables."""
    status = "online" if snapshot.get("daemon_online") else "offline"
    status_style = "bold green" if status == "online" else "bold red"

    header = Text()
    header.append("Journaler daemon ", style="bold")
    header.append(status.upper(), style=status_style)
    if snapshot.get("last_event"):
        header.append(f" | last event: {snapshot['last_event']}", style="dim")

    health = Table.grid(expand=True)
    health.add_column(ratio=1)
    health.add_column(ratio=2)
    health.add_row("PID", _value(snapshot.get("pid")))
    health.add_row("Started", _value(snapshot.get("started_at")))
    health.add_row("Uptime", _value(snapshot.get("uptime")))
    health.add_row("Heartbeat", _heartbeat_text(snapshot))
    health.add_row("HTTP", _http_text(snapshot))
    health.add_row("State dir", escape(str(snapshot.get("state_dir") or "")))

    model = Table.grid(expand=True)
    model.add_column(ratio=1)
    model.add_column(ratio=2)
    model.add_row("Model", escape(str(snapshot.get("model_path") or "unknown")))
    model.add_row("Loaded", _bool_text(snapshot.get("model_loaded")))
    model.add_row("Chat", escape(str(snapshot.get("chat_url") or "disabled")))

    scan = Table.grid(expand=True)
    scan.add_column(ratio=1)
    scan.add_column(ratio=2)
    scan.add_row("Last scan", _value(snapshot.get("last_scan")))
    scan.add_row("Tracked files", _value(snapshot.get("tracked_files")))
    scan.add_row("Pending tasks", _value(snapshot.get("pending_tasks")))
    scan.add_row("Completed tasks", _value(snapshot.get("completed_tasks")))
    scan.add_row("Stale tasks", _value(snapshot.get("stale_tasks")))

    engine = snapshot.get("engine") if isinstance(snapshot.get("engine"), dict) else {}
    pressure = Table.grid(expand=True)
    pressure.add_column(ratio=1)
    pressure.add_column(ratio=2)
    pressure.add_row("Pressure", _value(engine.get("pressure")))
    pressure.add_row("Utilization", _value(engine.get("utilization")))
    pressure.add_row("History turns", _value(engine.get("history_turns")))
    pressure.add_row("History tokens", _value(engine.get("history_tokens")))
    pressure.add_row("Available tokens", _value(engine.get("available_tokens")))
    pressure.add_row("Topic", _value(engine.get("current_topic")))

    suggestions = Table.grid(expand=True)
    suggestions.add_column()
    for suggestion in snapshot.get("suggestions", []):
        suggestions.add_row(f"- {escape(str(suggestion))}")

    return Group(
        Panel(header, title="Journaler Monitor", border_style=status_style),
        Panel(health, title="Daemon", border_style="cyan"),
        Panel(model, title="Model / Chat", border_style="blue"),
        Panel(scan, title="Workspace Scan", border_style="magenta"),
        Panel(pressure, title="Context Pressure", border_style="yellow"),
        Panel(suggestions, title="Suggestions", border_style="green"),
    )


def _value(value: Any) -> str:
    if value is None or value == "":
        return "[dim]unknown[/dim]"
    return escape(str(value))


def _bool_text(value: Any) -> str:
    if value is True:
        return "[green]yes[/green]"
    if value is False:
        return "[red]no[/red]"
    return "[dim]unknown[/dim]"


def _heartbeat_text(snapshot: dict[str, Any]) -> str:
    last = snapshot.get("last_heartbeat") or "unknown"
    age = snapshot.get("heartbeat_age_seconds")
    if isinstance(age, (int, float)):
        text = f"{last} ({int(age)}s ago)"
    else:
        text = str(last)
    if snapshot.get("heartbeat_fresh"):
        return f"[green]{escape(text)}[/green]"
    return f"[red]{escape(text)}[/red]"


def _http_text(snapshot: dict[str, Any]) -> str:
    if snapshot.get("http_online"):
        return "[green]reachable[/green]"
    if snapshot.get("http_error"):
        return f"[red]{escape(str(snapshot['http_error']))}[/red]"
    if not snapshot.get("chat_enabled"):
        return "[dim]disabled[/dim]"
    return "[red]unreachable[/red]"
