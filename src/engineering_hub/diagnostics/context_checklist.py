"""Heuristic detection of which blocks appear in a formatted agent context string."""

from __future__ import annotations

import re
from typing import Any

_SEGMENT_SEP = "\n\n---\n\n"


def _base_segment_properties(base: str) -> dict[str, Any]:
    """Classify the primary (Django/minimal) formatted section."""
    title = "## Project Context:" in base or "# Project Context:" in base
    client = bool(
        re.search(r"\*\*Client\*\*:", base)
        or re.search(r"\*\*Client\*\* :", base)
        or "### Client Information" in base
    )
    status = "**Status**:" in base or "### Project Overview" in base
    overview = title and client and (
        "### Project Overview" in base
        or "### Client Information" in base
        or "### Review Context" in base
        or "### Evaluation Context" in base
    )

    scope = any(
        h in base
        for h in (
            "### Scope of Work",
            "### Document Purpose",
            "### Scope Items to Verify",
            "### Scope (for alignment check)",
            "### Scope",
            "### Success Criteria (from Scope)",
        )
    )

    standards = any(
        h in base
        for h in (
            "### Standards & Requirements",
            "### Standards to Reference",
            "**Required Standards:**",
            "### Standards for Verification",
            "### Quality Standards",
            "### Compliance Requirements",
        )
    )

    files = any(
        h in base
        for h in (
            "### Available Project Files",
            "### Available Research & Files",
            "### Documents Available for Review",
            "### Available Source Materials",
        )
    )

    referenced = "### Referenced Documents" in base

    truncated = "..." in base and "```" in base

    return {
        "project_overview": bool(overview or (title and client and status)),
        "scope_of_work": scope,
        "standards_list": standards,
        "available_files_list": files,
        "task_referenced_documents": referenced,
        "referenced_docs_maybe_truncated": truncated,
    }


def _classify_post_base_segments(extra_segments: list[str]) -> dict[str, bool]:
    memory = corpus = template = False
    corpus_guesses: list[str] = []

    for seg in extra_segments:
        if "### Relevant Past Context" in seg:
            memory = True
        elif "## Report Template:" in seg:
            template = True
        else:
            corpus_guesses.append(seg)

    # Any remaining non-empty segment between memory and template is treated as corpus-like.
    if corpus_guesses:
        corpus = True

    return {
        "memory_block": memory,
        "corpus_block": corpus,
        "template_skeleton": template,
    }


def analyze_formatted_context(formatted: str) -> dict[str, Any]:
    """Return structured flags for CONTEXT DELIVERED checklist items.

    Corpus detection: formatter appends opaque ``corpus_service.format_for_context`` output
    after a ``---`` separator; any trailing segment that is not memory or template is
    counted as corpus-like.
    """
    parts = formatted.split(_SEGMENT_SEP)
    base = parts[0] if parts else ""
    extras = [p for p in parts[1:] if p.strip()] if len(parts) > 1 else []

    base_props = _base_segment_properties(base)
    post = _classify_post_base_segments(extras)

    return {
        **base_props,
        **post,
        "segment_count": len(parts),
    }


def checklist_for_template(analysis: dict[str, Any]) -> dict[str, bool]:
    """Boolean map aligned with the human task log CONTEXT DELIVERED section."""
    return {
        "project_overview": analysis["project_overview"],
        "scope_of_work": analysis["scope_of_work"],
        "standards_list": analysis["standards_list"],
        "available_files_list": analysis["available_files_list"],
        "task_referenced_document_contents": analysis["task_referenced_documents"],
        "memory_block": analysis["memory_block"],
        "corpus_block": analysis["corpus_block"],
        "template_skeleton": analysis["template_skeleton"],
    }
