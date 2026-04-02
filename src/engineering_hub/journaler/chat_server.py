"""Lightweight HTTP chat endpoint for the Journaler daemon.

Uses stdlib http.server to avoid adding framework dependencies.
Runs in a daemon thread so the main scheduler loop stays unblocked.

Endpoints:
    POST /chat     — {"message": "..."} → {"response": "..."}
    GET  /status   — {"model_loaded", "last_scan", "uptime", "history"}
    GET  /briefing — latest morning briefing as markdown text
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engineering_hub.journaler.context import JournalContext
    from engineering_hub.journaler.engine import ConversationEngine

logger = logging.getLogger(__name__)


class ChatServer:
    """HTTP server for ad-hoc Journaler interaction."""

    def __init__(
        self,
        engine: ConversationEngine,
        context: JournalContext,
        host: str = "127.0.0.1",
        port: int = 18790,
        start_time: datetime | None = None,
    ) -> None:
        self.engine = engine
        self.context = context
        self.host = host
        self.port = port
        self.start_time = start_time or datetime.now()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start_background(self) -> None:
        """Start the HTTP server in a daemon thread."""
        handler = _make_handler(self.engine, self.context, self.start_time)
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
) -> type[BaseHTTPRequestHandler]:
    """Create a request handler class with access to the engine and context."""

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
                response = engine.chat(message)
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
