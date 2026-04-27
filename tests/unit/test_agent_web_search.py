"""Tests for local-first /agent web search context injection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from engineering_hub.config.settings import Settings
from engineering_hub.journaler.chat_server import _handle_agent_command
from engineering_hub.journaler.engine import ConversationEngine, DelegateContextResult
from engineering_hub.search import (
    SearchResult,
    SearxngSearchProvider,
    format_search_results_for_context,
)


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


def test_searxng_provider_normalizes_results(monkeypatch: Any) -> None:
    calls: list[dict[str, Any]] = []

    def fake_get(url: str, params: dict[str, str], timeout: float) -> _Response:
        calls.append({"url": url, "params": params, "timeout": timeout})
        return _Response(
            {
                "results": [
                    {
                        "title": "Result A",
                        "url": "https://example.com/a",
                        "content": "Snippet A",
                        "engine": "duckduckgo",
                        "score": 1.5,
                    },
                    {"title": "missing url"},
                    {
                        "title": "Result B",
                        "url": "https://example.com/b",
                        "content": "Snippet B",
                    },
                ]
            }
        )

    monkeypatch.setattr("engineering_hub.search.providers.requests.get", fake_get)

    provider = SearxngSearchProvider("http://localhost:8080/", timeout_seconds=2.5)
    results = provider.search("query text", max_results=2)

    assert calls == [
        {
            "url": "http://localhost:8080/search",
            "params": {"q": "query text", "format": "json"},
            "timeout": 2.5,
        }
    ]
    assert [r.title for r in results] == ["Result A", "Result B"]
    assert results[0].engine == "duckduckgo"
    assert results[0].score == 1.5


def test_format_search_results_for_context_is_bounded() -> None:
    results = [
        SearchResult(
            title="Long result",
            url="https://example.com",
            snippet="x" * 500,
            engine="search",
        )
    ]

    block = format_search_results_for_context(
        results,
        query="current acoustic standard",
        max_chars=450,
    )

    assert "## Web search results (SearXNG)" in block
    assert "https://example.com" in block
    assert "truncated" in block.lower()
    assert len(block) < 520


def test_delegate_context_injects_web_results(tmp_path: Path) -> None:
    class Provider:
        def search(self, query: str, max_results: int) -> list[SearchResult]:
            assert query == "current FAA vertiport noise guidance"
            assert max_results == 3
            return [
                SearchResult(
                    title="FAA guidance",
                    url="https://faa.example/guidance",
                    snippet="Noise guidance excerpt",
                )
            ]

    engine = ConversationEngine(
        backend=object(),
        system_prompt="system",
        log_dir=tmp_path,
        web_search_provider=Provider(),
        web_search_enabled=True,
        web_search_max_results=3,
    )

    result = engine.build_delegate_context_result("current FAA vertiport noise guidance")

    assert result.web_search_attempted is True
    assert result.web_search_succeeded is True
    assert "## Web search results (SearXNG)" in result.context
    assert "https://faa.example/guidance" in result.context


def test_agent_web_search_settings_from_yaml(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        """
agent_web_search:
  enabled: true
  provider: searxng
  searxng_url: "http://localhost:9999"
  max_results: 7
  max_chars: 9000
  timeout_seconds: 3.5
  anthropic_backup_enabled: true
  anthropic_tool_version: web_search_20260209
  anthropic_max_uses: 4
""",
        encoding="utf-8",
    )

    settings = Settings.from_yaml(config)

    assert settings.agent_web_search_enabled is True
    assert settings.agent_web_search_provider == "searxng"
    assert settings.agent_web_search_searxng_url == "http://localhost:9999"
    assert settings.agent_web_search_max_results == 7
    assert settings.agent_web_search_max_chars == 9000
    assert settings.agent_web_search_timeout_seconds == 3.5
    assert settings.agent_web_search_anthropic_backup_enabled is True
    assert settings.agent_web_search_anthropic_tool_version == "web_search_20260209"
    assert settings.agent_web_search_anthropic_max_uses == 4


def test_agent_web_flag_failure_stops_without_backup(tmp_path: Path) -> None:
    class Context:
        journal_dir = tmp_path

    class Engine:
        web_search_anthropic_backup_enabled = False
        web_search_anthropic_tool_version = "web_search_20250305"
        web_search_anthropic_max_uses = 3

        def build_delegate_context_result(
            self,
            task_description: str,
            *,
            web_search_enabled: bool | None = None,
            web_search_required: bool = False,
        ) -> DelegateContextResult:
            assert task_description == "find current guidance"
            assert web_search_enabled is True
            assert web_search_required is True
            return DelegateContextResult(
                context="",
                web_search_attempted=True,
                web_search_succeeded=False,
                web_search_error="connection refused",
            )

    class Delegator:
        def will_use_anthropic_backend(self, backend: str) -> bool:
            return False

    message = "/agent research find current guidance --backend mlx --web"

    response = _handle_agent_command(message, Delegator(), Context(), engine=Engine())

    assert "local SearXNG retrieval failed" in response
    assert "connection refused" in response


def test_agent_web_flag_uses_anthropic_backup_when_available(tmp_path: Path) -> None:
    class Context:
        journal_dir = tmp_path

    class Engine:
        web_search_anthropic_backup_enabled = True
        web_search_anthropic_tool_version = "web_search_20260209"
        web_search_anthropic_max_uses = 2

        def build_delegate_context_result(
            self,
            task_description: str,
            *,
            web_search_enabled: bool | None = None,
            web_search_required: bool = False,
        ) -> DelegateContextResult:
            return DelegateContextResult(
                context="local failure note",
                web_search_attempted=True,
                web_search_succeeded=False,
                web_search_error="unavailable",
            )

    class Delegator:
        def __init__(self) -> None:
            self.kwargs: dict[str, Any] = {}

        def will_use_anthropic_backend(self, backend: str) -> bool:
            return backend == "claude"

        def delegate(self, **kwargs: Any) -> str:
            self.kwargs = kwargs
            return "delegated"

    delegator = Delegator()
    message = "/agent research find current guidance --backend claude --web"

    response = _handle_agent_command(message, delegator, Context(), engine=Engine())

    assert response == "delegated"
    assert delegator.kwargs["description"] == "find current guidance"
    assert delegator.kwargs["anthropic_web_search"] is True
    assert delegator.kwargs["anthropic_web_search_tool_version"] == "web_search_20260209"
    assert delegator.kwargs["anthropic_web_search_max_uses"] == 2
