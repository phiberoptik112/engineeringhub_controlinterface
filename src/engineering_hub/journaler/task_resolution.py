"""Task key normalization and prose completion detection for JournalContext."""

from __future__ import annotations

import re
from dataclasses import dataclass

from engineering_hub.journaler.models import CompletionKind, TrackedTask

_AGENT_PREFIX = re.compile(r"^@([\w-]+):\s*(.+)$", re.IGNORECASE)
_PROSE_VERB = re.compile(
    r"\b(done|finished|completed|closed|wrapped up|complete)\b",
    re.IGNORECASE,
)
_CLOSED_MARKER = re.compile(r"\bCLOSED:\s*\d{4}-\d{2}-\d{2}", re.IGNORECASE)


def normalize_task_key(text: str) -> str:
    """Stable dedup key: lowercase, strip @agent prefix, collapse whitespace."""
    t = text.strip().lower()
    m = _AGENT_PREFIX.match(t)
    if m:
        t = m.group(2).strip()
    t = re.sub(r"\s+", " ", t)
    return t[:120]


def task_text_fragments(text: str) -> list[str]:
    """Return searchable fragments for fuzzy task matching."""
    words = normalize_task_key(text).split()
    if len(words) <= 2:
        return [normalize_task_key(text)] if words else []
    return [" ".join(words[i : i + 3]) for i in range(len(words) - 2)]


def text_mentions_task(corpus: str, task_text: str) -> bool:
    """True when corpus contains a significant fragment of task_text."""
    corpus_lower = corpus.lower()
    words = normalize_task_key(task_text).split()
    if len(words) <= 2:
        return normalize_task_key(task_text) in corpus_lower
    return any(frag in corpus_lower for frag in task_text_fragments(task_text))


@dataclass
class ProseCompletionHit:
    """A pending task resolved by prose in journal text."""

    task_key: str
    text: str
    detail: str


def detect_prose_completions(
    pending_tasks: list[TrackedTask],
    *,
    new_text: str,
    old_text: str = "",
) -> list[ProseCompletionHit]:
    """Detect pending tasks completed by prose in added/changed journal lines."""
    if not new_text.strip():
        return []

    old_lines = set(old_text.splitlines()) if old_text else set()
    added_lines = [
        line for line in new_text.splitlines()
        if line.strip() and line not in old_lines
    ]
    if not added_lines:
        added_lines = new_text.splitlines()

    corpus = "\n".join(added_lines)
    hits: list[ProseCompletionHit] = []
    seen_keys: set[str] = set()

    for task in pending_tasks:
        if task.status != "pending" or task.task_key in seen_keys:
            continue
        if not text_mentions_task(corpus, task.text):
            continue

        matched_line = ""
        for line in added_lines:
            if text_mentions_task(line, task.text):
                matched_line = line.strip()
                break

        if not matched_line:
            continue

        has_signal = bool(_PROSE_VERB.search(matched_line)) or bool(
            _CLOSED_MARKER.search(matched_line)
        )
        if not has_signal:
            # Also accept "task text — done" style trailing markers
            lower = matched_line.lower()
            if not any(
                lower.endswith(suffix)
                for suffix in (" done", " complete", " completed", " finished")
            ):
                continue

        hits.append(
            ProseCompletionHit(
                task_key=task.task_key,
                text=task.text,
                detail=matched_line[:200],
            )
        )
        seen_keys.add(task.task_key)

    return hits


def merge_tracked_tasks(records: list[TrackedTask]) -> list[TrackedTask]:
    """Merge tasks by task_key; completed wins; newest last_seen wins ties."""
    by_key: dict[str, TrackedTask] = {}
    for record in records:
        existing = by_key.get(record.task_key)
        if existing is None:
            by_key[record.task_key] = record
            continue

        if existing.status == "pending" and record.status == "completed":
            merged = record
        elif existing.status == "completed" and record.status == "pending":
            merged = existing
        elif record.last_seen > existing.last_seen:
            merged = record
        else:
            merged = existing

        merged.first_seen = min(existing.first_seen, record.first_seen)
        by_key[record.task_key] = merged

    return list(by_key.values())


def derive_task_lists(
    tracked: list[TrackedTask],
) -> tuple[list[str], list[str], dict[str, str]]:
    """Return pending texts, completed texts, and task_first_seen map."""
    pending: list[str] = []
    completed: list[str] = []
    first_seen: dict[str, str] = {}

    for task in tracked:
        first_seen[task.task_key] = task.first_seen
        if task.status == "completed":
            completed.append(task.text)
        else:
            pending.append(task.text)

    return pending, completed, first_seen


def is_task_already_completed(description: str, completed_keys: set[str]) -> bool:
    """True when description matches a completed task key (exact or substring)."""
    if not completed_keys:
        return False
    desc_key = normalize_task_key(description)
    if desc_key in completed_keys:
        return True
    for key in completed_keys:
        if len(key) < 8:
            continue
        if key in desc_key or desc_key in key:
            return True
    return False
