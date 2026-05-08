from __future__ import annotations

from pathlib import Path

from engineering_hub.zettelkasten.detector import detect_candidates
from engineering_hub.zettelkasten.models import ProposalBatch, SuggestedLink
from engineering_hub.zettelkasten.proposals import (
    apply_proposal_batch,
    create_proposal_batch,
    render_org_roam_note,
)
from engineering_hub.zettelkasten.state import ZettelkastenState


def test_detect_candidates_from_marked_journal_entry(tmp_path: Path) -> None:
    journal_dir = tmp_path / "journals"
    journal_dir.mkdir()
    journal = journal_dir / "2099-01-01.org"
    journal.write_text(
        "#+title: 2099-01-01\n\n"
        "* Notes\n"
        "The mass-air-mass resonance issue matters here. #idea\n"
        "It affects operable partition isolation below the rating band.\n\n"
        "* Other\n"
        "Unmarked text.\n",
        encoding="utf-8",
    )

    candidates = detect_candidates(journal_dir, lookback_days=36500)

    assert len(candidates) == 1
    assert candidates[0].heading == "Notes"
    assert candidates[0].marker == "#idea"
    assert "operable partition" in candidates[0].text
    assert candidates[0].start_line == 4


def test_create_proposal_batch_skips_processed_candidates(tmp_path: Path) -> None:
    journal_dir = tmp_path / "journals"
    journal_dir.mkdir()
    journal = journal_dir / "2099-01-01.org"
    journal.write_text("* Notes\nA durable thought. #extract\n", encoding="utf-8")
    candidates = detect_candidates(journal_dir, lookback_days=36500)
    state = ZettelkastenState()
    state.mark_proposed(candidates[0].source_hash, "existing")

    batch = create_proposal_batch(candidates, state=state)

    assert batch.source_count == 1
    assert batch.notes == []


def test_render_org_roam_note_includes_source_and_links() -> None:
    note = ProposalBatch.from_json(
        """
        {
          "batch_id": "b",
          "created_at": "2026-05-06T09:00:00",
          "source_count": 1,
          "notes": [
            {
              "proposal_id": "abc",
              "node_id": "node-1",
              "title": "Air gaps change isolation",
              "body": "Core claim:\\nAir gaps change isolation.",
              "source_path": "/tmp/journals/2026-05-06.org",
              "source_heading": "Notes",
              "source_hash": "hash",
              "tags": ["zettelkasten", "acoustics"],
              "links": [],
              "created_at": "2026-05-06T09:00:00",
              "status": "proposed"
            }
          ]
        }
        """
    ).notes[0]
    note.links.append(
        SuggestedLink(
            title="Flanking paths",
            target="id:existing",
            similarity=0.91,
            reason="Related acoustics concept.",
            category="Directly Related",
        )
    )

    rendered = render_org_roam_note(note)

    assert ":ID:       node-1" in rendered
    assert "#+title: Air gaps change isolation" in rendered
    assert "[[file:/tmp/journals/2026-05-06.org][2026-05-06.org]]" in rendered
    assert "[[id:existing][Flanking paths]]" in rendered


def test_apply_proposal_batch_writes_approved_note(tmp_path: Path) -> None:
    journal_dir = tmp_path / "journals"
    journal_dir.mkdir()
    journal = journal_dir / "2099-01-01.org"
    journal.write_text("* Notes\nA durable thought. #idea\n", encoding="utf-8")
    candidates = detect_candidates(journal_dir, lookback_days=36500)
    state = ZettelkastenState()
    batch = create_proposal_batch(candidates, state=state)
    roam_dir = tmp_path / "roam"

    created = apply_proposal_batch(batch, roam_dir=roam_dir, state=state)

    assert len(created) == 1
    assert created[0].exists()
    assert ":zettelkasten:" in created[0].read_text(encoding="utf-8")
    assert state.processed_hashes[candidates[0].source_hash].startswith("applied:")
