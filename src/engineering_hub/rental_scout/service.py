"""Core Bay Area Rental Scout operations.

Plain functions over the rental-scout workspace (criteria.yaml,
seen_listings.db, latest_digest.json). Every function takes an explicit
``workspace_dir`` so callers (MCP tools, agent tool handlers, tests) control
where state lives; use :func:`resolve_workspace` to derive it from Settings.

The actual scrape/score pipeline lives in the separate rental-scout project
(``main.py`` inside the workspace); :func:`run_scan` shells out to it and the
remaining functions read the artifacts it produces.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from engineering_hub.config.loader import find_config_file
from engineering_hub.config.settings import Settings

logger = logging.getLogger(__name__)

CRITERIA_FILE = "criteria.yaml"
DB_FILE = "seen_listings.db"
DIGEST_FILE = "latest_digest.json"
SCAN_TIMEOUT_SECONDS = 300


def _load_settings() -> Settings:
    config_path = find_config_file()
    return Settings.from_yaml(config_path) if config_path else Settings()


def resolve_workspace(
    workspace_dir: Path | str | None = None,
    settings: Settings | None = None,
) -> Path:
    """Resolve the rental-scout workspace directory, creating it if needed.

    Priority: explicit argument > ``rental_scout.workspace_dir`` from config.
    """
    if workspace_dir is None:
        settings = settings or _load_settings()
        workspace_dir = settings.rental_scout_workspace_dir
    path = Path(workspace_dir).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_python_executable(
    workspace_dir: Path,
    settings: Settings | None = None,
) -> Path:
    """Resolve the Python interpreter used to run the rental-scout pipeline.

    Priority: explicit ``rental_scout.python_path`` config >
    ``{workspace_dir}/.venv/bin/python`` when present > current process.
    """
    settings = settings or _load_settings()
    if settings.rental_scout_python_path is not None:
        return Path(settings.rental_scout_python_path).expanduser()

    venv_python = workspace_dir / ".venv" / "bin" / "python"
    if venv_python.is_file():
        return venv_python

    return Path(sys.executable)


def _build_scan_env(workspace_dir: Path) -> dict[str, str]:
    """Build subprocess env for the rental-scout pipeline."""
    env = os.environ.copy()
    env["RENTAL_SCOUT_WORKSPACE"] = str(workspace_dir.resolve())
    return env


# ---------------------------------------------------------------------------
# Criteria
# ---------------------------------------------------------------------------


def get_criteria(workspace_dir: Path) -> dict[str, Any]:
    """Return the current rental search criteria from criteria.yaml."""
    criteria_path = workspace_dir / CRITERIA_FILE
    if not criteria_path.exists():
        return {"error": "criteria.yaml not found", "path": str(criteria_path)}
    with open(criteria_path) as f:
        return yaml.safe_load(f) or {}


def update_criteria(
    workspace_dir: Path,
    cities: list[str] | None = None,
    price_min: int | None = None,
    price_max: int | None = None,
    bedrooms_min: int | None = None,
    bedrooms_max: int | None = None,
    amenities_required: list[str] | None = None,
    amenities_preferred: list[str] | None = None,
) -> dict[str, Any]:
    """Patch one or more fields in criteria.yaml and save.

    Only supplied arguments are modified; everything else is preserved.
    Returns the full updated criteria dict.
    """
    criteria_path = workspace_dir / CRITERIA_FILE

    if criteria_path.exists():
        with open(criteria_path) as f:
            criteria: dict[str, Any] = yaml.safe_load(f) or {}
    else:
        criteria = {}

    if cities is not None:
        criteria.setdefault("location", {})["cities"] = cities
    if price_min is not None:
        criteria.setdefault("price", {})["min"] = price_min
    if price_max is not None:
        criteria.setdefault("price", {})["max"] = price_max
    if bedrooms_min is not None:
        criteria.setdefault("property", {})["bedrooms_min"] = bedrooms_min
    if bedrooms_max is not None:
        criteria.setdefault("property", {})["bedrooms_max"] = bedrooms_max
    if amenities_required is not None:
        criteria["amenities_required"] = amenities_required
    if amenities_preferred is not None:
        criteria["amenities_preferred"] = amenities_preferred

    with open(criteria_path, "w") as f:
        yaml.dump(criteria, f, default_flow_style=False, sort_keys=False)

    logger.info("rental_scout: criteria updated → %s", criteria_path)
    return {"status": "updated", "criteria": criteria}


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


def run_scan(
    workspace_dir: Path,
    sources: list[str] | None = None,
    dry_run: bool = False,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Trigger a rental listing scan via the rental-scout pipeline subprocess.

    Results are written to latest_digest.json in the workspace; use
    :func:`get_top_matches` to read them.
    """
    settings = settings or _load_settings()
    main_script = workspace_dir / "main.py"

    if not main_script.exists():
        return {
            "error": (
                f"rental scout main.py not found at {main_script}. "
                "Clone the rental-scout project into your workspace first."
            )
        }

    python_executable = resolve_python_executable(workspace_dir, settings=settings)
    if not python_executable.is_file():
        return {
            "error": (
                f"Python interpreter not found at {python_executable}. "
                "Set rental_scout.python_path in config or create a .venv in the workspace."
            )
        }

    cmd = [
        str(python_executable),
        str(main_script),
        "--output-json",
        str(workspace_dir / DIGEST_FILE),
    ]
    if sources:
        cmd += ["--sources", ",".join(sources)]
    if dry_run:
        cmd.append("--dry-run")

    logger.info(
        "rental_scout: launching scan (python=%s, cwd=%s) → %s",
        python_executable,
        workspace_dir,
        " ".join(cmd),
    )
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=SCAN_TIMEOUT_SECONDS,
        cwd=str(workspace_dir),
        env=_build_scan_env(workspace_dir),
    )

    if result.returncode != 0:
        return {
            "status": "error",
            "returncode": result.returncode,
            "stderr": result.stderr[-2000:],
        }

    digest_path = workspace_dir / DIGEST_FILE
    if digest_path.exists():
        with open(digest_path) as f:
            digest = json.load(f)
        matches = digest.get("matches", [])
        return {
            "status": "ok",
            "scanned": digest.get("total_scanned", "?"),
            "new": digest.get("total_new", "?"),
            "matches": len(matches),
            "top_score": matches[0]["score"] if matches else None,
            "ran_at": digest.get("ran_at"),
        }

    return {"status": "ok", "stdout": result.stdout[-1000:]}


