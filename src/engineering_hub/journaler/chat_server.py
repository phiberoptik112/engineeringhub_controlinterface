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
    /pipeline draft-section --section "<section>" [--project <id>] [--backend mlx|claude] [--loop-limit <n>]
        Run the multi-stage report drafting pipeline:
        DataGatherer → technical-writer → standards-checker (loop) → technical-reviewer → latex-writer.
        All numeric data must be pre-computed; the pipeline drafts prose only.
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

from engineering_hub.journaler.chat_router import route_natural_language_task
from engineering_hub.journaler.model_profiles import journaler_slash_model_command
from engineering_hub.journaler.org_writer import add_todo_to_journal

if TYPE_CHECKING:
    from engineering_hub.journaler.context import JournalContext
    from engineering_hub.journaler.delegator import AgentDelegator
    from engineering_hub.journaler.engine import ConversationEngine
    from engineering_hub.journaler.model_profiles import JournalerChatModelContext

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
_WEB_FLAG_RE = re.compile(r"--web\b", re.IGNORECASE)
_NO_WEB_FLAG_RE = re.compile(r"--no-web\b", re.IGNORECASE)
_DISPATCH_CONFIRM_RE = re.compile(
    r"\b(yes|yep|please|go ahead|run it|dispatch|do it|execute|start)\b",
    re.IGNORECASE,
)

