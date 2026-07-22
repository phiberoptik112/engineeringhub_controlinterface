"""Slash command handlers for /convo multi-conversation support."""

from __future__ import annotations

import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from engineering_hub.journaler.conversation_store import Conversation, ConversationStore
    from engineering_hub.journaler.engine import ConversationEngine


def _relative_time(iso_ts: str) -> str:
    try:
        ts = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except ValueError:
        return iso_ts
    delta = datetime.now(timezone.utc) - ts
    secs = int(delta.total_seconds())
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    if secs < 172800:
        return "yesterday"
    return f"{secs // 86400}d ago"


def _resolve_project_title(
    project_id: int | None,
    resolve_project: Callable[[int], str] | None,
) -> str | None:
    if project_id is None:
        return None
    if resolve_project is not None:
        try:
            return resolve_project(project_id)
        except Exception:
            pass
    return f"Project {project_id}"


def format_convo_list(
    conversations: list[Conversation],
    *,
    resolve_project: Callable[[int], str] | None = None,
    active_id: str | None = None,
) -> str:
    """Text listing grouped by project then topic."""
    if not conversations:
        return "No conversations yet. Use /convo new <title> to create one."

    by_project: dict[str, list[Conversation]] = {}
    by_topic: dict[str, list[Conversation]] = {}
    ungrouped: list[Conversation] = []

    for conv in conversations:
        if conv.project_id is not None:
            key = _resolve_project_title(conv.project_id, resolve_project) or (
                f"Project {conv.project_id}"
            )
            by_project.setdefault(key, []).append(conv)
        elif conv.topic:
            by_topic.setdefault(conv.topic, []).append(conv)
        else:
            ungrouped.append(conv)

    lines: list[str] = ["Conversations:"]
    for group_label, convs in sorted(by_project.items()):
        lines.append(f"\n▸ {group_label}")
        for c in convs:
            marker = " *" if c.id == active_id else ""
            lines.append(
                f"  {c.id}: {c.title}  ({c.turn_count} turns, {_relative_time(c.updated_at)}){marker}"
            )
    for topic, convs in sorted(by_topic.items()):
        lines.append(f"\n▸ Topic: {topic}")
        for c in convs:
            marker = " *" if c.id == active_id else ""
            lines.append(
                f"  {c.id}: {c.title}  ({c.turn_count} turns, {_relative_time(c.updated_at)}){marker}"
            )
    if ungrouped:
        lines.append("\n▸ Ungrouped")
        for c in ungrouped:
            marker = " *" if c.id == active_id else ""
            lines.append(
                f"  {c.id}: {c.title}  ({c.turn_count} turns, {_relative_time(c.updated_at)}){marker}"
            )
    lines.append("\n(* = active)")
    return "\n".join(lines)


def parse_convo_new_args(parts: list[str]) -> tuple[int | None, str | None, str | None]:
    """Parse /convo new [--project N] [--topic LABEL] [title...].

    Returns (project_id, topic, title).
    """
    project_id: int | None = None
    topic: str | None = None
    title_parts: list[str] = []
    i = 1
    while i < len(parts):
        p = parts[i].lower()
        if p == "--project" and i + 1 < len(parts):
            try:
                project_id = int(parts[i + 1])
            except ValueError:
                project_id = None
            i += 2
            continue
        if p == "--topic" and i + 1 < len(parts):
            topic = parts[i + 1]
            i += 2
            continue
        title_parts.append(parts[i])
        i += 1
    title = " ".join(title_parts).strip() or None
    return project_id, topic, title


