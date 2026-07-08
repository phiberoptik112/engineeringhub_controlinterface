"""Main Textual application for the Journaler TUI."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import ContentSwitcher, Footer, Header

from engineering_hub.journaler.tui.widgets.activity_log import ActivityLog
from engineering_hub.journaler.tui.widgets.browser_panel import BrowserPanel
from engineering_hub.journaler.tui.widgets.chat_view import ChatView
from engineering_hub.journaler.tui.widgets.command_card import CommandGrid
from engineering_hub.journaler.tui.widgets.context_panel import QuickContextPanel
from engineering_hub.journaler.tui.widgets.input_bar import InputBar
from engineering_hub.journaler.tui.widgets.sidebar import BrowserNavBar, CategorySidebar
from engineering_hub.journaler.tui.widgets.status_bar import StatusBar

if TYPE_CHECKING:
    from engineering_hub.journaler.delegator import AgentDelegator
    from engineering_hub.journaler.engine import ConversationEngine
    from engineering_hub.journaler.tui.load_tracker import LoadTracker

# Commands that open the interactive browser instead of going through CommandExecutor.
_BROWSE_COMMANDS: frozenset[str] = frozenset({
    "/load_browse", "/edit_browse", "/agent_browse", "/capture_browse", "/model_browse",
})

# Supported extensions reused from command_executor (keep in sync).
_SUPPORTED_EXTENSIONS = frozenset({
    ".md", ".txt", ".org", ".py", ".yaml", ".yml",
    ".json", ".tex", ".csv", ".toml", ".rst", ".docx", ".pdf",
})


class JournalerApp(App):
    """Full-screen Textual TUI for Engineering Hub Journaler."""

    CSS_PATH = "styles/app.tcss"
    TITLE = "Engineering Hub Journaler"
    SUB_TITLE = "TUI Mode"

    BINDINGS = [
        Binding("ctrl+p", "show_palette", "Command Palette", show=True),
        Binding("ctrl+l", "show_context", "Quick Context", show=True),
        Binding("ctrl+g", "toggle_activity", "Activity Log", show=True),
        Binding("escape", "focus_chat", "Focus Chat", show=False),
    ]

    def __init__(
        self,
        engine: ConversationEngine,
        delegator: AgentDelegator | None,
        config: object,
        model_label: str = "",
        state_dir: Path | None = None,
        settings: object | None = None,
        model_ctx: object | None = None,
    ) -> None:
        super().__init__()
        self.engine = engine
        self.delegator = delegator
        self.config = config
        self.model_label = model_label
        self._state_dir = state_dir
        self.settings = settings
        self.model_ctx = model_ctx

        self._load_tracker: LoadTracker | None = None
        if state_dir:
            from engineering_hub.journaler.tui.load_tracker import LoadTracker
            self._load_tracker = LoadTracker(state_dir)

        # Track which ContentSwitcher view was active before entering browser.
        self._pre_browser_view: str = "chat-view"

    @property
    def load_tracker(self) -> LoadTracker | None:
        return self._load_tracker

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main-container"):
            with Vertical(id="sidebar-column"):
                yield BrowserNavBar(id="browser-nav")
                yield CategorySidebar(id="sidebar")
            with Vertical(id="content-area"):
                with ContentSwitcher(initial="chat-view", id="content-switcher"):
                    yield ChatView(id="chat-view")
                    yield CommandGrid(id="commands-view")
                    yield QuickContextPanel(
                        id="context-view",
                        engine=self.engine,
                        config=self.config,
                        load_tracker=self._load_tracker,
                    )
                    yield BrowserPanel(id="browser-view")
                yield ActivityLog(id="activity-log", max_lines=200)
                yield InputBar(id="input-bar")
        yield StatusBar(model_label=self.model_label, id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        self._update_status()
        self.query_one(InputBar).focus()

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------

    def _update_status(self) -> None:
        status = self.engine.get_status()
        if self.model_ctx is not None:
            from engineering_hub.journaler.model_profiles import journaler_model_display_label

            self.model_label = journaler_model_display_label(self.model_ctx.spec)
        self.query_one(StatusBar).update_status(status, self.model_label)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_show_palette(self) -> None:
        from engineering_hub.journaler.tui.screens.palette import PaletteScreen
        self.push_screen(PaletteScreen(), callback=self._on_palette_result)

    def _on_palette_result(self, command: str | None) -> None:
        if command:
            self._execute_command(command)

    def action_show_context(self) -> None:
        switcher = self.query_one("#content-switcher", ContentSwitcher)
        self._exit_browser_mode()
        switcher.current = "context-view"
        panel = self.query_one(QuickContextPanel)
        self.call_after_refresh(lambda: self.call_after_refresh(panel.focus_active_list))

    def action_focus_chat(self) -> None:
        switcher = self.query_one("#content-switcher", ContentSwitcher)
        # If browser is open, Escape cancels the browser.
        if switcher.current == "browser-view":
            self._exit_browser_mode()
            return
        switcher.current = "chat-view"
        self.query_one(InputBar).focus()

    def action_focus_sidebar(self) -> None:
        self.query_one(CategorySidebar).focus()

    def action_toggle_activity(self) -> None:
        log = self.query_one(ActivityLog)
        log.display = not log.display

    # ------------------------------------------------------------------
    # Browser mode helpers
    # ------------------------------------------------------------------

    def _enter_browser_mode(self, mode: str, path: str = "") -> None:
        """Show the browser nav bar and record the current view."""
        nav = self.query_one(BrowserNavBar)
        mode_labels = {
            "load": "Load Browse",
            "edit": "Edit Browse",
            "skills": "Agent Browse",
            "capture": "Capture Browse",
            "models": "Model Browse",
        }
        nav.set_mode(mode_labels.get(mode, mode), path)

        switcher = self.query_one("#content-switcher", ContentSwitcher)
        if switcher.current != "browser-view":
            self._pre_browser_view = switcher.current
        switcher.current = "browser-view"

    def _exit_browser_mode(self) -> None:
        """Hide browser nav bar and restore the previous view."""
        self.query_one(BrowserNavBar).clear_mode()
        switcher = self.query_one("#content-switcher", ContentSwitcher)
        switcher.current = self._pre_browser_view
        self.query_one(InputBar).focus()

    def _get_org_roam_dir(self) -> Path | None:
        if self.config and hasattr(self.config, "org_roam_dir"):
            return Path(self.config.org_roam_dir)  # type: ignore[arg-type]
        return None

    # ------------------------------------------------------------------
    # Input / chat
    # ------------------------------------------------------------------

    async def on_input_bar_submitted(self, event: InputBar.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return

        chat_view = self.query_one(ChatView)

        if text.startswith("/"):
            chat_view.add_user_message(text)
            await self._execute_command(text)
        else:
            chat_view.add_user_message(text)
            self.query_one(ActivityLog).log_operation("chat", "sending…", status="running")
            self.run_worker(self._do_chat(text), exclusive=True, thread=True)

    async def _do_chat(self, message: str) -> None:
        chat_view = self.query_one(ChatView)
        activity_log = self.query_one(ActivityLog)
        try:
            response = self.engine.chat(message)
            self.call_from_thread(chat_view.add_assistant_message, response)
            self.call_from_thread(activity_log.log_chat_response, "ok", response)
        except Exception as e:
            self.call_from_thread(chat_view.add_system_message, f"Error: {e}")
            self.call_from_thread(activity_log.log_operation, "chat", "response", "error", str(e)[:60])
        self.call_from_thread(self._update_status)

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    async def _execute_command(self, command_str: str) -> None:
        parts = command_str.split()
        cmd = parts[0].lower() if parts else ""

        # Intercept browse commands — open the native browser widget.
        if cmd in _BROWSE_COMMANDS:
            await self._open_browser(cmd)
            return

        from engineering_hub.journaler.tui.command_executor import (
            CommandExecutor,
            JournalerChatExit,
        )

        executor = CommandExecutor(
            self.engine,
            self.delegator,
            self.config,
            settings=self.settings,
            model_ctx=self.model_ctx,
        )
        chat_view = self.query_one(ChatView)
        activity_log = self.query_one(ActivityLog)

        is_load = cmd == "/load"
        load_path = parts[1] if is_load and len(parts) >= 2 else ""

        try:
            result = executor.execute(command_str)
            if result:
                chat_view.add_system_message(result)

            if is_load and load_path:
                ok = result and ("loaded" in result.lower() or "chars" in result.lower())
                activity_log.log_file_load(
                    load_path,
                    status="ok" if ok else "warn",
                    detail=result.splitlines()[0][:60] if result else "",
                )
            else:
                activity_log.log_command(
                    cmd,
                    status="ok",
                    detail=result.splitlines()[0][:60] if result else "",
                )

        except JournalerChatExit:
            self.exit()
        except Exception as e:
            chat_view.add_system_message(f"Error: {e}")
            activity_log.log_command(cmd, status="error", detail=str(e)[:60])

        self._update_status()

    # ------------------------------------------------------------------
    # Browser open / confirm / cancel
    # ------------------------------------------------------------------

    async def _open_browser(self, cmd: str) -> None:
        """Configure and display the BrowserPanel for the given browse command."""
        browser = self.query_one(BrowserPanel)
        activity_log = self.query_one(ActivityLog)

        if cmd == "/load_browse":
            root = self._get_org_roam_dir() or Path.home()
            browser.configure("load", root=root)
            self._enter_browser_mode("load", str(root))
            activity_log.log_command("/load_browse", status="info", detail="browser open")

        elif cmd == "/edit_browse":
            root = self._get_org_roam_dir() or Path.home()
            browser.configure("edit", root=root)
            self._enter_browser_mode("edit", str(root))
            activity_log.log_command("/edit_browse", status="info", detail="browser open")

        elif cmd == "/agent_browse":
            skills = self._get_skills()
            if not skills:
                self.query_one(ChatView).add_system_message(
                    "No agent skills available. Check journaler.skills_dir in config."
                )
                activity_log.log_command("/agent_browse", status="warn", detail="no skills found")
                return
            browser.configure("skills", items=skills)
            self._enter_browser_mode("skills")
            activity_log.log_command("/agent_browse", status="info", detail=f"{len(skills)} skills")

        elif cmd == "/capture_browse":
            templates = self._get_capture_templates()
            if not templates:
                self.query_one(ChatView).add_system_message(
                    "No capture templates found."
                )
                activity_log.log_command("/capture_browse", status="warn", detail="no templates")
                return
            browser.configure("capture", items=templates)
            self._enter_browser_mode("capture")
            activity_log.log_command(
                "/capture_browse", status="info", detail=f"{len(templates)} templates"
            )

        elif cmd == "/model_browse":
            if self.settings is None or self.model_ctx is None:
                self.query_one(ChatView).add_system_message(
                    "Model browse requires settings context (file a bug if you see this)."
                )
                activity_log.log_command("/model_browse", status="error", detail="no context")
                return
            from engineering_hub.journaler.model_catalog import build_model_catalog

            catalog = build_model_catalog(self.settings, self.model_ctx.spec)
            if not catalog:
                self.query_one(ChatView).add_system_message(
                    "No mlx-community models in HF cache and no journaler.models profiles. "
                    "Run: engineering-hub journaler download"
                )
                activity_log.log_command("/model_browse", status="warn", detail="empty catalog")
                return
            browser.configure("models", items=catalog)
            self._enter_browser_mode("models")
            activity_log.log_command(
                "/model_browse", status="info", detail=f"{len(catalog)} models"
            )

        # Focus the browser list for immediate keyboard navigation.
        self.call_after_refresh(self._focus_browser_list)

    def _focus_browser_list(self) -> None:
        try:
            self.query_one("#bp-list").focus()
        except Exception:
            pass

    def _get_skills(self) -> list:
        if self.delegator is None:
            return []
        try:
            return list(self.delegator.skills.values())
        except Exception:
            return []

    def _get_capture_templates(self) -> list:
        try:
            from engineering_hub.capture.loader import (
                _default_capture_templates_dir,
                load_capture_templates,
            )
            ct_dir = _default_capture_templates_dir()
            tpls = load_capture_templates(ct_dir)
            return list(tpls.values()) if tpls else []
        except Exception:
            return []

    def on_browser_panel_confirmed(self, event: BrowserPanel.Confirmed) -> None:
        """Handle confirmed selection from the BrowserPanel."""
        self._exit_browser_mode()
        chat_view = self.query_one(ChatView)
        activity_log = self.query_one(ActivityLog)

        if event.mode == "load":
            if not event.files:
                chat_view.add_system_message("No files selected.")
                return
            results: list[str] = []
            for path in event.files:
                ok, msg = self.engine.load_file(path, extensions=_SUPPORTED_EXTENSIONS)
                results.append(msg)
                activity_log.log_file_load(str(path), status="ok" if ok else "error", detail=msg.splitlines()[0][:50])
                if ok and self._load_tracker:
                    self._load_tracker.record_load(path, "browse")
            chat_view.add_system_message("\n".join(results))

        elif event.mode == "edit":
            if not event.files:
                chat_view.add_system_message("No file selected.")
                return
            target = event.files[0]
            self.engine.set_roam_edit_target(target)
            msg = f"Opened for /edit: {target}"
            chat_view.add_system_message(msg)
            activity_log.log_file_load(str(target), status="ok", detail="edit target set")

        elif event.mode == "skills":
            skill = event.skill
            if skill is None:
                return
            name = getattr(skill, "display_name", str(skill))
            msg = f"Agent persona set: {name}\nUse /agent {getattr(skill, 'name', name)} <description> to delegate."
            chat_view.add_system_message(msg)
            activity_log.log_command("/agent_browse", status="ok", detail=name)

        elif event.mode == "capture":
            tpl = event.template
            if tpl is None:
                return
            name = getattr(tpl, "display_name", str(tpl))
            key = getattr(tpl, "key", "")
            msg = f"Template: {name}\nUse /capture {key} to apply."
            chat_view.add_system_message(msg)
            activity_log.log_command("/capture_browse", status="ok", detail=name)

        elif event.mode == "models":
            entry = event.model_entry
            if entry is None or self.settings is None or self.model_ctx is None:
                return
            from engineering_hub.journaler.model_profiles import load_model_from_catalog_entry

            msg = load_model_from_catalog_entry(
                entry,
                settings=self.settings,
                model_ctx=self.model_ctx,
                engine=self.engine,
                delegator=self.delegator,
            )
            chat_view.add_system_message(msg)
            self.model_label = entry.profile_name or entry.label
            activity_log.log_command("/model_browse", status="ok", detail=entry.label)

        self._update_status()

    def on_browser_panel_cancelled(self, event: BrowserPanel.Cancelled) -> None:
        """Handle browser cancellation."""
        self._exit_browser_mode()
        self.query_one(ActivityLog).log_operation("cmd", "browser", status="info", detail="cancelled")

    # ------------------------------------------------------------------
    # Sidebar / command grid events
    # ------------------------------------------------------------------

    def on_category_sidebar_category_selected(
        self, event: CategorySidebar.CategorySelected
    ) -> None:
        switcher = self.query_one("#content-switcher", ContentSwitcher)
        self._exit_browser_mode()
        if event.category == "Quick Context":
            switcher.current = "context-view"
            panel = self.query_one(QuickContextPanel)
            self.call_after_refresh(lambda: self.call_after_refresh(panel.focus_active_list))
        else:
            switcher.current = "commands-view"
            self.query_one(CommandGrid).show_category(event.category)

    def on_command_grid_command_selected(
        self, event: CommandGrid.CommandSelected
    ) -> None:
        from engineering_hub.journaler.tui.widgets.command_card import MODEL_PICKER_COMMANDS

        if event.command_entry.name in MODEL_PICKER_COMMANDS:
            self._open_model_picker()
            return
        if event.needs_input:
            from engineering_hub.journaler.tui.screens.commands import CommandInputScreen
            self.push_screen(
                CommandInputScreen(event.command_entry),
                callback=self._on_command_input_result,
            )
        else:
            self._execute_command(event.command_entry.name)

    def _open_model_picker(self) -> None:
        from engineering_hub.journaler.model_catalog import build_model_catalog
        from engineering_hub.journaler.tui.screens.commands import ModelPickerScreen

        catalog = []
        if self.settings is not None and self.model_ctx is not None:
            catalog = build_model_catalog(self.settings, self.model_ctx.spec)
        self.push_screen(ModelPickerScreen(catalog), callback=self._on_model_picker_result)

    def _on_model_picker_result(self, result: object | None) -> None:
        if result is None:
            return
        if isinstance(result, str):
            self._execute_command(result)
            return
        if self.settings is None or self.model_ctx is None:
            return
        from engineering_hub.journaler.model_profiles import load_model_from_catalog_entry

        chat_view = self.query_one(ChatView)
        activity_log = self.query_one(ActivityLog)
        msg = load_model_from_catalog_entry(
            result,
            settings=self.settings,
            model_ctx=self.model_ctx,
            engine=self.engine,
            delegator=self.delegator,
        )
        chat_view.add_system_message(msg)
        self.model_label = getattr(result, "profile_name", None) or getattr(result, "label", self.model_label)
        activity_log.log_command("/model", status="ok", detail=self.model_label)
        self._update_status()

    def _on_command_input_result(self, command_str: str | None) -> None:
        if command_str:
            self._execute_command(command_str)

    # ------------------------------------------------------------------
    # Context panel file-load feedback
    # ------------------------------------------------------------------

    def on_quick_context_panel_file_load_requested(
        self, event: QuickContextPanel.FileLoadRequested
    ) -> None:
        chat_view = self.query_one(ChatView)
        activity_log = self.query_one(ActivityLog)
        status = "ok" if event.ok else "error"
        chat_view.add_system_message(event.message)
        activity_log.log_file_load(
            str(event.file_path), status=status, detail=event.message.splitlines()[0][:50]
        )
        self._update_status()