# Regex to parse: /pipeline draft-section [flags]
_PIPELINE_CMD_RE = re.compile(r"^/pipeline\s+draft-section\b", re.IGNORECASE)
_SECTION_FLAG_RE = re.compile(r'--section\s+"([^"]+)"|--section\s+(\S+)', re.IGNORECASE)
_LOOP_LIMIT_FLAG_RE = re.compile(r"--loop-limit\s+(\d+)", re.IGNORECASE)


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
                elif mlow.startswith("/pipeline "):
                    response = _handle_pipeline_command(
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
                    settings_obj = (
                        model_context.settings if model_context is not None else None
                    )
                    mode = (
                        (settings_obj.journaler_default_task_mode or "immediate")
                        if settings_obj is not None
                        else "immediate"
                    ).lower()

                    routed = route_natural_language_task(
                        message,
                        engine=engine,
                        delegator=delegator,
                        mode=mode,
                        pending_tasks_file=resolved_pending,
                        run_agent_command=lambda cmd: _handle_agent_command(
                            cmd, delegator, context, engine=engine
                        ),
                    )
                    if routed is not None:
                        elapsed = time.monotonic() - t0
                        body = {
                            "response": routed.response,
                            "elapsed_seconds": round(elapsed, 2),
                        }
                        if routed.agent_result is not None:
                            body["agent_result"] = routed.agent_result
                        if routed.dispatched:
                            body["dispatched"] = True
                        self._send_json(body)
                        return

                    raw_response = engine.chat(message)
                    response, dispatch_cmd = _extract_dispatch(raw_response)
                    if dispatch_cmd:
                        elapsed = time.monotonic() - t0
                        if _message_confirms_dispatch(message):
                            logger.info(
                                "Executing confirmed DISPATCH sentinel from LLM: %s",
                                dispatch_cmd[:80],
                            )
                            agent_result = _handle_agent_command(
                                dispatch_cmd, delegator, context, engine=engine
                            )
                            engine.inject_turn(
                                user=dispatch_cmd, assistant=agent_result
                            )
                            self._send_json({
                                "response": response,
                                "agent_result": agent_result,
                                "dispatched": True,
                                "elapsed_seconds": round(elapsed, 2),
                            })
                            return
                        self._send_json({
                            "response": response,
                            "proposed_dispatch": dispatch_cmd,
                            "dispatched": False,
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


def _message_confirms_dispatch(message: str) -> bool:
    """Return True when the latest user message explicitly confirms execution."""
    return bool(_DISPATCH_CONFIRM_RE.search(message))


def _handle_agent_command(
    message: str,
    delegator: AgentDelegator | None,
    context: JournalContext,
    engine: ConversationEngine | None = None,
) -> str:
    """Parse and execute a /agent command.

    Syntax: /agent <type> <description> [--project <id>] [--backend mlx|claude] [--web|--no-web]
    """
    m = _AGENT_CMD_RE.match(message)
    if not m:
        return (
            "Usage: `/agent <type> <description> [--project <id>] "
            "[--backend mlx|claude] [--web|--no-web]`\n\n"
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

    web_search_enabled: bool | None = None
    web_search_required = False
    if _NO_WEB_FLAG_RE.search(raw_description):
        web_search_enabled = False
        raw_description = _NO_WEB_FLAG_RE.sub("", raw_description).strip()
    if _WEB_FLAG_RE.search(raw_description):
        web_search_enabled = True
        web_search_required = True
        raw_description = _WEB_FLAG_RE.sub("", raw_description).strip()

    description = raw_description.strip(" -")

    if not description:
        return "Please provide a task description after the agent type."

    if delegator is None:
        # Fall back to journal write when no delegator is configured
        journal_dir = context.journal_dir

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
    anthropic_web_search = False
    if engine is not None:
        context_result = engine.build_delegate_context_result(
            description,
            web_search_enabled=web_search_enabled,
            web_search_required=web_search_required,
        )
        journaler_context = context_result.context
        if (
            context_result.web_search_attempted
            and not context_result.web_search_succeeded
            and engine.web_search_anthropic_backup_enabled
            and delegator.will_use_anthropic_backend(backend)
        ):
            anthropic_web_search = True
        elif (
            web_search_required
            and context_result.web_search_attempted
            and not context_result.web_search_succeeded
        ):
            return (
                "Web search was requested with `--web`, but local SearXNG retrieval "
                f"failed: {context_result.web_search_error or 'unknown error'}"
            )
    elif web_search_required:
        return "Web search was requested with `--web`, but no conversation engine is available."

    return delegator.delegate(
        agent_type=agent_type,
        description=description,
        project_id=project_id,
        backend=backend,
        journaler_context=journaler_context,
        anthropic_web_search=anthropic_web_search,
        anthropic_web_search_tool_version=(
            engine.web_search_anthropic_tool_version
            if engine is not None
            else "web_search_20250305"
        ),
        anthropic_web_search_max_uses=(
            engine.web_search_anthropic_max_uses if engine is not None else 3
        ),
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


def _handle_pipeline_command(
    message: str,
    delegator: AgentDelegator | None,
    context: JournalContext,
    engine: ConversationEngine | None = None,
) -> str:
    """Parse and execute a /pipeline draft-section command.

    Syntax::

        /pipeline draft-section --section "<section>" [--project <id>]
                                 [--backend mlx|claude] [--loop-limit <n>]

    Gathers pre-processed result files from the project staging directory,
    then runs the multi-stage drafting pipeline:
    technical-writer → standards-checker (loop) → technical-reviewer → latex-writer.

    Falls back to queueing a journal TODO when no delegator is configured.
    """
    if not _PIPELINE_CMD_RE.match(message):
        return (
            "Usage: `/pipeline draft-section --section \"<section>\" "
            "[--project <id>] [--backend mlx|claude] [--loop-limit <n>]`\n\n"
            "Example: `/pipeline draft-section --section \"6.0 Noise Impacts\" --project 42`"
        )

    # --- Extract --section flag ---
    sm = _SECTION_FLAG_RE.search(message)
    section = (sm.group(1) or sm.group(2) or "").strip() if sm else ""
    if not section:
        return (
            "Please supply a section name via `--section \"<section>\"`.\n\n"
            "Example: `/pipeline draft-section --section \"6.0 Noise Impacts\" --project 42`"
        )

    # --- Extract optional flags ---
    project_id: int | str | None = None
    pm = _PROJECT_FLAG_RE.search(message)
    if pm:
        raw_id = pm.group(1)
        try:
            project_id = int(raw_id)
        except ValueError:
            project_id = raw_id

    backend = "auto"
    bm = _BACKEND_FLAG_RE.search(message)
    if bm:
        backend = bm.group(1).lower()

    loop_limit: int | None = None
    lm = _LOOP_LIMIT_FLAG_RE.search(message)
    if lm:
        loop_limit = int(lm.group(1))

    # --- No delegator: queue to journal ---
    if delegator is None:
        journal_dir = context.journal_dir
        from engineering_hub.journaler.org_writer import add_todo_to_journal

        item = f"@pipeline: draft-section '{section}'"
        if project_id is not None:
            item += f" [[django://project/{project_id}]]"
        ok, msg_text = add_todo_to_journal(journal_dir, item)
        if ok:
            return (
                f"No live agent backend configured — pipeline task queued for overnight dispatch:\n"
                f"`- [ ] {item}`\n\n"
                f"The Orchestrator will pick it up on the next scan."
            )
        return f"Could not queue pipeline task: {msg_text}"

    # --- Run the pipeline ---
    from engineering_hub.context.data_gatherer import DataGatherer
    from engineering_hub.orchestration.pipeline import AgentPipeline

    output_dir = context.workspace_dir / "outputs"
    gatherer = DataGatherer(output_dir=output_dir)

    pid_for_gather = project_id if project_id is not None else "unknown"
    bundle = gatherer.gather(project_id=pid_for_gather, section_hint=section)

    if bundle.is_empty:
        logger.warning(
            "Pipeline: no data files found for project %s — proceeding with empty bundle.",
            pid_for_gather,
        )

    pipeline = AgentPipeline(output_dir=output_dir / "pipeline")

    try:
        result = pipeline.run(
            section=section,
            data_bundle=bundle,
            delegator=delegator,
            project_id=project_id,
            backend=backend,
            loop_limit=loop_limit,
        )
    except Exception as exc:
        logger.error("Pipeline execution error: %s", exc)
        return f"Pipeline failed with an unexpected error: {exc}"

    summary = result.format_summary()
    if engine is not None:
        engine.inject_turn(message, summary)
    return summary
