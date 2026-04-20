"""Lightweight HTTP chat endpoint for the Journaler daemon.

Uses stdlib http.server to avoid adding framework dependencies.
Runs in a daemon thread so the main scheduler loop stays unblocked.

Endpoints:
    POST /chat     — {"message": "..."} → {"response": "..."}
    GET  /status   — {"model_loaded", "last_scan", "uptime", "history"}
    GET  /briefing — latest morning briefing as markdown text

Slash commands (parsed before reaching the LLM):
    /model [path] <...>
        Show active model, switch named profile, or load a HF id/path (daemon).
    /agent <type> <description> [--project <id>] [--backend mlx|claude]
        Delegate a task to a named agent and return the result inline.
        Default backend follows journaler.agent_backend (defaults to mlx).
    Falls back to writing to the journal if no delegator is configured.
    /skills
        List available agent delegation skills.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engineering_hub.journaler.context import JournalContext
    from engineering_hub.journaler.delegator import AgentDelegator
    from engineering_hub.journaler.engine import ConversationEngine
    from engineering_hub.journaler.model_profiles import JournalerChatModelContext

from engineering_hub.journaler.model_profiles import journaler_slash_model_command

logger = logging.getLogger(__name__)

# Matches a DISPATCH sentinel line the LLM emits to propose an agent execution.
# Must appear as its own line: "DISPATCH: /agent <type> <description> ..."
_DISPATCH_SENTINEL_RE = re.compile(
    r"^DISPATCH:\s*(/agent\s+\S+.+)$",
    re.IGNORECASE | re.MULTILINE,
)

# Regex to parse: /agent <type> <description> [--project <id>] [--backend <name>]
_AGENT_CMD_RE = re.compile(
    r"^/agent\s+(?P<agent_type>\S+)\s+(?P<description>.+?)$",
    re.IGNORECASE | re.DOTALL,
)
_PROJECT_FLAG_RE = re.compile(r"--project\s+(\S+)", re.IGNORECASE)
_BACKEND_FLAG_RE = re.compile(r"--backend\s+(mlx|claude)", re.IGNORECASE)


class ChatServer:
    """HTTP server for ad-hoc Journaler interaction."""

    def __init__(
        self,
        engine: ConversationEngine,
        context: JournalContext,
        host: str = "127.0.0.1",
        port: int = 18790,
        start_time: datetime | None = None,
        delegator: AgentDelegator | None = None,
        model_context: JournalerChatModelContext | None = None,
        pending_tasks_file: Path | None = None,
    ) -> None:
        self.engine = engine
        self.context = context
        self.host = host
        self.port = port
        self.start_time = start_time or datetime.now()
        self.delegator = delegator
        self.model_context = model_context
        self.pending_tasks_file = (
            pending_tasks_file.expanduser().resolve()
            if pending_tasks_file is not None
            else (context.workspace_dir / ".journaler" / "pending-tasks.org").resolve()
        )
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start_background(self) -> None:
        """Start the HTTP server in a daemon thread."""
        handler = _make_handler(
            self.engine,
            self.context,
            self.start_time,
            self.delegator,
            self.model_context,
            self.pending_tasks_file,
        )
        self._server = ThreadingHTTPServer((self.host, self.port), handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="journaler-chat",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Shut down the server gracefully."""
        if self._server:
            self._server.shutdown()
            logger.info("Chat server stopped")


