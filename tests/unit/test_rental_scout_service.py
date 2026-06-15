"""Unit tests for the rental scout service and its integration points."""

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from engineering_hub.agents.registry import DEFAULT_AGENT_CONFIGS
from engineering_hub.agents.tools import TOOL_REGISTRY, ToolContext, resolve_tools
from engineering_hub.config.settings import Settings
from engineering_hub.core.constants import AGENT_PROMPT_FILES, AgentType
from engineering_hub.journaler.delegator import _AGENT_ALIASES, _load_skills
from engineering_hub.rental_scout import service

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path / "rental_workspace"


@pytest.fixture
def populated_workspace(workspace: Path) -> Path:
    workspace.mkdir(parents=True)
    digest = {
        "ran_at": "2026-06-09T15:00:00Z",
        "total_scanned": 60,
        "total_new": 12,
        "matches": [
            {
                "url": "https://example.com/oak-1",
                "score": 9,
                "price": 3200,
                "beds": 2,
                "city": "Oakland",
                "neighborhood": "Rockridge",
                "summary": "Bright 2BR near BART.",
                "pros": ["in-unit laundry"],
                "cons": ["street parking"],
                "red_flags": [],
            },
            {
                "url": "https://example.com/sf-1",
                "score": 7,
                "price": 2950,
                "beds": 1,
                "city": "San Francisco",
                "neighborhood": "Inner Sunset",
                "summary": "1BR near Golden Gate Park.",
                "pros": ["allows cats"],
                "cons": ["no parking"],
                "red_flags": ["no photos"],
            },
            {
                "url": "https://example.com/low-1",
                "score": 4,
                "price": 2400,
                "beds": 1,
                "city": "Oakland",
                "neighborhood": "",
                "summary": "Below threshold.",
                "pros": [],
                "cons": [],
                "red_flags": [],
            },
        ],
    }
    (workspace / service.DIGEST_FILE).write_text(json.dumps(digest))

    conn = sqlite3.connect(workspace / service.DB_FILE)
    conn.execute(
        "CREATE TABLE seen_listings (id TEXT, source TEXT, seen_at TEXT)"
    )
    conn.executemany(
        "INSERT INTO seen_listings VALUES (?, ?, ?)",
        [
            ("a", "craigslist", "2026-06-08T10:00:00Z"),
            ("b", "craigslist", "2026-06-09T10:00:00Z"),
            ("c", "zumper", "2026-06-09T11:00:00Z"),
        ],
    )
    conn.commit()
    conn.close()
    return workspace


# ---------------------------------------------------------------------------
# Criteria
# ---------------------------------------------------------------------------


class TestCriteria:
    def test_get_criteria_missing_file(self, workspace: Path):
        workspace.mkdir(parents=True)
        result = service.get_criteria(workspace)
        assert "error" in result

    def test_update_then_get_round_trip(self, workspace: Path):
        workspace.mkdir(parents=True)
        result = service.update_criteria(
            workspace,
            cities=["Oakland", "Walnut Creek"],
            price_max=3800,
            amenities_required=["in_unit_laundry"],
        )
        assert result["status"] == "updated"

        criteria = service.get_criteria(workspace)
        assert criteria["location"]["cities"] == ["Oakland", "Walnut Creek"]
        assert criteria["price"]["max"] == 3800
        assert criteria["amenities_required"] == ["in_unit_laundry"]

    def test_partial_update_preserves_existing(self, workspace: Path):
        workspace.mkdir(parents=True)
        service.update_criteria(workspace, price_min=2500, price_max=4500)
        service.update_criteria(workspace, price_max=3800)

        criteria = service.get_criteria(workspace)
        assert criteria["price"]["min"] == 2500
        assert criteria["price"]["max"] == 3800


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


