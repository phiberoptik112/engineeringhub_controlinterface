"""Construct optional CorpusService from Hub Settings (libraryfiles-corpus)."""

from __future__ import annotations

import logging
from typing import Any

from engineering_hub.config.settings import Settings

logger = logging.getLogger(__name__)

try:
    from corpus.service import CorpusService as _CorpusService
except ImportError:
    _CorpusService = None


def build_corpus_service_from_settings(settings: Settings) -> Any:
    """Return a CorpusService instance, or None if corpus is disabled or unavailable.

    libraryfiles-corpus must be installed in the same environment (pip install -e ...).
    """
    if _CorpusService is None:
        logger.warning(
            "PDF corpus unavailable: install libraryfiles-corpus in this environment "
            "(e.g. pip install -e /path/to/libraryfiles_corpus)."
        )
        return None
    if not settings.corpus_enabled:
        logger.debug("PDF corpus disabled (corpus.enabled: false).")
        return None
    db_path = settings.corpus_db_path
    if db_path is None:
        logger.warning(
            "corpus.enabled is true but corpus.db_path is not set; corpus search disabled."
        )
        return None
    expanded = db_path.expanduser()
    if not expanded.exists():
        logger.warning(
            "corpus.db not found at %s; run ingest or fix corpus.db_path.",
            expanded,
        )
        return None
    try:
        service = _CorpusService.from_db_path(
            expanded,
            ollama_host=settings.ollama_host,
            ollama_model=settings.ollama_embed_model,
            search_k=settings.corpus_search_k,
            search_threshold=settings.corpus_search_threshold,
        )
    except Exception as exc:
        logger.warning("Failed to initialize CorpusService: %s", exc)
        return None
    if service.is_available():
        logger.info("PDF corpus service ready (DB: %s).", expanded)
    else:
        logger.warning(
            "CorpusService initialized but embedder unavailable; "
            "corpus search will return empty until Ollama is running."
        )
    return service
