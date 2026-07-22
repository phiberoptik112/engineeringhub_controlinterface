"""Build ConversationsConfig from Settings and wire ConversationStore."""

from __future__ import annotations

from pathlib import Path

from engineering_hub.config.settings import Settings
from engineering_hub.journaler.conversation_store import ConversationStore, build_conversation_store
from engineering_hub.journaler.daemon import ConversationsConfig, JournalerConfig
from engineering_hub.journaler.engine import ConversationEngine


def conversations_config_from_settings(settings: Settings) -> ConversationsConfig:
    """Build ConversationsConfig from YAML-backed Settings."""
    state_dir = settings.journaler_state_dir
    db_path = settings.journaler_conversations_db_path
    store_dir = settings.journaler_conversations_store_dir
    if db_path is not None and not db_path.is_absolute():
        db_path = state_dir / db_path
    if store_dir is not None and not store_dir.is_absolute():
        store_dir = state_dir / store_dir
    return ConversationsConfig(
        enabled=settings.journaler_conversations_enabled,
        db_path=db_path,
        store_dir=store_dir,
        default_project=settings.journaler_conversations_default_project,
        restore_files_on_switch=settings.journaler_conversations_restore_files_on_switch,
        max_restore_history_turns=settings.journaler_conversations_max_restore_history_turns,
        suggest_split_on_topic_shift=settings.journaler_conversations_suggest_split_on_topic_shift,
    )


def build_store_from_config(
    state_dir: Path,
    config: ConversationsConfig,
) -> ConversationStore | None:
    return build_conversation_store(
        state_dir,
        enabled=config.enabled,
        db_path=config.db_path,
        store_dir=config.store_dir,
    )


def wire_conversation_engine(
    engine: ConversationEngine,
    store: ConversationStore,
    config: ConversationsConfig,
) -> str:
    """Attach store to engine and switch to active/seed conversation."""
    engine.attach_conversation_store(store, config)
    conv = store.ensure_initialized()
    return engine.switch_session(
        conv,
        restore_files=config.restore_files_on_switch,
    )


def resolve_transcript_path(
    state_dir: Path,
    *,
    store: ConversationStore | None = None,
) -> Path:
    """Active conversation JSONL, or legacy singleton."""
    if store is not None:
        return store.active_jsonl_path()
    return state_dir / "conversation.jsonl"


def attach_conversations_to_engine(
    engine: ConversationEngine,
    config: JournalerConfig,
) -> str | None:
    """Attach conversation store to engine if enabled. Returns switch status."""
    conv_cfg = config.get_conversations_config()
    store = build_store_from_config(config.state_dir, conv_cfg)
    if store is None:
        return None
    return wire_conversation_engine(engine, store, conv_cfg)
