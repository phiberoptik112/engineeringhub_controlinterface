"""Data models for the Zettelkasten proposal workflow."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


def stable_hash(text: str) -> str:
    """Return a stable hash for a normalized source span."""
    normalized = " ".join(text.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass
class AtomicCandidate:
    """A journal span that may contain one atomic permanent-note idea."""

    source_path: str
    journal_date: str
    heading: str
    marker: str
    text: str
    start_line: int
    end_line: int
    source_hash: str

    @classmethod
    def build(
        cls,
        *,
        source_path: Path,
        journal_date: str,
        heading: str,
        marker: str,
        text: str,
        start_line: int,
        end_line: int,
    ) -> "AtomicCandidate":
        return cls(
            source_path=str(source_path),
            journal_date=journal_date,
            heading=heading,
            marker=marker,
            text=text.strip(),
            start_line=start_line,
            end_line=end_line,
            source_hash=stable_hash(text),
        )


@dataclass
class SuggestedLink:
    """A possible org-roam relationship for a proposed note."""

    title: str
    target: str
    similarity: float
    reason: str
    category: str = "Tangentially Related"


@dataclass
class ProposedAtomicNote:
    """A permanent-note proposal awaiting human approval."""

    proposal_id: str
    node_id: str
    title: str
    body: str
    source_path: str
    source_heading: str
    source_hash: str
    tags: list[str] = field(default_factory=list)
    links: list[SuggestedLink] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    status: str = "proposed"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProposedAtomicNote":
        links = [
            link if isinstance(link, SuggestedLink) else SuggestedLink(**link)
            for link in data.get("links", [])
        ]
        return cls(
            proposal_id=data["proposal_id"],
            node_id=data["node_id"],
            title=data["title"],
            body=data["body"],
            source_path=data["source_path"],
            source_heading=data.get("source_heading", ""),
            source_hash=data["source_hash"],
            tags=list(data.get("tags", [])),
            links=links,
            created_at=data.get("created_at", datetime.now().isoformat(timespec="seconds")),
            status=data.get("status", "proposed"),
        )


@dataclass
class ProposalBatch:
    """A reviewable batch of proposed org-roam notes."""

    batch_id: str
    created_at: str
    notes: list[ProposedAtomicNote]
    source_count: int = 0

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> "ProposalBatch":
        data = json.loads(raw)
        notes = [ProposedAtomicNote.from_dict(note) for note in data.get("notes", [])]
        return cls(
            batch_id=data["batch_id"],
            created_at=data["created_at"],
            notes=notes,
            source_count=data.get("source_count", len(notes)),
        )