class TestRunScan:
    def test_missing_pipeline_returns_error(self, workspace: Path):
        workspace.mkdir(parents=True)
        result = service.run_scan(workspace)
        assert "error" in result
        assert "main.py not found" in result["error"]

    def test_resolve_python_uses_configured_path(self, workspace: Path):
        workspace.mkdir(parents=True)
        configured = workspace / "custom-python"
        configured.write_text("")
        settings = Settings(rental_scout_python_path=configured)
        assert service.resolve_python_executable(workspace, settings=settings) == configured

    def test_resolve_python_prefers_workspace_venv(self, workspace: Path):
        workspace.mkdir(parents=True)
        venv_python = workspace / ".venv" / "bin" / "python"
        venv_python.parent.mkdir(parents=True)
        venv_python.write_text("")
        assert service.resolve_python_executable(workspace) == venv_python

    def test_resolve_python_falls_back_to_current_interpreter(self, workspace: Path):
        workspace.mkdir(parents=True)
        assert service.resolve_python_executable(workspace) == Path(sys.executable)

    def test_run_scan_uses_workspace_venv_and_cwd(self, workspace: Path, monkeypatch):
        workspace.mkdir(parents=True)
        (workspace / "main.py").write_text("# stub\n")
        venv_python = workspace / ".venv" / "bin" / "python"
        venv_python.parent.mkdir(parents=True)
        venv_python.write_text("")

        captured: dict[str, object] = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return MagicMock(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(service.subprocess, "run", fake_run)

        result = service.run_scan(workspace, dry_run=True)

        assert result["status"] == "ok"
        assert captured["cmd"][0] == str(venv_python)
        assert captured["cmd"][1] == str(workspace / "main.py")
        assert captured["kwargs"]["cwd"] == str(workspace)
        assert captured["kwargs"]["env"]["RENTAL_SCOUT_WORKSPACE"] == str(
            workspace.resolve()
        )


# ---------------------------------------------------------------------------
# Digest queries
# ---------------------------------------------------------------------------


class TestTopMatches:
    def test_missing_digest_returns_error(self, workspace: Path):
        workspace.mkdir(parents=True)
        result = service.get_top_matches(workspace)
        assert "error" in result

    def test_filters_by_min_score(self, populated_workspace: Path):
        result = service.get_top_matches(populated_workspace, min_score=6)
        assert result["total_matches"] == 3
        assert result["returned"] == 2
        scores = [listing["score"] for listing in result["listings"]]
        assert scores == sorted(scores, reverse=True)

    def test_filters_by_city(self, populated_workspace: Path):
        result = service.get_top_matches(populated_workspace, city_filter="Oakland")
        assert result["returned"] == 1
        assert result["listings"][0]["city"] == "Oakland"

    def test_respects_limit(self, populated_workspace: Path):
        result = service.get_top_matches(populated_workspace, limit=1, min_score=1)
        assert result["returned"] == 1
        assert result["listings"][0]["score"] == 9


class TestListingStats:
    def test_missing_db_returns_error(self, workspace: Path):
        workspace.mkdir(parents=True)
        result = service.get_listing_stats(workspace)
        assert "error" in result

    def test_stats_from_db_and_digest(self, populated_workspace: Path):
        stats = service.get_listing_stats(populated_workspace)
        assert stats["total_seen"] == 3
        assert stats["by_source"] == {"craigslist": 2, "zumper": 1}
        assert stats["last_seen_at"] == "2026-06-09T11:00:00Z"
        assert stats["last_scan"]["matches"] == 3


class TestClearSeenListings:
    def test_dry_run_does_not_delete(self, populated_workspace: Path):
        result = service.clear_seen_listings(populated_workspace, confirm=False)
        assert result["status"] == "dry_run"
        assert result["would_delete"] == 3
        assert service.get_listing_stats(populated_workspace)["total_seen"] == 3

    def test_confirm_deletes(self, populated_workspace: Path):
        result = service.clear_seen_listings(populated_workspace, confirm=True)
        assert result == {"status": "cleared", "deleted": 3}
        assert service.get_listing_stats(populated_workspace)["total_seen"] == 0


# ---------------------------------------------------------------------------
# Journal bridge / org formatting
# ---------------------------------------------------------------------------


class TestAddJournalTask:
    def test_writes_task_to_new_journal_file(self, tmp_path: Path):
        journal_dir = tmp_path / "journal"
        result = service.add_journal_task(
            "run rental scan for Oakland", org_journal_dir=journal_dir
        )
        assert result["status"] == "written"
        content = Path(result["file"]).read_text()
        assert "* Overnight Agent Tasks" in content
        assert "@rental-scout: run rental scan for Oakland" in content
        assert "[[mcp://rental-scout/run_scan]]" not in content

    def test_appends_without_duplicating_heading(self, tmp_path: Path):
        journal_dir = tmp_path / "journal"
        service.add_journal_task("task one", org_journal_dir=journal_dir)
        result = service.add_journal_task("task two", org_journal_dir=journal_dir)
        content = Path(result["file"]).read_text()
        assert content.count("* Overnight Agent Tasks") == 1
        assert "task one" in content and "task two" in content


class TestFormatDigestForOrg:
    def test_error_when_no_digest(self, workspace: Path):
        workspace.mkdir(parents=True)
        text = service.format_digest_for_org(workspace)
        assert text.startswith("** Rental Scout Error")

    def test_org_output_contains_listings(self, populated_workspace: Path):
        text = service.format_digest_for_org(populated_workspace)
        assert "** Rental Scout Digest" in text
        assert "$3200/mo" in text
        assert "Rockridge, Oakland" in text
        assert "no photos" in text  # red flag surfaced


# ---------------------------------------------------------------------------
# Integration: tool registry, agent config, skill YAML
# ---------------------------------------------------------------------------


RENTAL_TOOL_NAMES = [
    "rental_get_criteria",
    "rental_update_criteria",
    "rental_run_scan",
    "rental_get_top_matches",
    "rental_get_listing_stats",
    "rental_format_digest_for_org",
    "rental_clear_seen_listings",
    "rental_add_journal_task",
]


class TestIntegration:
    def test_rental_tools_registered(self):
        for name in RENTAL_TOOL_NAMES:
            assert name in TOOL_REGISTRY, f"{name} missing from TOOL_REGISTRY"
            assert TOOL_REGISTRY[name].schema["name"] == name

    def test_agent_config_tools_all_resolve(self):
        cfg = DEFAULT_AGENT_CONFIGS[AgentType.RENTAL_SCOUT]
        assert len(resolve_tools(cfg.tools)) == len(cfg.tools)

    def test_prompt_file_exists(self):
        prompt = REPO_ROOT / "prompts" / AGENT_PROMPT_FILES[AgentType.RENTAL_SCOUT]
        assert prompt.exists()

    def test_aliases_resolve_to_rental_scout(self):
        for alias in ("rental-scout", "rental", "scout", "housing"):
            assert _AGENT_ALIASES[alias] == "rental-scout"

    def test_skill_yaml_loads(self):
        skills = _load_skills(REPO_ROOT / "skills")
        assert "rental-scout" in skills
        skill = skills["rental-scout"]
        assert skill.agent_type == "rental-scout"
        assert skill.when_to_use
        assert skill.invocation_examples

    def test_tool_handler_round_trip(self, populated_workspace: Path, monkeypatch):
        monkeypatch.setattr(
            "engineering_hub.rental_scout.service.resolve_workspace",
            lambda *a, **k: populated_workspace,
        )
        ctx = ToolContext(
            corpus_service=None, memory_service=None, output_dir=populated_workspace
        )
        raw = TOOL_REGISTRY["rental_get_top_matches"].handler(
            {"min_score": 6, "city_filter": "Oakland"}, ctx
        )
        result = json.loads(raw)
        assert result["returned"] == 1
        assert result["listings"][0]["city"] == "Oakland"

    def test_clear_seen_listings_handler(self, populated_workspace: Path, monkeypatch):
        monkeypatch.setattr(
            "engineering_hub.rental_scout.service.resolve_workspace",
            lambda *a, **k: populated_workspace,
        )
        ctx = ToolContext(
            corpus_service=None, memory_service=None, output_dir=populated_workspace
        )
        dry = json.loads(
            TOOL_REGISTRY["rental_clear_seen_listings"].handler({}, ctx)
        )
        assert dry["status"] == "dry_run"
        assert dry["would_delete"] == 3

        cleared = json.loads(
            TOOL_REGISTRY["rental_clear_seen_listings"].handler({"confirm": True}, ctx)
        )
        assert cleared == {"status": "cleared", "deleted": 3}

    def test_add_journal_task_handler(self, tmp_path: Path, monkeypatch):
        journal_dir = tmp_path / "journal"
        ctx = ToolContext(
            corpus_service=None, memory_service=None, output_dir=tmp_path
        )
        raw = TOOL_REGISTRY["rental_add_journal_task"].handler(
            {
                "task_description": "run scan for Oakland",
                "org_journal_dir": str(journal_dir),
            },
            ctx,
        )
        result = json.loads(raw)
        assert result["status"] == "written"
        assert "@rental-scout: run scan for Oakland" in Path(result["file"]).read_text()
