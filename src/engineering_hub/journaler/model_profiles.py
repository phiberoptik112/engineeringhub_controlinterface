"""Resolve Journaler MLX model specifications from config and CLI overrides.

Supports named profiles under ``journaler.models`` in YAML, optional
``enable_thinking`` for Qwen3-style chat templates, and ``mlx_backend`` forcing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from engineering_hub.config.settings import Settings

logger = logging.getLogger(__name__)


@dataclass
class JournalerChatModelContext:
    """Mutable runtime spec for interactive or HTTP chat (supports ``/model``)."""

    settings: Any
    spec: JournalerModelSpec


@dataclass(frozen=True)
class JournalerModelSpec:
    """Fully resolved parameters for loading the Journaler MLX model."""

    model_path: str
    model_context_window: int = 32768
    max_tokens: int = 4000
    temp: float = 0.7
    top_p: float = 0.9
    min_p: float = 0.05
    repetition_penalty: float = 1.1
    mlx_backend: str = "auto"
    enable_thinking: bool | None = None
    profile_name: str | None = None


def _legacy_base_spec(settings: Settings) -> JournalerModelSpec:
    """Build spec fields from top-level journaler / mlx settings (no profile map)."""
    path = (settings.journaler_model_path or "").strip() or (settings.mlx_model_path or "").strip()
    return JournalerModelSpec(
        model_path=path,
        model_context_window=settings.journaler_model_context_window,
        max_tokens=settings.journaler_max_tokens,
        temp=settings.journaler_temp,
        top_p=settings.journaler_top_p,
        min_p=settings.journaler_min_p,
        repetition_penalty=settings.journaler_repetition_penalty,
        mlx_backend="auto",
        enable_thinking=None,
        profile_name=None,
    )


def _parse_enable_thinking(raw: object) -> bool | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        low = raw.strip().lower()
        if low in ("true", "1", "yes"):
            return True
        if low in ("false", "0", "no"):
            return False
    return None


def _spec_from_profile_dict(
    profile_name: str | None,
    data: dict[str, Any],
    defaults: JournalerModelSpec,
) -> JournalerModelSpec:
    """Overlay profile YAML dict onto *defaults*."""
    path = data.get("model_path")
    if not path or not str(path).strip():
        raise ValueError(
            f"Journaler model profile {profile_name!r} is missing required 'model_path'"
        )
    return JournalerModelSpec(
        model_path=str(path).strip(),
        model_context_window=int(
            data["model_context_window"]
            if data.get("model_context_window") is not None
            else defaults.model_context_window
        ),
        max_tokens=int(data["max_tokens"])
        if data.get("max_tokens") is not None
        else defaults.max_tokens,
        temp=float(data["temp"]) if data.get("temp") is not None else defaults.temp,
        top_p=float(data["top_p"]) if data.get("top_p") is not None else defaults.top_p,
        min_p=float(data["min_p"]) if data.get("min_p") is not None else defaults.min_p,
        repetition_penalty=float(data["repetition_penalty"])
        if data.get("repetition_penalty") is not None
        else defaults.repetition_penalty,
        mlx_backend=str(data.get("mlx_backend") or defaults.mlx_backend).strip().lower()
        if data.get("mlx_backend") is not None
        else defaults.mlx_backend,
        enable_thinking=_parse_enable_thinking(data["enable_thinking"])
        if "enable_thinking" in data
        else defaults.enable_thinking,
        profile_name=profile_name,
    )


def resolve_journaler_model_spec(
    settings: Settings,
    *,
    cli_model: str | None = None,
    cli_profile: str | None = None,
) -> JournalerModelSpec:
    """Resolve the effective Journaler model spec.

    Precedence:
    1. ``cli_model`` (HF id or local path) — uses journaler defaults for other fields.
    2. ``cli_profile`` — named entry in ``settings.journaler_models``.
    3. ``journaler.model_profile`` when ``journaler.models`` is non-empty.
    4. Legacy: ``journaler.model_path`` / ``mlx.model_path`` with top-level journaler fields.
    """
    base = _legacy_base_spec(settings)
    models = getattr(settings, "journaler_models", None) or {}

    if cli_model and str(cli_model).strip():
        path = str(cli_model).strip()
        logger.debug("Journaler model: CLI --model override path=%s", path)
        return JournalerModelSpec(
            model_path=path,
            model_context_window=base.model_context_window,
            max_tokens=base.max_tokens,
            temp=base.temp,
            top_p=base.top_p,
            min_p=base.min_p,
            repetition_penalty=base.repetition_penalty,
            mlx_backend=base.mlx_backend,
            enable_thinking=base.enable_thinking,
            profile_name=None,
        )

    if cli_profile and str(cli_profile).strip():
        name = str(cli_profile).strip()
        if name not in models:
            available = ", ".join(sorted(models.keys())) if models else "(none defined)"
            raise ValueError(
                f"Unknown journaler profile {name!r}. "
                f"Configured profiles: {available}. "
                "Define journaler.models in config.yaml."
            )
        return _spec_from_profile_dict(name, models[name], base)

    if models:
        prof = (settings.journaler_model_profile or "default").strip()
        if prof not in models:
            available = ", ".join(sorted(models.keys()))
            raise ValueError(
                f"Journaler model_profile {prof!r} not found under journaler.models. "
                f"Available: {available}"
            )
        return _spec_from_profile_dict(prof, models[prof], base)

    return base


def resolve_journaler_model_spec_for_slash(
    settings: Settings,
    *,
    profile_name: str | None = None,
    raw_path: str | None = None,
    current_defaults: JournalerModelSpec | None = None,
) -> JournalerModelSpec:
    """Resolve spec for ``/model`` slash command (no CLI flags).

    If *profile_name* is set, load that profile from config.
    If *raw_path* is set, use it as model_path and inherit from *current_defaults*
    or legacy base.
    """
    base = current_defaults if current_defaults is not None else _legacy_base_spec(settings)
    models = getattr(settings, "journaler_models", None) or {}

    if raw_path and raw_path.strip():
        return JournalerModelSpec(
            model_path=raw_path.strip(),
            model_context_window=base.model_context_window,
            max_tokens=base.max_tokens,
            temp=base.temp,
            top_p=base.top_p,
            min_p=base.min_p,
            repetition_penalty=base.repetition_penalty,
            mlx_backend=base.mlx_backend,
            enable_thinking=base.enable_thinking,
            profile_name=None,
        )

    if profile_name and profile_name.strip():
        name = profile_name.strip()
        if name not in models:
            available = (
                ", ".join(sorted(models.keys())) if models else "(none — add journaler.models)"
            )
            raise ValueError(f"Unknown profile {name!r}. Available: {available}")
        return _spec_from_profile_dict(name, models[name], base)

    raise ValueError("internal: profile_name or raw_path required")


def apply_spec_to_journaler_config_attrs(spec: JournalerModelSpec) -> dict[str, Any]:
    """Map a spec to JournalerConfig keyword arguments (subset)."""
    return {
        "model_path": spec.model_path,
        "model_context_window": spec.model_context_window,
        "max_tokens": spec.max_tokens,
        "temp": spec.temp,
        "top_p": spec.top_p,
        "min_p": spec.min_p,
        "repetition_penalty": spec.repetition_penalty,
        "enable_thinking": spec.enable_thinking,
        "mlx_backend": spec.mlx_backend,
    }


def build_journaler_mlx_backend(spec: JournalerModelSpec):
    """Factory for :class:`ConversationalMLXBackend` from a spec."""
    from engineering_hub.journaler.engine import ConversationalMLXBackend

    return ConversationalMLXBackend(
        model_path=spec.model_path,
        temp=spec.temp,
        top_p=spec.top_p,
        min_p=spec.min_p,
        repetition_penalty=spec.repetition_penalty,
        backend=spec.mlx_backend,
        enable_thinking=spec.enable_thinking,
    )


def parse_model_slash_message(message: str) -> tuple[str, str | None, str | None]:
    """Parse ``/model`` input.

    Returns:
        ``(mode, profile_name, path)`` where *mode* is ``status``, ``profile``, or ``path``.
    """
    stripped = message.strip()
    if not stripped.lower().startswith("/model"):
        return "status", None, None
    rest = stripped[6:].strip()
    if not rest:
        return "status", None, None
    if rest.lower().startswith("path "):
        return "path", None, rest[5:].strip()
    return "profile", rest, None


def journaler_slash_model_command(
    message: str,
    *,
    settings: Any,
    model_ctx: JournalerChatModelContext,
    engine: Any,
    delegator: Any | None = None,
) -> str:
    """Handle ``/model``, profile switch, or ``/model path <id>``.

    Returns user-facing text.
    """
    mode, profile, path = parse_model_slash_message(message)
    cur = model_ctx.spec
    if mode == "path" and not (path or "").strip():
        return "Usage: /model path <huggingface-id-or-local-path>"
    if mode == "status":
        think = cur.enable_thinking
        think_s = "default (tokenizer)" if think is None else str(think)
        prof = cur.profile_name or "(legacy / CLI override)"
        return (
            f"Active model: {cur.model_path}\n"
            f"Profile: {prof}\n"
            f"Context window: {cur.model_context_window}\n"
            f"enable_thinking: {think_s}\n"
            f"mlx_backend: {cur.mlx_backend}"
        )
    try:
        if mode == "path":
            new_spec = resolve_journaler_model_spec_for_slash(
                settings, raw_path=path or "", current_defaults=cur
            )
        else:
            new_spec = resolve_journaler_model_spec_for_slash(
                settings, profile_name=profile, current_defaults=cur
            )
    except ValueError as exc:
        return f"Could not switch model: {exc}"

    import time

    t0 = time.monotonic()
    try:
        reload_journaler_model_into_engine(new_spec, engine, delegator)
    except Exception as exc:
        logger.exception("Journaler model reload failed")
        return f"Model load failed (previous model still active): {exc}"
    model_ctx.spec = new_spec
    elapsed = time.monotonic() - t0
    return (
        f"Model ready: {new_spec.model_path}\n"
        f"(loaded in {elapsed:.1f}s; conversation history kept.)"
    )


def reload_journaler_model_into_engine(
    spec: JournalerModelSpec,
    engine: Any,
    delegator: Any | None = None,
) -> None:
    """Load *spec* as a new backend, swap *engine*'s backend, sync *delegator* if set."""
    backend = build_journaler_mlx_backend(spec)
    engine.replace_backend(
        backend,
        model_context_window=spec.model_context_window,
        max_tokens=spec.max_tokens,
    )
    if delegator is not None:
        delegator.set_mlx_backend(backend)


def ensure_spec_model_path(spec: JournalerModelSpec, default_id: str) -> JournalerModelSpec:
    """If *spec.model_path* is empty, return a copy with *default_id*."""
    if spec.model_path and spec.model_path.strip():
        return spec
    return replace(spec, model_path=default_id)


def spec_from_journaler_config(config: Any) -> JournalerModelSpec:
    """Build a spec from :class:`JournalerConfig` (daemon / HTTP runtime)."""
    return JournalerModelSpec(
        model_path=config.model_path,
        model_context_window=config.model_context_window,
        max_tokens=config.max_tokens,
        temp=config.temp,
        top_p=config.top_p,
        min_p=config.min_p,
        repetition_penalty=config.repetition_penalty,
        mlx_backend=getattr(config, "mlx_backend", "auto"),
        enable_thinking=getattr(config, "enable_thinking", None),
        profile_name=None,
    )
