"""Retrieve prior Journaler chat context from summaries and JSONL transcripts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

_PAST_REFERENCE_RE = re.compile(
    r"\b(previous|prior|past|earlier|last time|last chat|that chat|that session|"
    r"conversation|session|we discussed|remember when|from before|yesterday)\b",
    re.IGNORECASE,
)
_TOKEN_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{2,}")
_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
_STOPWORDS = {
    "about",
    "again",
    "before",
    "chat",
    "conversation",
    "could",
    "discussed",
    "earlier",
    "from",
    "have",
    "last",
    "past",
    "please",
    "previous",
    "prior",
    "session",
    "that",
    "the",
    "this",
    "time",
    "what",
    "when",
    "where",
    "with",
    "would",
    "yesterday",
    "you",
}


@dataclass(frozen=True)
class SessionRetrievalHit:
    """A matching prior conversation excerpt."""

    source: str
    date: str
    excerpt: str
    score: float


def references_past_session(message: str) -> bool:
    """Return True when a message appears to ask about prior chat context."""
    return bool(_PAST_REFERENCE_RE.search(message))


def retrieve_past_sessions(
    query: str,
    *,
    state_dir: Path,
    max_results: int = 5,
    excerpt_chars: int = 1200,
    max_transcript_turns: int = 2000,
) -> list[SessionRetrievalHit]:
    """Search daily summaries and the raw transcript for prior conversation hits."""
    terms = _keywords(query)
    if not terms and not _DATE_RE.search(query):
        return []

    hits: list[SessionRetrievalHit] = []
    hits.extend(_search_daily_summaries(query, terms, state_dir, excerpt_chars))
    hits.extend(
        _search_conversation_jsonl(
            query,
            terms,
            state_dir / "conversation.jsonl",
            excerpt_chars,
            max_transcript_turns,
        )
    )
    hits.sort(key=lambda h: (h.score, h.date), reverse=True)
    return hits[:max_results]


def format_past_session_block(hits: list[SessionRetrievalHit]) -> str:
    """Format prior-session hits as an instruction-bearing context block."""
    if not hits:
        return ""
    lines = [
        "### Retrieved Past Journaler Conversations",
        "_The user is referring to prior chat context. Treat these excerpts as primary "
        "conversation history, cite dates when useful, and answer from them before "
        "falling back to workspace notes._",
        "",
    ]
    for hit in hits:
        lines.append(
            f"**{hit.date or 'unknown date'}** "
            f"_{hit.source}, score {hit.score:.2f}_"
        )
        lines.append(f"> {hit.excerpt}")
        lines.append("")
    return "\n".join(lines)


def _search_daily_summaries(
    query: str,
    terms: set[str],
    state_dir: Path,
    excerpt_chars: int,
) -> list[SessionRetrievalHit]:
    summary_dir = state_dir / "daily_summaries"
    if not summary_dir.exists():
        return []
    hits: list[SessionRetrievalHit] = []
    for path in sorted(summary_dir.glob("*.md"), reverse=True):
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        score = _score(query, terms, f"{path.stem} {text}")
        if score <= 0:
            continue
        hits.append(
            SessionRetrievalHit(
                source="daily summary",
                date=_date_from_path(path),
                excerpt=_excerpt(text, terms, excerpt_chars),
                score=score + 0.15,
            )
        )
    return hits


def _search_conversation_jsonl(
    query: str,
    terms: set[str],
    path: Path,
    excerpt_chars: int,
    max_turns: int,
) -> list[SessionRetrievalHit]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    turns: list[dict[str, Any]] = []
    for line in lines[-max_turns:]:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        content = str(item.get("content") or "")
        if not content.strip():
            continue
        turns.append(
            {
                "timestamp": str(item.get("timestamp") or ""),
                "role": str(item.get("role") or ""),
                "content": content,
            }
        )

    hits: list[SessionRetrievalHit] = []
    for index, turn in enumerate(turns):
        score = _score(query, terms, f"{turn['timestamp']} {turn['content']}")
        if score <= 0:
            continue
        window = turns[max(0, index - 1): index + 2]
        text = "\n".join(
            f"{item['role'].upper()}: {item['content']}"
            for item in window
            if item["content"]
        )
        hits.append(
            SessionRetrievalHit(
                source="raw transcript",
                date=(turn["timestamp"] or "")[:10],
                excerpt=_excerpt(text, terms, excerpt_chars),
                score=score,
            )
        )
    return hits


def _score(query: str, terms: set[str], text: str) -> float:
    text_lower = text.lower()
    date_bonus = 0.5 if any(d in text_lower for d in _DATE_RE.findall(query)) else 0.0
    if not terms:
        return date_bonus
    matches = sum(1 for term in terms if term in text_lower)
    if matches == 0:
        return date_bonus
    return matches / max(1, len(terms)) + date_bonus


def _keywords(text: str) -> set[str]:
    return {
        token.lower()
        for token in _TOKEN_RE.findall(text)
        if token.lower() not in _STOPWORDS
    }


def _excerpt(text: str, terms: set[str], max_chars: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    lower = compact.lower()
    positions = [lower.find(term) for term in terms if lower.find(term) >= 0]
    center = min(positions) if positions else 0
    start = max(0, center - max_chars // 3)
    end = min(len(compact), start + max_chars)
    excerpt = compact[start:end].strip()
    if start > 0:
        excerpt = "..." + excerpt
    if end < len(compact):
        excerpt += "..."
    return excerpt


def _date_from_path(path: Path) -> str:
    try:
        return date.fromisoformat(path.stem).isoformat()
    except ValueError:
        return path.stem