# ---------------------------------------------------------------------------
# Digest queries
# ---------------------------------------------------------------------------


def get_top_matches(
    workspace_dir: Path,
    limit: int = 10,
    min_score: int = 6,
    city_filter: str | None = None,
) -> dict[str, Any]:
    """Return top rental matches from the most recent scan digest."""
    digest_path = workspace_dir / DIGEST_FILE

    if not digest_path.exists():
        return {
            "error": "No digest found. Run run_scan() first.",
            "path": str(digest_path),
        }

    with open(digest_path) as f:
        digest = json.load(f)

    matches: list[dict[str, Any]] = digest.get("matches", [])

    filtered = [
        m
        for m in matches
        if m.get("score", 0) >= min_score
        and (city_filter is None or city_filter.lower() in m.get("city", "").lower())
    ]
    filtered.sort(key=lambda x: x.get("score", 0), reverse=True)
    filtered = filtered[:limit]

    return {
        "ran_at": digest.get("ran_at"),
        "total_matches": len(matches),
        "returned": len(filtered),
        "listings": filtered,
    }


def get_listing_stats(workspace_dir: Path) -> dict[str, Any]:
    """Return summary statistics from the deduplication database."""
    db_path = workspace_dir / DB_FILE

    if not db_path.exists():
        return {"error": "No database found yet. Run a scan first."}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    stats: dict[str, Any] = {}

    cur.execute("SELECT COUNT(*) as total FROM seen_listings")
    row = cur.fetchone()
    stats["total_seen"] = row["total"] if row else 0

    cur.execute(
        "SELECT source, COUNT(*) as cnt FROM seen_listings "
        "GROUP BY source ORDER BY cnt DESC"
    )
    stats["by_source"] = {r["source"]: r["cnt"] for r in cur.fetchall()}

    cur.execute("SELECT MAX(seen_at) as last FROM seen_listings")
    row = cur.fetchone()
    stats["last_seen_at"] = row["last"] if row else None

    conn.close()

    digest_path = workspace_dir / DIGEST_FILE
    if digest_path.exists():
        with open(digest_path) as f:
            digest = json.load(f)
        stats["last_scan"] = {
            "ran_at": digest.get("ran_at"),
            "total_scanned": digest.get("total_scanned"),
            "total_new": digest.get("total_new"),
            "matches": len(digest.get("matches", [])),
        }

    return stats


