"""Marker-based journal mining for atomic note candidates."""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

from engineering_hub.zettelkasten.models import AtomicCandidate

DEFAULT_MARKERS = ("#idea", "#extract", "TODO extract", "TODO: extract")

_HEADING_RE = re.compile(r"^(\*+)\s+(?:(?:TODO|DONE|WAITING|CANCELLED)\s+)?(.+?)\s*$")


def detect_candidates(
    journal_dir: Path,
    *,
    markers: list[str] | tuple[str, ...] = DEFAULT_MARKERS,
    lookback_days: int = 7,
) -> list[AtomicCandidate]:
    """Find marker-tagged journal spans that should be proposed as atomic notes."""
    selected = _selected_journal_files(journal_dir, lookback_days)
    candidates: list[AtomicCandidate] = []
    seen_hashes: set[str] = set()

    for path in selected:
        candidates.extend(
            _detect_in_file(path=path, markers=markers, seen_hashes=seen_hashes)
        )

    return candidates


def _selected_journal_files(journal_dir: Path, lookback_days: int) -> list[Path]:
    journal_dir = journal_dir.expanduser().resolve()
    if not journal_dir.exists():
        return []

    earliest = date.today() - timedelta(days=max(0, lookback_days - 1))
    dated: list[tuple[date, Path]] = []
    for path in journal_dir.glob("*.org"):
        try:
            file_date = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if file_date >= earliest:
            dated.append((file_date, path.resolve()))

    dated.sort(key=lambda item: item[0])
    return [path for _, path in dated]


def _detect_in_file(
    *,
    path: Path,
    markers: list[str] | tuple[str, ...],
    seen_hashes: set[str],
) -> list[AtomicCandidate]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []

    candidates: list[AtomicCandidate] = []
    heading = ""
    headings_by_line: dict[int, str] = {}
    for idx, line in enumerate(lines):
        match = _HEADING_RE.match(line.strip())
        if match:
            heading = match.group(2).strip()
        headings_by_line[idx] = heading

    for idx, line in enumerate(lines):
        marker = _matching_marker(line, markers)
        if marker is None:
            continue

        start, end = _paragraph_bounds(lines, idx)
        text = "\n".join(lines[start:end + 1]).strip()
        if not text:
            continue

        candidate = AtomicCandidate.build(
            source_path=path,
            journal_date=path.stem,
            heading=headings_by_line.get(idx, ""),
            marker=marker,
            text=text,
            start_line=start + 1,
            end_line=end + 1,
        )
        if candidate.source_hash in seen_hashes:
            continue
        seen_hashes.add(candidate.source_hash)
        candidates.append(candidate)

    return candidates


def _matching_marker(line: str, markers: list[str] | tuple[str, ...]) -> str | None:
    lower = line.lower()
    for marker in markers:
        if marker.lower() in lower:
            return marker
    return None


def _paragraph_bounds(lines: list[str], marker_idx: int) -> tuple[int, int]:
    start = marker_idx
    while start > 0 and lines[start - 1].strip():
        if _HEADING_RE.match(lines[start - 1].strip()):
            break
        start -= 1

    end = marker_idx
    while end + 1 < len(lines) and lines[end + 1].strip():
        if _HEADING_RE.match(lines[end + 1].strip()):
            break
        end += 1

    return start, end
