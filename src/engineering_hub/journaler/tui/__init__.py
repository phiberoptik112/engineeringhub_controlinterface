"""Textual-based TUI for the Engineering Hub Journaler."""

from __future__ import annotations

__all__ = ["JournalerApp"]


def run_tui(
    engine: object,
    delegator: object | None,
    config: object,
    model_label: str = "",
    settings: object | None = None,
    model_ctx: object | None = None,
) -> None:
    """Launch the Textual TUI app (called from CLI)."""
    from engineering_hub.journaler.tui.app import JournalerApp

    state_dir = getattr(config, "state_dir", None)
    app = JournalerApp(
        engine=engine,  # type: ignore[arg-type]
        delegator=delegator,  # type: ignore[arg-type]
        config=config,  # type: ignore[arg-type]
        model_label=model_label,
        state_dir=state_dir,  # type: ignore[arg-type]
        settings=settings,
        model_ctx=model_ctx,
    )
    app.run()
