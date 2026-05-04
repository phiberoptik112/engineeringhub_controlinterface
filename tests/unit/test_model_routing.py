"""Tests for per-agent model routing (ModelClass, settings, backend resolution)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from engineering_hub.agents.backends import (
    OllamaBackend,
    _resolve_model_for_agent,
    create_backend,
)
from engineering_hub.agents.registry import (
    AgentConfig,
    AgentRegistry,
    ModelClass,
)
from engineering_hub.config.settings import Settings
from engineering_hub.core.constants import AgentType

# ---------------------------------------------------------------------------
# ModelClass + AgentConfig basics
# ---------------------------------------------------------------------------


class TestModelClass:
    def test_values(self):
        assert ModelClass.REASONING.value == "reasoning"
        assert ModelClass.TOOL_USE.value == "tool_use"

    def test_is_str_enum(self):
        assert isinstance(ModelClass.REASONING, str)
        assert ModelClass("reasoning") is ModelClass.REASONING


class TestAgentConfigDefaults:
    def test_default_model_class_is_reasoning(self):
        config = AgentConfig(
            agent_type=AgentType.STANDARDS_CHECKER,
            prompt_file="standards-checker.txt",
        )
        assert config.model_class is ModelClass.REASONING

    def test_model_override_default_none(self):
        config = AgentConfig(
            agent_type=AgentType.RESEARCH,
            prompt_file="research-agent.txt",
        )
        assert config.model_override is None

    def test_explicit_tool_use_class(self):
        config = AgentConfig(
            agent_type=AgentType.RESEARCH,
            prompt_file="research-agent.txt",
            model_class=ModelClass.TOOL_USE,
        )
        assert config.model_class is ModelClass.TOOL_USE


# ---------------------------------------------------------------------------
# Registry classifications
# ---------------------------------------------------------------------------


class TestRegistryModelClass:
    def setup_method(self):
        self.registry = AgentRegistry()

    def test_research_is_tool_use(self):
        assert self.registry.get_model_class(AgentType.RESEARCH) is ModelClass.TOOL_USE

    def test_technical_writer_is_tool_use(self):
        assert self.registry.get_model_class(AgentType.TECHNICAL_WRITER) is ModelClass.TOOL_USE

    def test_technical_reviewer_is_tool_use(self):
        assert self.registry.get_model_class(AgentType.TECHNICAL_REVIEWER) is ModelClass.TOOL_USE

    def test_latex_writer_is_tool_use(self):
        assert self.registry.get_model_class(AgentType.LATEX_WRITER) is ModelClass.TOOL_USE

    def test_standards_checker_is_reasoning(self):
        assert self.registry.get_model_class(AgentType.STANDARDS_CHECKER) is ModelClass.REASONING

    def test_evaluator_is_reasoning(self):
        assert self.registry.get_model_class(AgentType.EVALUATOR) is ModelClass.REASONING

    def test_ref_engineer_is_reasoning(self):
        assert self.registry.get_model_class(AgentType.REF_ENGINEER) is ModelClass.REASONING

    def test_weekly_reviewer_is_reasoning(self):
        assert self.registry.get_model_class(AgentType.WEEKLY_REVIEWER) is ModelClass.REASONING

    def test_panning_for_gold_is_reasoning(self):
        assert self.registry.get_model_class(AgentType.PANNING_FOR_GOLD) is ModelClass.REASONING

    def test_unknown_agent_defaults_to_reasoning(self):
        """get_model_class falls back to REASONING for unknown agents."""
        fake_type = MagicMock(spec=AgentType)
        assert self.registry.get_model_class(fake_type) is ModelClass.REASONING


# ---------------------------------------------------------------------------
# Settings: agents_* fields + from_yaml
# ---------------------------------------------------------------------------


class TestSettingsAgentFields:
    def test_defaults_empty(self):
        s = Settings()
        assert s.agents_reasoning_model == ""
        assert s.agents_tool_use_model == ""
        assert s.agents_reasoning_max_tokens == 8192
        assert s.agents_tool_use_max_tokens == 4096

    def test_from_yaml_agents_section(self, tmp_path: Path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "agents:\n"
            "  models:\n"
            '    reasoning: "qwen3:32b"\n'
            '    tool_use: "qwen3.6:35b-a3b"\n'
            "  reasoning_max_tokens: 16384\n"
            "  tool_use_max_tokens: 8192\n"
        )
        s = Settings.from_yaml(config_file)
        assert s.agents_reasoning_model == "qwen3:32b"
        assert s.agents_tool_use_model == "qwen3.6:35b-a3b"
        assert s.agents_reasoning_max_tokens == 16384
        assert s.agents_tool_use_max_tokens == 8192

    def test_from_yaml_partial_agents(self, tmp_path: Path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "agents:\n"
            "  models:\n"
            '    reasoning: "qwen3:32b"\n'
        )
        s = Settings.from_yaml(config_file)
        assert s.agents_reasoning_model == "qwen3:32b"
        assert s.agents_tool_use_model == ""

    def test_from_yaml_no_agents_section(self, tmp_path: Path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("llm_provider: ollama\n")
        s = Settings.from_yaml(config_file)
        assert s.agents_reasoning_model == ""
        assert s.agents_tool_use_model == ""


# ---------------------------------------------------------------------------
# _resolve_model_for_agent
# ---------------------------------------------------------------------------


class TestResolveModelForAgent:
    def setup_method(self):
        self.registry = AgentRegistry()
        self.settings = Settings(
            agents_reasoning_model="qwen3:32b",
            agents_tool_use_model="qwen3.6:35b-a3b",
        )

    def test_none_agent_type_returns_none(self):
        assert _resolve_model_for_agent(self.settings, None, self.registry) is None

    def test_none_registry_returns_none(self):
        assert _resolve_model_for_agent(self.settings, AgentType.RESEARCH, None) is None

    def test_reasoning_agent_gets_reasoning_model(self):
        result = _resolve_model_for_agent(
            self.settings, AgentType.STANDARDS_CHECKER, self.registry
        )
        assert result == "qwen3:32b"

    def test_tool_use_agent_gets_tool_use_model(self):
        result = _resolve_model_for_agent(
            self.settings, AgentType.RESEARCH, self.registry
        )
        assert result == "qwen3.6:35b-a3b"

    def test_model_override_takes_priority(self):
        config = self.registry.get_config(AgentType.RESEARCH)
        assert config is not None
        config.model_override = "custom-model:latest"

        result = _resolve_model_for_agent(
            self.settings, AgentType.RESEARCH, self.registry
        )
        assert result == "custom-model:latest"

        config.model_override = None

    def test_empty_class_model_returns_none(self):
        settings = Settings(agents_reasoning_model="", agents_tool_use_model="")
        result = _resolve_model_for_agent(
            settings, AgentType.STANDARDS_CHECKER, self.registry
        )
        assert result is None

    def test_unknown_agent_type_returns_none(self):
        fake_type = MagicMock(spec=AgentType)
        result = _resolve_model_for_agent(self.settings, fake_type, self.registry)
        assert result is None


# ---------------------------------------------------------------------------
# create_backend with agent routing
# ---------------------------------------------------------------------------


class TestCreateBackendRouting:
    """Test that create_backend respects per-agent model overrides for Ollama."""

    def setup_method(self):
        self.registry = AgentRegistry()

    def test_ollama_uses_class_model(self):
        settings = Settings(
            llm_provider="ollama",
            ollama_chat_model="default-model",
            agents_tool_use_model="tool-use-model",
        )
        backend = create_backend(
            settings,
            agent_type=AgentType.RESEARCH,
            registry=self.registry,
        )
        assert isinstance(backend, OllamaBackend)
        assert backend._model == "tool-use-model"

    def test_ollama_falls_back_to_global(self):
        settings = Settings(
            llm_provider="ollama",
            ollama_chat_model="default-model",
            agents_tool_use_model="",
        )
        backend = create_backend(
            settings,
            agent_type=AgentType.RESEARCH,
            registry=self.registry,
        )
        assert isinstance(backend, OllamaBackend)
        assert backend._model == "default-model"

    def test_ollama_reasoning_agent_gets_reasoning_model(self):
        settings = Settings(
            llm_provider="ollama",
            ollama_chat_model="default-model",
            agents_reasoning_model="reasoning-model",
        )
        backend = create_backend(
            settings,
            agent_type=AgentType.STANDARDS_CHECKER,
            registry=self.registry,
        )
        assert isinstance(backend, OllamaBackend)
        assert backend._model == "reasoning-model"

    def test_no_agent_type_uses_global(self):
        settings = Settings(
            llm_provider="ollama",
            ollama_chat_model="default-model",
            agents_reasoning_model="reasoning-model",
        )
        backend = create_backend(settings)
        assert isinstance(backend, OllamaBackend)
        assert backend._model == "default-model"

    def test_anthropic_ignores_model_routing(self):
        """Anthropic model IDs are not overridden by local model tags."""
        settings = Settings(
            llm_provider="anthropic",
            anthropic_api_key="test-key",
            anthropic_model="claude-sonnet-4-5-20250929",
            agents_reasoning_model="qwen3:32b",
        )
        backend = create_backend(
            settings,
            agent_type=AgentType.STANDARDS_CHECKER,
            registry=self.registry,
        )
        assert backend.model == "claude-sonnet-4-5-20250929"
