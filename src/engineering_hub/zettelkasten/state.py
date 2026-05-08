"""Persistent state for Zettelkasten proposal deduplication."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class ZettelkastenState:
    """Tracks source spans that have already produced proposals or notes."""

    processed_hashes: dict[str, str] = field(default_factory=dict)
    proposal_batches: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "ZettelkastenState":
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        return cls(
            processed_hashes=dict(data.get("processed_hashes", {})),
            proposal_batches=dict(data.get("proposal_batches", {})),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "processed_hashes": self.processed_hashes,
            "proposal_batches": self.proposal_batches,
        }
        path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    def is_processed(self, source_hash: str) -> bool:
        return source_hash in self.processed_hashes

    def mark_proposed(self, source_hash: str, proposal_id: str) -> None:
        self.processed_hashes[source_hash] = proposal_id

    def mark_batch(self, batch_id: str, proposal_path: Path) -> None:
        self.proposal_batches[batch_id] = str(proposal_path)

    def mark_applied(self, source_hash: str, note_path: Path) -> None:
        self.processed_hashes[source_hash] = f"applied:{note_path}"


def default_state_path(workspace_dir: Path) -> Path:
    timestamp_free_dir = workspace_dir / ".journaler"
    return timestamp_free_dir / "zettelkasten_state.json"


def new_batch_id() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S")
