"""Command executor bridge for the TUI — executes slash commands without Rich console dependency."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engineering_hub.journaler.engine import ConversationEngine

SUPPORTED_EXTENSIONS = frozenset({
    ".md", ".txt", ".org", ".py", ".yaml", ".yml",
    ".json", ".tex", ".csv", ".toml", ".rst", ".docx", ".pdf",
})


class JournalerChatExit(Exception):
    """Raised when /exit or /quit is invoked."""


class CommandExecutor:
    """Execute slash commands and return plain-text results.

    This separates command logic from presentation so both the readline CLI
    (via Rich console) and the Textual TUI can share the same dispatch.
    Commands that require interactive stdin (like /agent_browse, /capture_browse)
    return guidance text instead.
    """

    def __init__(
        self,
        engine: ConversationEngine,
        delegator: object | None = None,
        config: object | None = None,
        journal_ctx: object | None = None,
        settings: object | None = None,
        load_tracker: object | None = None,
        model_ctx: object | None = None,
    ) -> None:
        self.engine = engine
        self.delegator = delegator
        self.config = config
        self.journal_ctx = journal_ctx
        self.settings = settings
        self.load_tracker = load_tracker
        self.model_ctx = model_ctx
        self._task_integrator: object | None = None

    def _get_journal_dir(self) -> Path | None:
        if self.config and hasattr(self.config, "journal_dir"):
            return Path(self.config.journal_dir)  # type: ignore[arg-type]
        return None

    def _get_org_roam_dir(self) -> Path | None:
        if self.config and hasattr(self.config, "org_roam_dir"):
            return Path(self.config.org_roam_dir)  # type: ignore[arg-type]
        return None

    def execute(self, raw: str) -> str:
        """Execute a slash command string and return the result as plain text.

        Raises JournalerChatExit for /exit and /quit.
        Returns a user-facing message string for all other commands.
        """
        parts = raw.split()
        cmd = parts[0].lower()

        if cmd in ("/exit", "/quit"):
            raise JournalerChatExit()

        if cmd == "/status":
            return self._cmd_status()
        if cmd == "/budget":
            return self._cmd_budget()
        if cmd == "/topic":
            return self._cmd_topic()
        if cmd == "/files":
            return self._cmd_files(parts)
        if cmd == "/clear":
            return self._cmd_clear(parts)
        if cmd == "/load":
            return self._cmd_load(parts)
        if cmd == "/load_recent":
            return self._cmd_load_recent(parts)
        if cmd == "/find":
            return self._cmd_find(parts)
        if cmd == "/skills":
            return self._cmd_skills()
        if cmd == "/blender":
            return self._cmd_blender(parts)
        if cmd == "/horn":
            return self._cmd_horn(raw)
        if cmd == "/integrate":
            return self._cmd_integrate(parts)
        if cmd == "/task":
            return self._cmd_task(parts, raw)
        if cmd == "/done":
            return self._cmd_done(parts)
        if cmd == "/note":
            return self._cmd_note(raw)
        if cmd == "/open":
            return self._cmd_open(parts, raw)
        if cmd == "/edit":
            return self._cmd_edit(raw)
        if cmd == "/timesheet":
            return self._cmd_timesheet(raw)
        if cmd == "/agent":
            return self._cmd_agent(raw)
        if cmd == "/history":
            return self._cmd_history(raw)
        if cmd == "/pipeline":
            return self._cmd_pipeline(raw)
        if cmd == "/tasks" or cmd == "/queue":
            return self._cmd_tasks(raw)
        if cmd == "/context":
            return self._cmd_context(parts)
        if cmd == "/help":
            return self._cmd_help()
        if cmd == "/capture_list":
            return self._cmd_capture_list()
        if cmd == "/model":
            return self._cmd_model(raw)

        # Commands requiring interactive input in original CLI
        if cmd in ("/load_browse", "/edit_browse", "/agent_browse", "/capture_browse", "/model_browse"):
            return (
                f"{cmd} is an interactive browser. "
                f"Use the sidebar or command palette to access this functionality."
            )

        if cmd == "/convo" and len(parts) == 1:
            return (
                "/convo opens the conversation browser. "
                "Use the sidebar or command palette, or /convo list."
            )

        if cmd == "/convo" or raw.lower().startswith("/convo "):
            from engineering_hub.journaler.convo_slash import handle_convo_command

            return handle_convo_command(raw, self.engine) or "Unknown /convo command."

        return f"Unknown command: {cmd}. Press Ctrl+P for the command palette."

    def _cmd_status(self) -> str:
        status = self.engine.get_status()
        lines = ["Context Status:"]
        for key, val in status.items():
            lines.append(f"  {key.replace('_', ' ').title()}: {val}")
        return "\n".join(lines)

    def _cmd_budget(self) -> str:
        self.engine.budget.history_tokens = self.engine.history.total_tokens
        self.engine._sync_loaded_files_budget()
        b = self.engine.budget
        lines = [
            "Token Budget:",
            f"  Context window:         {b.window_size:,}",
            f"  System prompt:          {b.system_prompt_tokens:,}",
            f"  Context snapshot:       {b.context_snapshot_tokens:,}",
            f"  Loaded files:           {b.loaded_files_tokens:,}",
            f"  Corpus injection:       {b.corpus_injection_tokens:,}",
            f"  Conversation history:   {b.history_tokens:,}",
            f"  Reserved for generation:{b.reserved_for_generation:,}",
            f"  {'─' * 30}",
            f"  Used:        {b.used:,}",
            f"  Available:   {b.available:,}",
            f"  Utilization: {b.utilization:.0%}",
        ]
        return "\n".join(lines)

    def _cmd_topic(self) -> str:
        topic = self.engine.topic_tracker.current_topic
        if topic:
            return f"Current topic: {topic}"
        return "No topic detected yet."

    def _cmd_files(self, parts: list[str]) -> str:
        if len(parts) >= 2 and parts[1].lower() == "clear":
            self.engine.clear_loaded_files()
            return "Loaded files cleared."
        entries = self.engine.list_loaded_files()
        if not entries:
            return "No files loaded."
        lines = [f"Loaded files ({len(entries)}):"]
        for label, char_count in entries:
            lines.append(f"  {label}  ({char_count:,} chars)")
        return "\n".join(lines)

    def _cmd_clear(self, parts: list[str]) -> str:
        from engineering_hub.journaler.context_manager import ClearStrategy

        flags = {p.lower() for p in parts[1:]}
        if "--hard" in flags:
            strategy = ClearStrategy.HARD
        elif "--summarize" in flags:
            strategy = ClearStrategy.SUMMARIZE
        else:
            strategy = ClearStrategy.SOFT
        return self.engine.clear(strategy)

    def _cmd_load(self, parts: list[str]) -> str:
        if len(parts) < 2:
            return "Usage: /load <path> [-r]"

        recursive = "-r" in parts or "--recursive" in parts
        path_str = next((p for p in parts[1:] if not p.startswith("-")), None)
        if not path_str:
            return "Usage: /load <path> [-r]"

        path = Path(path_str).expanduser()
        if path.is_dir():
            ok, msg = self.engine.load_directory(
                path, extensions=SUPPORTED_EXTENSIONS, recursive=recursive
            )
        else:
            ok, msg = self.engine.load_file(path, extensions=SUPPORTED_EXTENSIONS)
        return msg

    def _cmd_load_recent(self, parts: list[str]) -> str:
        from datetime import datetime

        from engineering_hub.journaler.recent_files import (
            collect_recent_files,
            default_recent_roots,
        )

        list_only = "--list" in parts
        default_limit = int(
            getattr(self.settings, "journaler_load_recent_max_files", 5) or 5
        )
        default_days = getattr(self.settings, "journaler_load_recent_days", 30)

        limit = default_limit
        days = default_days
        positional = [p for p in parts[1:] if not p.startswith("-")]
        if positional:
            try:
                limit = int(positional[0])
            except ValueError:
                return "Usage: /load_recent [N] [--days D] [--list]"
        if "--days" in parts:
            idx = parts.index("--days")
            try:
                days = int(parts[idx + 1])
            except (IndexError, ValueError):
                return "--days expects an integer"

        roots = default_recent_roots(self.config, self.settings)
        if not roots:
            return "/load_recent could not resolve any scan roots."

        files = collect_recent_files(
            roots, extensions=SUPPORTED_EXTENSIONS, limit=limit, days=days
        )
        if not files:
            window = f" in the last {days} day(s)" if days else ""
            return f"No recent files found{window}."

        if list_only:
            lines = [f"Most recently created files (top {len(files)}):"]
            for i, rf in enumerate(files, 1):
                created = datetime.fromtimestamp(rf.created_ts).strftime("%Y-%m-%d %H:%M")
                lines.append(f"  {i}. {created}  [{rf.root_label}]  {rf.path}")
            lines.append("Run /load_recent without --list to load them.")
            return "\n".join(lines)

        lines = [f"Loading {len(files)} recent file(s):"]
        for rf in files:
            created = datetime.fromtimestamp(rf.created_ts).strftime("%Y-%m-%d %H:%M")
            ok, msg = self.engine.load_file(rf.path, extensions=SUPPORTED_EXTENSIONS)
            marker = "ok" if ok else "fail"
            lines.append(f"  [{marker}] ({rf.root_label}, {created}) {msg}")
        return "\n".join(lines)

    def _cmd_find(self, parts: list[str]) -> str:
        org_roam_dir = self._get_org_roam_dir()
        if org_roam_dir is None:
            return "/find requires org-roam dir."
        if len(parts) < 2:
            return "Usage: /find <title fragment>"

        from engineering_hub.journaler.org_writer import find_org_by_title

        fragment = " ".join(parts[1:])
        ok, matches = find_org_by_title(org_roam_dir, fragment)
        if not ok:
            return f"Could not search: {org_roam_dir}"
        if not matches:
            return f"No org files found matching '{fragment}'."
        lines = [f"Found {len(matches)} file(s):"]
        for p in matches:
            lines.append(f"  {p}")
        return "\n".join(lines)

    def _cmd_skills(self) -> str:
        if self.delegator is None:
            return "No delegator configured. Set journaler.agent_backend in config."
        try:
            from engineering_hub.journaler.chat_server import _handle_skills_command
            return _handle_skills_command(self.delegator)
        except Exception as e:
            return f"Error listing skills: {e}"

    def _cmd_blender(self, parts: list[str]) -> str:
        from engineering_hub.blender import service as blender_service

        sub = parts[1].lower() if len(parts) > 1 else "status"
        if sub != "status":
            return "Usage: /blender status"
        return blender_service.format_status_message()

    def _cmd_horn(self, raw: str) -> str:
        from engineering_hub.horn_iterator import service as horn_service

        return horn_service.handle_slash_command(raw)

    def _get_task_integrator(self) -> object | None:
        if self._task_integrator is not None:
            return self._task_integrator
        if self.config is None:
            return None
        from engineering_hub.journaler.task_integrator import build_task_integrator

        self._task_integrator = build_task_integrator(
            self.config, self.engine, self.delegator
        )
        return self._task_integrator

    def _cmd_integrate(self, parts: list[str]) -> str:
        integrator = self._get_task_integrator()
        if integrator is None:
            return "/integrate requires workspace config (journal_dir, state_dir)."
        if len(parts) > 1 and parts[1].lower() == "status":
            return integrator.status_summary()  # type: ignore[attr-defined]
        try:
            result = integrator.run_cycle()  # type: ignore[attr-defined]
        except Exception as e:
            return f"Task-Integrator error: {e}"
        return result.summary()

    def _cmd_task(self, parts: list[str], raw: str) -> str:
        journal_dir = self._get_journal_dir()
        if journal_dir is None:
            return "/task requires a journal directory."
        if len(parts) < 2:
            return "Usage: /task <description>"
        from engineering_hub.journaler.org_writer import add_todo_to_journal
        description = " ".join(parts[1:])
        ok, msg = add_todo_to_journal(journal_dir, description)
        return msg

    def _cmd_done(self, parts: list[str]) -> str:
        journal_dir = self._get_journal_dir()
        if journal_dir is None:
            return "/done requires a journal directory."
        if len(parts) < 2:
            return "Usage: /done <description fragment>"
        from engineering_hub.journaler.org_writer import mark_done_in_journal
        fragment = " ".join(parts[1:])
        ok, msg = mark_done_in_journal(journal_dir, fragment)
        return msg

    def _cmd_note(self, raw: str) -> str:
        journal_dir = self._get_journal_dir()
        if journal_dir is None:
            return "/note requires a journal directory."
        rest = raw[len("/note"):].strip()
        if " :: " not in rest:
            return "Usage: /note <heading> :: <text>"
        heading, _, text = rest.partition(" :: ")
        heading = heading.strip()
        text = text.strip()
        if not heading or not text:
            return "Usage: /note <heading> :: <text>"
        from engineering_hub.journaler.org_writer import (
            _create_journal_file,
            _today_journal_path,
            append_to_heading,
        )
        today_path = _today_journal_path(journal_dir)
        _create_journal_file(today_path)
        ok, msg = append_to_heading(today_path, heading, text, create_heading_if_missing=True)
        return msg

    def _cmd_open(self, parts: list[str], raw: str) -> str:
        rest = raw[len("/open"):].strip()
        if not rest:
            t = self.engine.get_roam_edit_target()
            if t:
                return f"Current edit target: {t}"
            return "No edit target set. Use /open today, a path, or a title fragment."
        if rest.lower() == "clear":
            self.engine.set_roam_edit_target(None)
            return "Edit target cleared."
        org_roam_dir = self._get_org_roam_dir()
        if org_roam_dir is None:
            return "/open requires org-roam dir."
        if rest.lower() == "today":
            journal_dir = self._get_journal_dir()
            if journal_dir is None:
                return "/open today requires a journal directory."
            from engineering_hub.journaler.org_writer import (
                _create_journal_file,
                _today_journal_path,
            )
            today_path = _today_journal_path(journal_dir)
            created = _create_journal_file(today_path)
            self.engine.set_roam_edit_target(today_path)
            verb = "Created" if created else "Opened"
            return f"{verb} today's journal for /edit: {today_path}"

        from engineering_hub.journaler.org_writer import (
            assert_org_path_under_roam,
            find_org_by_title,
        )

        path_candidate = Path(rest).expanduser()
        is_path_like = path_candidate.suffix.lower() == ".org" or path_candidate.is_file()
        if is_path_like:
            ok_path, res = assert_org_path_under_roam(path_candidate, org_roam_dir)
            if not ok_path:
                return str(res)
            if isinstance(res, Path):
                self.engine.set_roam_edit_target(res)
            return f"Opened for /edit: {res}"

        ok_find, matches = find_org_by_title(org_roam_dir, rest)
        if not ok_find:
            return f"Could not search: {org_roam_dir}"
        if not matches:
            return f"No org files found matching title '{rest}'."
        if len(matches) > 1:
            lines = [f"{len(matches)} files match '{rest}'; narrow the title:"]
            for i, p in enumerate(matches, 1):
                lines.append(f"  {i}. {p}")
            return "\n".join(lines)
        self.engine.set_roam_edit_target(matches[0])
        return f"Opened for /edit: {matches[0]}"

    def _cmd_edit(self, raw: str) -> str:
        target = self.engine.get_roam_edit_target()
        if target is None:
            return "No edit target. Use /open (today, path, or title) first."
        rest = raw[len("/edit"):].strip()
        if " :: " not in rest:
            return "Usage: /edit <heading> :: <text>"
        heading, _, text = rest.partition(" :: ")
        heading = heading.strip()
        text = text.strip()
        if not heading or not text:
            return "Usage: /edit <heading> :: <text>"
        from engineering_hub.journaler.org_writer import append_to_heading
        ok, msg = append_to_heading(target, heading, text, create_heading_if_missing=True)
        return msg

    def _cmd_timesheet(self, raw: str) -> str:
        journal_dir = self._get_journal_dir()
        if journal_dir is None:
            return "/timesheet requires a daily journal directory."
        from engineering_hub.journaler.timesheet_slash import handle_timesheet_slash_command

        export_template = None
        if self.settings is not None and hasattr(
            self.settings, "resolved_timesheet_export_template"
        ):
            export_template = self.settings.resolved_timesheet_export_template
        return handle_timesheet_slash_command(
            raw,
            journal_dir,
            export_template=export_template,
        )

    def _cmd_agent(self, raw: str) -> str:
        if self.delegator is None:
            return "No delegator configured. Set journaler.agent_backend in config."
        if self.journal_ctx is None:
            return "/agent requires journal context."
        try:
            from engineering_hub.journaler.chat_server import _handle_agent_command
            return _handle_agent_command(
                raw, self.delegator, self.journal_ctx, engine=self.engine
            )
        except Exception as e:
            return f"Agent error: {e}"

    def _cmd_history(self, raw: str) -> str:
        if self.journal_ctx is None:
            return "/history requires journal context."
        try:
            from engineering_hub.journaler.chat_server import _handle_history_command
            return _handle_history_command(
                raw, self.delegator, self.journal_ctx, engine=self.engine
            )
        except Exception as e:
            return f"History error: {e}"

    def _cmd_pipeline(self, raw: str) -> str:
        if self.journal_ctx is None:
            return "/pipeline requires journal context."
        try:
            from engineering_hub.journaler.chat_server import _handle_pipeline_command
            return _handle_pipeline_command(
                raw, self.delegator, self.journal_ctx, engine=self.engine
            )
        except Exception as e:
            return f"Pipeline error: {e}"

    def _cmd_tasks(self, raw: str) -> str:
        if self.settings is None:
            return "/tasks requires workspace settings."
        try:
            from engineering_hub.journaler.task_slash import handle_tasks_slash_command
            pending_file = self.settings.resolved_journaler_pending_tasks_file  # type: ignore[attr-defined]
            return handle_tasks_slash_command(raw, self.engine, pending_file)
        except Exception as e:
            return f"Tasks error: {e}"

    def _cmd_context(self, parts: list[str]) -> str:
        """Show suggested context files or load one by number."""
        if self.load_tracker is None:
            return self._cmd_context_scan()

        # If a number is provided, load that file
        if len(parts) >= 2 and parts[1].isdigit():
            idx = int(parts[1]) - 1
            top = self.load_tracker.top_files(10)
            if 0 <= idx < len(top):
                path_str, _ = top[idx]
                path = Path(path_str)
                if path.exists():
                    ok, msg = self.engine.load_file(path, extensions=SUPPORTED_EXTENSIONS)
                    return msg
                return f"File not found: {path}"
            return f"Invalid number. Use 1-{len(top)}."

        # Show top suggested files
        top = self.load_tracker.top_files(10)
        if not top:
            return self._cmd_context_scan()

        lines = ["Suggested context files (by frequency/recency):"]
        for i, (path_str, score) in enumerate(top, 1):
            name = Path(path_str).name
            lines.append(f"  {i}. {name}  (score: {score:.2f})")
        lines.append("\nUse /context <number> to load a file.")
        return "\n".join(lines)

    def _cmd_context_scan(self) -> str:
        """Fallback: scan for recent files when no tracker data exists."""
        journal_dir = self._get_journal_dir()
        lines = ["Quick Context — recent files:"]

        if journal_dir and journal_dir.is_dir():
            journals = sorted(journal_dir.glob("*.org"), key=lambda p: p.name, reverse=True)[:5]
            if journals:
                lines.append("\n  Journals:")
                for j in journals:
                    lines.append(f"    {j.name}")

        org_roam_dir = self._get_org_roam_dir()
        if org_roam_dir and org_roam_dir.is_dir():
            all_org = [
                f for f in org_roam_dir.rglob("*.org")
                if not (journal_dir and f.is_relative_to(journal_dir))
            ]
            recent = sorted(all_org, key=lambda p: p.stat().st_mtime, reverse=True)[:5]
            if recent:
                lines.append("\n  Recent project notes:")
                for f in recent:
                    lines.append(f"    {f.name}")

        lines.append("\nUse /load <path> to load a file into context.")
        return "\n".join(lines)

    def _cmd_model(self, raw: str) -> str:
        if self.settings is None or self.model_ctx is None:
            return "/model requires internal context (file a bug if you see this)."
        from engineering_hub.journaler.model_profiles import journaler_slash_model_command

        return journaler_slash_model_command(
            raw,
            settings=self.settings,
            model_ctx=self.model_ctx,
            engine=self.engine,
            delegator=self.delegator,
        )

    def _cmd_help(self) -> str:
        lines = [
            "Slash commands (press Ctrl+P for command palette):",
            "",
            "Context Management:",
            "  /model                        Show model, settings, and available /model set commands",
            "  /model <profile>              Switch to a named model profile (full reload)",
            "  /model path <hf-id>           Load a model by path (full reload)",
            "  /model set thinking on|off|auto  Toggle thinking mode live",
            "  /model set streaming on|off   Toggle per-token streaming output live",
            "  /model set temp <float>       Adjust temperature live",
            "  /model set top_p <float>      Adjust top-p sampling live",
            "  /model set min_p <float>      Adjust min-p sampling live",
            "  /model set max_tokens <int>   Adjust max generation tokens live",
            "  /model_browse                 Browse mlx-community models and profiles",
            "  /status                   Show context pressure and turn count",
            "  /budget                   Token budget breakdown",
            "  /topic                    Show detected conversation topic",
            "  /clear [--hard|--summarize] Clear conversation history",
            "  /summarize                Generate daily summary and archive",
            "  /files [clear]            List or clear loaded files",
            "",
            "File Operations:",
            "  /load <path> [-r]         Load a file or directory into context",
            "  /load_recent [N] [--days D] [--list]  Load most recently created workspace files",
            "  /find <title>             Search org-roam files by title",
            "",
            "Agent Delegation:",
            "  /agent <type> <desc>      Delegate to an agent persona",
            "  /pipeline draft-section   Run multi-stage report pipeline",
            "  /tasks [confirm|commit|rollback] Overnight task queue",
            "  /queue <description>      Propose a task for overnight queue",
            "  /history [--agent] <query> Retrieve prior chat excerpts",
            "  /skills                   List available agent personas",
            "  /blender status           Check Blender MCP addon connectivity",
            "  /horn [sweep|defaults]    Run parametric horn sweep / show LVT defaults",
            "  /integrate [status]       Interview inline in today's journal, propose agent tasks",
            "",
            "Org-Roam Writing:",
            "  /open [today|clear|path]  Set session edit target",
            "  /edit <heading> :: <text> Append under a heading",
            "  /task <description>       Add TODO to today's journal",
            "  /done <fragment>          Mark a TODO as done",
            "  /timesheet <hours> ...    Log hours by project",
            "  /timesheet export ...     Export final monthly timesheet",
            "  /note <heading> :: <text> Append to today's journal",
            "",
            "Session:",
            "  /exit, /quit              Leave the TUI",
            "  /help                     Show this help",
        ]
        return "\n".join(lines)

    def _cmd_capture_list(self) -> str:
        try:
            from engineering_hub.capture.loader import (
                _default_capture_templates_dir,
                load_capture_templates,
            )
            ct_dir = _default_capture_templates_dir()
            templates = load_capture_templates(ct_dir)
            if not templates:
                return "No capture templates found."
            lines = ["Capture Templates:"]
            for tpl in templates.values():
                lines.append(f"  {tpl.key}: {tpl.display_name} - {tpl.description[:50]}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error loading templates: {e}"