def handle_convo_command(
    raw: str,
    engine: ConversationEngine,
    *,
    resolve_project: Callable[[int], str] | None = None,
    default_project: int | None = None,
    interactive_picker: Callable[[], str | None] | None = None,
    confirm_delete: Callable[[str], bool] | None = None,
) -> str | None:
    """Handle /convo subcommands. Returns response text or None if picker needed."""
    store: ConversationStore | None = engine.conversation_store
    if store is None:
        return "Multi-conversation mode is disabled (journaler.conversations.enabled: false)."

    parts = shlex.split(raw)
    if len(parts) == 1:
        if interactive_picker is not None:
            return interactive_picker()
        return "Use /convo in interactive journaler chat for the picker, or /convo list."

    sub = parts[1].lower()

    if sub == "new":
        project_id, topic, title = parse_convo_new_args(parts)
        if project_id is None:
            project_id = default_project
        if not title:
            return "Usage: /convo new [--project N] [--topic LABEL] <title>"
        conv = store.create(title, project_id=project_id, topic=topic)
        return engine.switch_session(conv)

    if sub == "list":
        convs = store.list()
        active = engine.active_conversation
        return format_convo_list(
            convs,
            resolve_project=resolve_project,
            active_id=active.id if active else store.get_active_id(),
        )

    if sub == "status":
        conv = engine.active_conversation
        if conv is None:
            return "No active conversation."
        loaded = len(engine.list_loaded_files())
        remembered = len(conv.file_manifest)
        proj = _resolve_project_title(conv.project_id, resolve_project)
        lines = [
            f"Active: {conv.title} ({conv.id})",
            f"Turns in memory: {len(engine.history.turns)}",
            f"Turns logged: {conv.turn_count}",
            f"Loaded files: {loaded}",
            f"Remembered files: {remembered}",
        ]
        if proj:
            lines.append(f"Project: {proj}")
        if conv.topic:
            lines.append(f"Topic: {conv.topic}")
        return "\n".join(lines)

    if sub == "rename":
        if len(parts) < 3:
            return "Usage: /convo rename <new title>"
        conv = engine.active_conversation
        if conv is None:
            return "No active conversation to rename."
        new_title = " ".join(parts[2:])
        store.update_meta(conv.id, title=new_title)
        conv.title = new_title
        return f"Renamed to '{new_title}'."

    if sub == "restore-files":
        loaded, msg = engine.restore_session_files()
        if loaded == 0 and "No remembered" in msg:
            return msg
        return msg

    if sub == "archive":
        target_id = parts[2] if len(parts) > 2 else None
        if target_id is None:
            conv = engine.active_conversation
            if conv is None:
                return "No active conversation to archive."
            target_id = conv.id
        if not store.archive(target_id):
            return f"Conversation not found: {target_id}"
        if engine.active_conversation and engine.active_conversation.id == target_id:
            engine.active_conversation = None
            engine.pressure_manager.named_conversation_active = False
            remaining = store.list()
            if remaining:
                return (
                    f"Archived '{target_id}'. "
                    + engine.switch_session(remaining[0])
                )
            new_conv = store.create("Default")
            return f"Archived '{target_id}'. " + engine.switch_session(new_conv)
        return f"Archived conversation '{target_id}'."

    if sub == "delete":
        if len(parts) < 3:
            return "Usage: /convo delete <id> [--confirm]"
        target_id = parts[2]
        needs_confirm = "--confirm" not in [p.lower() for p in parts[3:]]
        if needs_confirm and confirm_delete is not None:
            if not confirm_delete(target_id):
                return "Delete cancelled."
        elif needs_confirm:
            return f"Usage: /convo delete {target_id} --confirm"
        if not store.delete(target_id):
            return f"Conversation not found: {target_id}"
        if engine.active_conversation and engine.active_conversation.id == target_id:
            engine.active_conversation = None
            engine.pressure_manager.named_conversation_active = False
        return f"Deleted conversation '{target_id}'."

    # Jump by id or title fragment
    query = " ".join(parts[1:])
    conv = store.get(query)
    if conv is None:
        hits = store.find_by_title_fragment(query)
        if len(hits) == 1:
            conv = hits[0]
        elif len(hits) > 1:
            names = ", ".join(f"{h.id} ({h.title})" for h in hits[:5])
            return f"Multiple matches: {names}. Use /convo <id>."
        else:
            return f"No conversation matching '{query}'."
    if conv.archived:
        return f"Conversation '{conv.id}' is archived."
    return engine.switch_session(conv)
