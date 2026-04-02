"""LLM backend abstraction layer.

Provides a Protocol-based interface so AgentWorker can use either the
Anthropic API or local MLX models transparently.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import anthropic

from engineering_hub.core.exceptions import LLMBackendError

if TYPE_CHECKING:
    from engineering_hub.config.settings import Settings

logger = logging.getLogger(__name__)


@runtime_checkable
class LLMBackend(Protocol):
    """Minimal interface every LLM backend must satisfy."""

    def complete(self, system: str, user_message: str, max_tokens: int) -> str: ...

    def test_connection(self) -> bool: ...


class AnthropicBackend:
    """Wraps the Anthropic Messages API."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-5-20250929") -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def complete(self, system: str, user_message: str, max_tokens: int) -> str:
        try:
            message = self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user_message}],
            )
        except anthropic.APIError as exc:
            raise LLMBackendError(str(exc), provider="anthropic") from exc

        text_parts = [block.text for block in message.content if hasattr(block, "text")]
        return "\n".join(text_parts)

    def test_connection(self) -> bool:
        try:
            self._client.messages.create(
                model=self.model,
                max_tokens=10,
                messages=[{"role": "user", "content": "Hi"}],
            )
            return True
        except Exception as exc:
            logger.error(f"Anthropic connection test failed: {exc}")
            return False


@dataclass
class MLXSamplingConfig:
    """Sampling parameters for MLX generation."""

    temp: float = 0.7
    top_p: float = 0.9
    min_p: float = 0.05
    repetition_penalty: float = 1.1
    repetition_context_size: int = 20


class MLXBackend:
    """Wraps mlx-lm for local on-device inference.

    Accepts either a HuggingFace model ID (e.g. ``mlx-community/gemma-3-27b-it-qat-4bit``)
    or an explicit local path to a snapshot directory containing ``config.json``
    and ``*.safetensors`` weights.
    """

    def __init__(
        self,
        model_path: str,
        sampling: MLXSamplingConfig | None = None,
    ) -> None:
        try:
            import mlx_lm
            from mlx_lm.sample_utils import make_logits_processors, make_sampler
        except ImportError as exc:
            raise LLMBackendError(
                "mlx-lm is not installed. Install with: pip install 'engineering-hub[mlx]'",
                provider="mlx",
            ) from exc

        self._mlx_lm = mlx_lm
        self._make_sampler = make_sampler
        self._make_logits_processors = make_logits_processors
        self._sampling = sampling or MLXSamplingConfig()
        self._model_path = model_path

        resolved = str(Path(model_path).expanduser())
        if Path(resolved).is_dir():
            load_path = resolved
        else:
            load_path = model_path

        logger.info(f"Loading MLX model from: {load_path}")
        try:
            self._model, self._tokenizer = mlx_lm.load(load_path)
        except Exception as exc:
            raise LLMBackendError(
                f"Failed to load MLX model from '{load_path}': {exc}",
                provider="mlx",
            ) from exc
        logger.info(f"MLX model loaded: {model_path}")

    def complete(self, system: str, user_message: str, max_tokens: int) -> str:
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ]
        prompt = self._tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )

        s = self._sampling
        sampler = self._make_sampler(temp=s.temp, top_p=s.top_p, min_p=s.min_p)
        logits_processors = self._make_logits_processors(
            repetition_penalty=s.repetition_penalty,
            repetition_context_size=s.repetition_context_size,
        )

        try:
            return self._mlx_lm.generate(
                self._model,
                self._tokenizer,
                prompt=prompt,
                max_tokens=max_tokens,
                sampler=sampler,
                logits_processors=logits_processors,
            )
        except Exception as exc:
            raise LLMBackendError(
                f"MLX generation failed: {exc}", provider="mlx"
            ) from exc

    def test_connection(self) -> bool:
        return self._model is not None


def create_backend(settings: Settings) -> LLMBackend:
    """Factory: build the appropriate LLM backend from application settings."""
    provider = settings.llm_provider.lower()

    if provider == "anthropic":
        if not settings.anthropic_api_key.get_secret_value():
            raise LLMBackendError(
                "Anthropic API key is required when llm_provider is 'anthropic'. "
                "Set ENGINEERING_HUB_ANTHROPIC_API_KEY or add to config.",
                provider="anthropic",
            )
        return AnthropicBackend(
            api_key=settings.anthropic_api_key.get_secret_value(),
            model=settings.anthropic_model,
        )

    if provider == "mlx":
        if not settings.mlx_model_path:
            raise LLMBackendError(
                "mlx.model_path is required when llm_provider is 'mlx'. "
                "Set it to a HuggingFace model ID or a local snapshot path.",
                provider="mlx",
            )
        sampling = MLXSamplingConfig(
            temp=settings.mlx_temp,
            top_p=settings.mlx_top_p,
            min_p=settings.mlx_min_p,
            repetition_penalty=settings.mlx_repetition_penalty,
        )
        return MLXBackend(model_path=settings.mlx_model_path, sampling=sampling)

    raise LLMBackendError(
        f"Unknown llm_provider '{provider}'. Choose 'anthropic' or 'mlx'.",
    )