def _make_handler(
    engine: ConversationEngine,
    context: JournalContext,
    start_time: datetime,
    delegator: AgentDelegator | None = None,
    model_context: JournalerChatModelContext | None = None,
    pending_tasks_file: Path | None = None,
) -> type[BaseHTTPRequestHandler]:
    """Create a request handler class with access to the engine and context."""

    resolved_pending = (
        pending_tasks_file.expanduser().resolve()
        if pending_tasks_file is not None
        else (context.workspace_dir / ".journaler" / "pending-tasks.org").resolve()
    )

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if self.path == "/chat":
                self._handle_chat()
            else:
                self._send_json({"error": "not found"}, 404)

        def do_GET(self) -> None:
            if self.path == "/status":
                self._handle_status()
            elif self.path == "/briefing":
                self._handle_briefing()
            elif self.path == "/skills":
                self._handle_skills()
            else:
                self._send_json({"error": "not found"}, 404)

        def do_OPTIONS(self) -> None:
            self.send_response(200)
            self._set_cors_headers()
            self.end_headers()

        def _handle_chat(self) -> None:
            try:
                body = self._read_body()
                message = body.get("message", "").strip()
                if not message:
                    self._send_json({"error": "message is required"}, 400)
                    return

                t0 = time.monotonic()

                # Route slash commands before they reach the LLM.
                mlow = message.lower()
                if mlow.startswith("/model"):
                    if model_context is None:
                        response = (
                            "Model context is not available on this server instance."
                        )
                    else:
                        response = journaler_slash_model_command(
                            message,
                            settings=model_context.settings,
                            model_ctx=model_context,
                            engine=engine,
                            delegator=delegator,
                        )
                elif mlow.startswith("/agent "):
                    response = _handle_agent_command(
                        message, delegator, context, engine=engine
                    )
                elif mlow == "/skills":
                    response = _handle_skills_command(delegator)
                elif mlow.startswith("/tasks") or mlow.startswith("/queue"):
                    from engineering_hub.journaler.task_slash import (
                        handle_tasks_slash_command,
                    )

                    response = handle_tasks_slash_command(
                        message, engine, resolved_pending
                    )
                else:
                    from datetime import datetime, timezone

                    from engineering_hub.journaler.task_intent_extractor import (
                        classify_journaler_intent,
                    )
                    from engineering_hub.journaler.task_planner_models import (
                        ProposedTask,
                    )

                    settings_obj = (
                        model_context.settings if model_context is not None else None
                    )
                    mode = (
                        (settings_obj.journaler_default_task_mode or "immediate")
                        if settings_obj is not None
                        else "immediate"
                    ).lower()

                    if mode != "propose" and delegator is not None:
                        kind, payload = classify_journaler_intent(engine, message)
                        desc = (payload.get("description") or "").strip()
                        if kind == "immediate_task" and desc:
                            agent = str(payload.get("agent_type") or "research")
                            cmd = f"/agent {agent} {desc}"
                            pid = payload.get("project_id")
                            if pid is not None:
                                cmd += f" --project {pid}"
                            agent_result = _handle_agent_command(
                                cmd, delegator, context, engine=engine
                            )
                            response = f"**@{agent}**\n\n{agent_result}"
                            engine.inject_turn(message, response)
                            elapsed = time.monotonic() - t0
                            self._send_json({
                                "response": response,
                                "agent_result": agent_result,
                                "dispatched": True,
                                "elapsed_seconds": round(elapsed, 2),
                            })
                            return
                        if kind == "queued_task" and desc:
                            agent = str(payload.get("agent_type") or "research")
                            now = datetime.now(timezone.utc)
                            kws = payload.get("keywords") or []
                            if isinstance(kws, str):
                                kws = [kws]
                            paths = payload.get("input_paths") or []
                            if not isinstance(paths, list):
                                paths = []
                            op = payload.get("output_path")
                            pt = ProposedTask(
                                agent_type=agent,
                                description=desc,
                                session_id=engine.session_id,
                                session_timestamp=engine.session_opened_at,
                                proposed_at=now,
                                keywords=[str(x) for x in kws][:8],
                                project_id=payload.get("project_id"),
                                input_paths=[str(x) for x in paths],
                                output_path=str(op) if op else None,
                                context_flags=[],
                                status="proposed",
                                confidence=float(payload.get("confidence", 0.0)),
                                clarification_needed=bool(
                                    payload.get("clarification_needed", False)
                                ),
                            )
                            engine.task_planner.add_proposal(pt)
                            response = (
                                f"Queued task proposal (not saved yet):\n\n"
                                f"- @{agent}: {desc}\n"
                            )
                            if pt.output_path:
                                response += f"- Suggested output: `{pt.output_path}`\n"
                            response += (
                                "\nUse `/tasks confirm` and `/tasks commit` for "
                                "pending-tasks.org."
                            )
                            engine.inject_turn(message, response)
                            elapsed = time.monotonic() - t0
                            self._send_json({
                                "response": response,
                                "elapsed_seconds": round(elapsed, 2),
                            })
                            return

                    raw_response = engine.chat(message)
                    response, dispatch_cmd = _extract_dispatch(raw_response)
                    if dispatch_cmd:
                        # Auto-execute the proposed agent dispatch: the HTTP
                        # client's user already confirmed intent in their message.
                        logger.info(
                            "Auto-executing DISPATCH sentinel from LLM: %s",
                            dispatch_cmd[:80],
                        )
                        agent_result = _handle_agent_command(
                            dispatch_cmd, delegator, context, engine=engine
                        )
                        engine.inject_turn(
                            user=dispatch_cmd, assistant=agent_result
                        )
                        elapsed = time.monotonic() - t0
                        self._send_json({
                            "response": response,
                            "agent_result": agent_result,
                            "dispatched": True,
                            "elapsed_seconds": round(elapsed, 2),
                        })
                        return

                elapsed = time.monotonic() - t0

                self._send_json({
                    "response": response,
                    "elapsed_seconds": round(elapsed, 2),
                })
            except Exception as exc:
                logger.error(f"Chat request failed: {exc}")
                self._send_json({"error": str(exc)}, 500)

        def _handle_status(self) -> None:
            uptime = datetime.now() - start_time
            hours, remainder = divmod(int(uptime.total_seconds()), 3600)
            minutes, seconds = divmod(remainder, 60)
            uptime_str = f"{hours}h {minutes}m {seconds}s"

            snapshot = context._snapshot
            self._send_json({
                "model_loaded": engine._backend.is_loaded(),
                "last_scan": snapshot.last_scan,
                "uptime": uptime_str,
                "pending_tasks": len(snapshot.pending_tasks),
                "completed_tasks": len(snapshot.completed_tasks),
                "history": engine.get_history_summary(),
            })

        def _handle_briefing(self) -> None:
            # Find and return the latest briefing file
            state_dir = context.state_dir
            briefing_dirs = [
                state_dir / "briefings",
                context.workspace_dir / ".journaler" / "briefings",
            ]
            latest: Path | None = None
            for bdir in briefing_dirs:
                if bdir.exists():
                    files = sorted(bdir.glob("*.md"), reverse=True)
                    if files:
                        latest = files[0]
                        break

            if latest:
                self.send_response(200)
                self.send_header("Content-Type", "text/markdown; charset=utf-8")
                self._set_cors_headers()
                self.end_headers()
                self.wfile.write(latest.read_bytes())
            else:
                self._send_json({"error": "no briefing available"}, 404)

        def _handle_skills(self) -> None:
            if delegator is None:
                self._send_json({"skills": [], "message": "No delegator configured."})
                return
            skills = [
                {
                    "name": s.name,
                    "display_name": s.display_name,
                    "agent_type": s.agent_type,
                    "description": s.description.splitlines()[0] if s.description else "",
                    "invocation_examples": s.invocation_examples[:2],
                }
                for s in delegator.list_skills()
            ]
            self._send_json({"skills": skills})

        def _read_body(self) -> dict:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            return json.loads(raw) if raw else {}

        def _send_json(self, data: dict, status: int = 200) -> None:
            body = json.dumps(data).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self._set_cors_headers()
            self.end_headers()
            self.wfile.write(body)

        def _set_cors_headers(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def log_message(self, format: str, *args: object) -> None:
            logger.debug(f"ChatServer: {format % args}")

    return Handler


# ---------------------------------------------------------------------------
# Slash command handlers (module-level for clarity)
# ---------------------------------------------------------------------------


def _extract_dispatch(response: str) -> tuple[str, str | None]:
    """Scan an LLM response for an embedded DISPATCH sentinel.

    The LLM emits a line of the form::

        DISPATCH: /agent technical-writer <description> [--project <id>]

    when it wants to propose an agent execution for user confirmation.

    Returns:
        A ``(clean_response, dispatch_cmd)`` tuple.  ``clean_response`` has the
        sentinel line stripped and trailing whitespace trimmed.  ``dispatch_cmd``
        is the ``/agent …`` portion, or ``None`` when no sentinel was found.
    """
    m = _DISPATCH_SENTINEL_RE.search(response)
    if not m:
        return response, None

    dispatch_cmd = m.group(1).strip()
    clean = _DISPATCH_SENTINEL_RE.sub("", response).rstrip()
    return clean, dispatch_cmd


def _handle_agent_command(
    message: str,
    delegator: AgentDelegator | None,
    context: JournalContext,
    engine: ConversationEngine | None = None,
) -> str:
    """Parse and execute a /agent command.

    Syntax: /agent <type> <description> [--project <id>] [--backend mlx|claude]
    """
    m = _AGENT_CMD_RE.match(message)
    if not m:
        return (
            "Usage: `/agent <type> <description> [--project <id>] [--backend mlx|claude]`\n\n"
            "Types: research, technical-writer, standards-checker, technical-reviewer, "
            "weekly-reviewer"
        )

    agent_type = m.group("agent_type").lower()
    raw_description = m.group("description").strip()

    # Extract and strip flags from the description
    project_id: int | str | None = None
    pm = _PROJECT_FLAG_RE.search(raw_description)
    if pm:
        raw_id = pm.group(1)
        try:
            project_id = int(raw_id)
        except ValueError:
            project_id = raw_id
        raw_description = _PROJECT_FLAG_RE.sub("", raw_description).strip()

    backend = "auto"
    bm = _BACKEND_FLAG_RE.search(raw_description)
    if bm:
        backend = bm.group(1).lower()
        raw_description = _BACKEND_FLAG_RE.sub("", raw_description).strip()

    description = raw_description.strip(" -")

    if not description:
        return "Please provide a task description after the agent type."

    if delegator is None:
        # Fall back to journal write when no delegator is configured
        journal_dir = context.journal_dir
        from engineering_hub.journaler.org_writer import add_todo_to_journal

        item = f"@{agent_type}: {description}"
        if project_id is not None:
            item += f" [[django://project/{project_id}]]"
        ok, msg_text = add_todo_to_journal(journal_dir, item)
        if ok:
            return (
                f"No live agent backend configured — task queued for overnight dispatch:\n"
                f"`- [ ] {item}`\n\n"
                f"The Orchestrator will pick it up on the next scan."
            )
        return f"Could not queue task: {msg_text}"

    journaler_context = ""
    if engine is not None:
        journaler_context = engine.build_delegate_context(description)

    return delegator.delegate(
        agent_type=agent_type,
        description=description,
        project_id=project_id,
        backend=backend,
        journaler_context=journaler_context,
    )


def _handle_skills_command(delegator: AgentDelegator | None) -> str:
    """Return a formatted list of available delegation skills."""
    if delegator is None:
        return (
            "Agent delegation is not configured. "
            "Add `anthropic.api_key` or ensure the MLX model is loaded, "
            "then set `journaler.agent_backend` in your config."
        )
    return delegator.skills_summary()