def clear_seen_listings(workspace_dir: Path, confirm: bool = False) -> dict[str, Any]:
    """Clear the deduplication database so all listings are re-evaluated.

    Dry-run unless ``confirm`` is True.
    """
    db_path = workspace_dir / DB_FILE

    if not db_path.exists():
        return {"status": "nothing to clear", "db_path": str(db_path)}

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM seen_listings")
    count = cur.fetchone()[0]

    if not confirm:
        conn.close()
        return {
            "status": "dry_run",
            "would_delete": count,
            "message": "Pass confirm=True to actually clear.",
        }

    cur.execute("DELETE FROM seen_listings")
    conn.commit()
    conn.close()

    logger.info("rental_scout: cleared %d seen listings", count)
    return {"status": "cleared", "deleted": count}


# ---------------------------------------------------------------------------
# Journal bridge
# ---------------------------------------------------------------------------


def add_journal_task(
    task_description: str,
    org_journal_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Write an @rental-scout task to today's org-roam journal.

    Bridges the Journaler's conversational interface to the Orchestrator's
    file-watching pipeline. The journal directory defaults to
    ``journal.org_journal_dir`` from config.
    """
    if org_journal_dir is not None:
        journal_dir = Path(org_journal_dir).expanduser()
    else:
        journal_dir = _load_settings().org_journal_dir.expanduser()
    journal_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    journal_file = journal_dir / f"{today}.org"

    heading = "* Overnight Agent Tasks"
    task_line = f"- [ ] @rental-scout: {task_description}\n"

    if journal_file.exists():
        content = journal_file.read_text()
    else:
        content = f"#+title: {today}\n#+filetags: :journal:\n\n"

    if heading not in content:
        content += f"\n{heading}\n"

    content += task_line
    journal_file.write_text(content)

    logger.info("rental_scout: wrote task to %s", journal_file)
    return {
        "status": "written",
        "file": str(journal_file),
        "task": task_line.strip(),
    }


# ---------------------------------------------------------------------------
# Org formatting
# ---------------------------------------------------------------------------


def format_digest_for_org(
    workspace_dir: Path,
    limit: int = 5,
    min_score: int = 6,
) -> str:
    """Return today's top matches as org-mode text for journal/note appends."""
    result = get_top_matches(workspace_dir, limit=limit, min_score=min_score)
    if "error" in result:
        return f"** Rental Scout Error\n{result['error']}\n"

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [
        f"** Rental Scout Digest — {today}",
        f"   {result['returned']} matches (from {result['total_matches']} total scored)\n",
    ]

    for listing in result["listings"]:
        score = listing.get("score", "?")
        price = listing.get("price", "?")
        beds = listing.get("beds", "?")
        city = listing.get("city", "?")
        hood = listing.get("neighborhood", "")
        url = listing.get("url", "")
        summary = listing.get("summary", "")
        pros = listing.get("pros", [])
        cons = listing.get("cons", [])
        red_flags = listing.get("red_flags", [])

        location = f"{hood}, {city}" if hood else city

        lines.append(f"*** ⭐ {score}/10 — ${price}/mo | {beds}BR | {location}")
        lines.append(f"    {summary}")
        if pros:
            lines.append(f"    ✅ {', '.join(pros)}")
        if cons:
            lines.append(f"    ➖ {', '.join(cons)}")
        if red_flags:
            lines.append(f"    🚩 {', '.join(red_flags)}")
        lines.append(f"    🔗 [[{url}][View listing]]")
        lines.append("")

    return "\n".join(lines)
