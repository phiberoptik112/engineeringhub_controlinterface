"""Proposal rendering and approval for Zettelkasten notes."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path

from engineering_hub.zettelkasten.linking import suggest_links
from engineering_hub.zettelkasten.models import (
    AtomicCandidate,
    ProposalBatch,
    ProposedAtomicNote,
    SuggestedLink,
)
from engineering_hub.zettelkasten.state import ZettelkastenState, new_batch_id

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def create_proposal_batch(
    candidates: list[AtomicCandidate],
    *,
    state: ZettelkastenState,
    memory_service: object | None = None,
    link_top_k: int = 5,
    link_threshold: float = 0.75,
) -> ProposalBatch:
    """Convert unprocessed candidates into a reviewable proposal batch."""
    batch_id = new_batch_id()
    notes: list[ProposedAtomicNote] = []
    for candidate in candidates:
        if state.is_processed(candidate.source_hash):
            continue
        note = _candidate_to_note(
            candidate,
            memory_service=memory_service,
            link_top_k=link_top_k,
            link_threshold=link_threshold,
        )
        notes.append(note)
        state.mark_proposed(candidate.source_hash, note.proposal_id)

    return ProposalBatch(
        batch_id=batch_id,
        created_at=datetime.now().isoformat(timespec="seconds"),
        notes=notes,
        source_count=len(candidates),
    )


def write_proposal_batch(
    batch: ProposalBatch,
    proposal_dir: Path,
    *,
    state: ZettelkastenState | None = None,
) -> tuple[Path, Path]:
    """Write JSON and org review files for a proposal batch."""
    proposal_dir = proposal_dir.expanduser().resolve()
    proposal_dir.mkdir(parents=True, exist_ok=True)
    json_path = proposal_dir / f"{batch.batch_id}.json"
    org_path = proposal_dir / f"{batch.batch_id}.org"
    json_path.write_text(batch.to_json() + "\n", encoding="utf-8")
    org_path.write_text(render_proposal_review(batch), encoding="utf-8")
    if state is not None:
        state.mark_batch(batch.batch_id, json_path)
    return json_path, org_path


def load_proposal_batch(path: Path) -> ProposalBatch:
    """Load a proposal batch JSON file."""
    return ProposalBatch.from_json(path.expanduser().read_text(encoding="utf-8"))


def apply_proposal_batch(
    batch: ProposalBatch,
    *,
    roam_dir: Path,
    state: ZettelkastenState | None = None,
    proposal_ids: set[str] | None = None,
) -> list[Path]:
    """Write approved proposed notes into org-roam and return created paths."""
    roam_dir = roam_dir.expanduser().resolve()
    roam_dir.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    selected = proposal_ids or {note.proposal_id for note in batch.notes}

    for note in batch.notes:
        if note.proposal_id not in selected:
            continue
        file_path = _write_note(roam_dir, note)
        created.append(file_path)
        if state is not None:
            state.mark_applied(note.source_hash, file_path)

    return created


def render_proposal_review(batch: ProposalBatch) -> str:
    """Render a human-reviewable org buffer for a proposal batch."""
    lines = [
        ":PROPERTIES:",
        f":BATCH_ID: {batch.batch_id}",
        ":END:",
        f"#+title: Zettelkasten proposals {batch.batch_id}",
        "#+filetags: :zettelkasten:proposals:",
        "",
        "* Review Instructions",
        "Edit the JSON sidecar before applying if any title, body, tag, or link needs changes.",
        "Apply with `engineering-hub zettel apply <proposal-json>` after review.",
        "",
    ]
    for note in batch.notes:
        lines.extend([
            f"* PROPOSED {note.title}",
            ":PROPERTIES:",
            f":PROPOSAL_ID: {note.proposal_id}",
            f":NODE_ID: {note.node_id}",
            f":SOURCE_HASH: {note.source_hash}",
            ":END:",
            "",
            f"Source: [[file:{note.source_path}][{Path(note.source_path).name}]]",
            f"Source heading: {note.source_heading or '(none)'}",
            f"Tags: {' '.join('#' + tag for tag in note.tags) if note.tags else '(none)'}",
            "",
            "** Draft",
            note.body.strip(),
            "",
            "** Suggested Links",
        ])
        if note.links:
            for link in note.links:
                lines.append(
                    f"- {link.category}: [[{link.target}][{link.title}]] "
                    f"({link.similarity:.0%}) - {link.reason}"
                )
        else:
            lines.append("- No links met the configured similarity threshold.")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_org_roam_note(note: ProposedAtomicNote) -> str:
    """Render a single approved proposal as an org-roam node."""
    created = _org_timestamp(datetime.fromisoformat(note.created_at))
    filetags = _format_filetags(note.tags)
    lines = [
        ":PROPERTIES:",
        f":ID:       {note.node_id}",
        ":END:",
        f"#+title: {note.title}",
    ]
    if filetags:
        lines.append(f"#+filetags: {filetags}")
    lines.extend([
        f"#+created: {created}",
        "",
        f"* {note.title}",
        "",
        note.body.strip(),
        "",
        "* Source",
        f"- [[file:{note.source_path}][{Path(note.source_path).name}]]",
    ])
    if note.source_heading:
        lines.append(f"- Heading: {note.source_heading}")
    lines.extend(["", "* Related"])
    lines.extend(_render_links(note.links, "Directly Related"))
    lines.extend(_render_links(note.links, "Tangentially Related"))
    return "\n".join(lines).rstrip() + "\n"


def _candidate_to_note(
    candidate: AtomicCandidate,
    *,
    memory_service: object | None,
    link_top_k: int,
    link_threshold: float,
) -> ProposedAtomicNote:
    title = _title_from_text(candidate.text)
    body = _body_from_candidate(candidate)
    links = suggest_links(
        body,
        memory_service,  # type: ignore[arg-type]
        top_k=link_top_k,
        threshold=link_threshold,
    )
    proposal_id = candidate.source_hash[:12]
    return ProposedAtomicNote(
        proposal_id=proposal_id,
        node_id=str(uuid.uuid4()),
        title=title,
        body=body,
        source_path=candidate.source_path,
        source_heading=candidate.heading,
        source_hash=candidate.source_hash,
        tags=_tags_from_candidate(candidate),
        links=links,
    )


def _title_from_text(text: str) -> str:
    cleaned = _strip_markers(text).strip()
    first = _SENTENCE_RE.split(cleaned, maxsplit=1)[0]
    first = re.sub(r"^[-*]\s+\[[ xX]\]\s*", "", first).strip()
    first = re.sub(r"^[-*]\s+", "", first).strip()
    first = first.replace("\n", " ")
    if len(first) > 80:
        first = first[:77].rstrip() + "..."
    return first or "Untitled atomic note"


def _body_from_candidate(candidate: AtomicCandidate) -> str:
    cleaned = _strip_markers(candidate.text).strip()
    return (
        "Core claim:\n"
        f"{cleaned}\n\n"
        "Context:\n"
        "This note was proposed from a marked daily-journal entry. Review for "
        "atomicity before applying."
    )


def _strip_markers(text: str) -> str:
    out = text
    for marker in DEFAULT_MARKER_TEXT:
        out = re.sub(re.escape(marker), "", out, flags=re.IGNORECASE)
    return " ".join(line.rstrip() for line in out.splitlines()).strip()


DEFAULT_MARKER_TEXT = ("#idea", "#extract", "TODO extract", "TODO: extract")


def _tags_from_candidate(candidate: AtomicCandidate) -> list[str]:
    tags = ["zettelkasten"]
    marker = candidate.marker.lstrip("#").lower().replace(":", "").replace(" ", "-")
    if marker:
        tags.append(marker)
    if candidate.heading:
        heading_tag = re.sub(r"[^a-z0-9]+", "-", candidate.heading.lower()).strip("-")
        if heading_tag:
            tags.append(heading_tag[:40])
    return list(dict.fromkeys(tags))


def _write_note(roam_dir: Path, note: ProposedAtomicNote) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    slug = _slug(note.title)
    filename = f"{timestamp}-{slug}.org"
    path = roam_dir / filename
    counter = 2
    while path.exists():
        path = roam_dir / f"{timestamp}-{slug}-{counter}.org"
        counter += 1
    path.write_text(render_org_roam_note(note), encoding="utf-8")
    return path


def _slug(title: str) -> str:
    slug = "".join(c if c.isalnum() or c == "-" else "-" for c in title.lower()).strip("-")
    slug = "-".join(filter(None, slug.split("-")))[:60]
    return slug or "atomic-note"


def _format_filetags(tags: list[str]) -> str:
    clean = [tag.strip().strip(":") for tag in tags if tag.strip()]
    return f":{':'.join(clean)}:" if clean else ""


def _org_timestamp(dt: datetime) -> str:
    return dt.strftime(f"[%Y-%m-%d {dt.strftime('%a')} %H:%M]")


def _render_links(links: list[SuggestedLink], category: str) -> list[str]:
    matching = [link for link in links if link.category == category]
    lines = [f"** {category}"]
    if not matching:
        lines.append("- None proposed.")
        return lines
    for link in matching:
        lines.append(
            f"- [[{link.target}][{link.title}]] ({link.similarity:.0%}) - {link.reason}"
        )
    return lines
