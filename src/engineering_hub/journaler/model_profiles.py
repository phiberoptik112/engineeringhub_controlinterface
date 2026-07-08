"""Resolve Journaler MLX model specifications from config and CLI overrides.

Supports named profiles under ``journaler.models`` in YAML, optional
``enable_thinking`` for Qwen3-style chat templates, and ``mlx_backend`` forcing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from engineering_hub.config.settings import Settings

logger = logging.getLogger(__name__)

DEFAULT_THINKING_MAX_TOKENS = 16_384


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
    max_tokens: int = 4096
    max_thinking_tokens: int = 8192
    temp: float = 0.7
    top_p: float = 0.9
    min_p: float = 0.05
    repetition_penalty: float = 1.1
    mlx_backend: str = "auto"
    enable_thinking: bool | None = None
    thinking_max_tokens: int | None = None
    streaming: bool = False
    profile_name: str | None = None


def effective_generation_max_tokens(
    spec: JournalerModelSpec,
    *,
    thinking_floor: int = DEFAULT_THINKING_MAX_TOKENS,
) -> int:
    """Return the MLX generation budget for *spec* (thinking + answer share one cap)."""
    if spec.enable_thinking is not True:
        return spec.max_tokens
    if spec.thinking_max_tokens is not None:
        return spec.thinking_max_tokens
    return max(spec.max_tokens, thinking_floor)


def model_status_data(
    spec: JournalerModelSpec,
    *,
    thinking_floor: int = DEFAULT_THINKING_MAX_TOKENS,
) -> list[tuple[str, str, str]]:
    """Return rows of ``(setting, current_value, how_to_change)`` for the /model display.

    Read-only fields (controlled by profile load) show ``(set by profile)`` in
    the third column.  Adjustable fields show the exact ``/model set`` command
    the user can run to change them.
    """
    think = spec.enable_thinking
    if think is None:
        think_s = "auto (tokenizer default)"
    else:
        think_s = "on" if think else "off"
    prof = spec.profile_name or "(legacy / CLI override)"
    effective = effective_generation_max_tokens(spec, thinking_floor=thinking_floor)
    rows: list[tuple[str, str, str]] = [
        ("Active model", spec.model_path, "/model <profile>  or  /model path <…>"),
        ("Profile", prof, "/model <profile>"),
        ("Context window", f"{spec.model_context_window:,}", "(set by profile)"),
        ("mlx_backend", spec.mlx_backend, "(set by profile)"),
        ("thinking", think_s, "/model set thinking on|off|auto"),
        ("streaming", "on" if spec.streaming else "off", "/model set streaming on|off"),
        ("temp", f"{spec.temp:.2f}", "/model set temp <float>"),
        ("top_p", f"{spec.top_p:.2f}", "/model set top_p <float>"),
        ("min_p", f"{spec.min_p:.3f}", "/model set min_p <float>"),
        ("max_tokens", str(spec.max_tokens), "/model set max_tokens <int>"),
    ]
    if spec.enable_thinking is True:
        rows.append(
            ("effective max_tokens", f"{effective:,}", "(thinking mode)"),
        )
    return rows


def _legacy_base_spec(settings: Settings) -> JournalerModelSpec:
    """Build spec fields from top-level journaler / mlx settings (no profile map)."""
    path = (settings.journaler_model_path or "").strip() or (settings.mlx_model_path or "").strip()
    return JournalerModelSpec(
        model_path=path,
        model_context_window=settings.journaler_model_context_window,
        max_tokens=settings.journaler_max_tokens,
        max_thinking_tokens=settings.journaler_max_thinking_tokens,
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
        max_thinking_tokens=int(data["max_thinking_tokens"])
        if data.get("max_thinking_tokens") is not None
        else defaults.max_thinking_tokens,
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
        thinking_max_tokens=int(data["thinking_max_tokens"])
        if data.get("thinking_max_tokens") is not None
        else defaults.thinking_max_tokens,
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
            max_thinking_tokens=base.max_thinking_tokens,
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
        from engineering_hub.journaler.model_catalog import normalize_model_path_input

        normalized = normalize_model_path_input(raw_path.strip())
        return JournalerModelSpec(
            model_path=normalized,
            model_context_window=base.model_context_window,
            max_tokens=base.max_tokens,
            max_thinking_tokens=base.max_thinking_tokens,
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
        "max_thinking_tokens": spec.max_thinking_tokens,
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
        ``(mode, arg1, arg2)`` where *mode* is one of:

        - ``"status"`` — no args; *arg1* and *arg2* are ``None``.
        - ``"profile"`` — *arg1* is the profile name; *arg2* is ``None``.
        - ``"path"`` — *arg1* is ``None``; *arg2* is the raw path/HF-id string.
        - ``"set"`` — *arg1* is the setting key; *arg2* is the raw value string.
    """
    stripped = message.strip()
    if not stripped.lower().startswith("/model"):
        return "status", None, None
    rest = stripped[6:].strip()
    if not rest:
        return "status", None, None
    if rest.lower().startswith("set "):
        tokens = rest.split(None, 2)
        key = tokens[1].lower() if len(tokens) > 1 else ""
        val = tokens[2].strip() if len(tokens) > 2 else ""
        return "set", key, val
    if rest.lower().startswith("path "):
        return "path", None, rest[5:].strip()
    return "profile", rest, None


_ADJUSTABLE_PARAMS: frozenset[str] = frozenset(
    {"thinking", "streaming", "temp", "top_p", "min_p", "repetition_penalty", "max_tokens"}
)

_SET_USAGE = (
    "Usage: /model set <param> <value>\n\n"
    "Adjustable params:\n"
    "  thinking   on | off | auto\n"
    "  streaming  on | off\n"
    "  temp       <float>  (e.g. 0.7)\n"
    "  top_p      <float>  (e.g. 0.9)\n"
    "  min_p      <float>  (e.g. 0.05)\n"
    "  max_tokens <int>    (e.g. 4096)"
)


def _apply_model_set_command(
    key: str,
    raw_val: str,
    cur: "JournalerModelSpec",
    engine: Any,
    *,
    thinking_floor: int = DEFAULT_THINKING_MAX_TOKENS,
) -> tuple["JournalerModelSpec", str]:
    """Parse and apply a ``/model set`` operation.

    Returns ``(new_spec, confirmation_message)`` on success.
    Raises ``ValueError`` with a user-facing message on bad input.
    """
    if not key:
        raise ValueError(_SET_USAGE)
    if key not in _ADJUSTABLE_PARAMS:
        available = ", ".join(sorted(_ADJUSTABLE_PARAMS))
        raise ValueError(f"Unknown param {key!r}. Adjustable: {available}\n\n{_SET_USAGE}")
    if not raw_val:
        raise ValueError(f"Missing value for '{key}'.\n\n{_SET_USAGE}")

    if key == "thinking":
        low = raw_val.lower()
        if low in ("on", "true", "1", "yes"):
            new_val: bool | None = True
        elif low in ("off", "false", "0", "no"):
            new_val = False
        elif low in ("auto", "none", "null", "default"):
            new_val = None
        else:
            raise ValueError(
                f"Invalid value for 'thinking': {raw_val!r}. Use on, off, or auto."
            )
        new_spec = replace(cur, enable_thinking=new_val)
        engine._backend.set_enable_thinking(new_val)
        effective = effective_generation_max_tokens(new_spec, thinking_floor=thinking_floor)
        engine.update_max_tokens(effective)
        label = "auto (tokenizer default)" if new_val is None else ("on" if new_val else "off")
        return new_spec, f"thinking → {label} (effective max_tokens → {effective:,})"

    if key == "streaming":
        low = raw_val.lower()
        if low in ("on", "true", "1", "yes"):
            enabled = True
        elif low in ("off", "false", "0", "no"):
            enabled = False
        else:
            raise ValueError(
                f"Invalid value for 'streaming': {raw_val!r}. Use on or off."
            )
        new_spec = replace(cur, streaming=enabled)
        return new_spec, f"streaming → {'on' if enabled else 'off'}"

    if key == "max_tokens":
        try:
            int_val = int(raw_val)
        except ValueError:
            raise ValueError(f"'max_tokens' requires an integer; got {raw_val!r}.")
        if int_val < 1:
            raise ValueError("'max_tokens' must be >= 1.")
        if cur.enable_thinking is True:
            new_spec = replace(cur, max_tokens=int_val, thinking_max_tokens=int_val)
        else:
            new_spec = replace(cur, max_tokens=int_val)
        effective = effective_generation_max_tokens(new_spec, thinking_floor=thinking_floor)
        engine.update_max_tokens(effective)
        return new_spec, f"max_tokens → {int_val} (effective → {effective:,})"

    # Floating-point sampling params: temp, top_p, min_p, repetition_penalty
    try:
        float_val = float(raw_val)
    except ValueError:
        raise ValueError(f"'{key}' requires a float; got {raw_val!r}.")
    if float_val < 0:
        raise ValueError(f"'{key}' must be >= 0; got {float_val}.")
    new_spec = replace(cur, **{key: float_val})
    engine.update_sampling_params(**{key: float_val})
    return new_spec, f"{key} → {float_val}"


def _model_status_as_text(
    spec: "JournalerModelSpec",
    *,
    thinking_floor: int = DEFAULT_THINKING_MAX_TOKENS,
) -> str:
    """Format model status as plain text for TUI / HTTP responses."""
    rows = model_status_data(spec, thinking_floor=thinking_floor)
    lines = ["Model status:"]
    for setting, value, how in rows:
        lines.append(f"  {setting:<16} {value:<35}  ({how})")
    return "\n".join(lines)


def journaler_slash_model_command(
    message: str,
    *,
    settings: Any,
    model_ctx: JournalerChatModelContext,
    engine: Any,
    delegator: Any | None = None,
) -> str:
    """Handle ``/model``, ``/model set …``, profile switch, or ``/model path <id>``.

    Returns user-facing text.
    """
    mode, arg1, arg2 = parse_model_slash_message(message)
    cur = model_ctx.spec

    thinking_floor = int(getattr(settings, "journaler_thinking_max_tokens", DEFAULT_THINKING_MAX_TOKENS))

    if mode == "status":
        return _model_status_as_text(cur, thinking_floor=thinking_floor)

    if mode == "set":
        try:
            new_spec, confirm = _apply_model_set_command(
                arg1 or "",
                arg2 or "",
                cur,
                engine,
                thinking_floor=thinking_floor,
            )
        except ValueError as exc:
            return str(exc)
        model_ctx.spec = new_spec
        return confirm

    if mode == "path" and not (arg2 or "").strip():
        return "Usage: /model path <huggingface-id-or-local-path>"

    try:
        if mode == "path":
            new_spec = resolve_journaler_model_spec_for_slash(
                settings, raw_path=arg2 or "", current_defaults=cur
            )
        else:
            new_spec = resolve_journaler_model_spec_for_slash(
                settings, profile_name=arg1, current_defaults=cur
            )
    except ValueError as exc:
        hint = " Try /model_browse to pick from cached mlx-community models."
        return f"Could not switch model: {exc}.{hint}"

    import time

    t0 = time.monotonic()
    try:
        reload_journaler_model_into_engine(
            new_spec,
            engine,
            delegator,
            thinking_floor=thinking_floor,
        )
    except Exception as exc:
        logger.exception("Journaler model reload failed")
        hint = " Try /model_browse or use /model path mlx-community/<name>."
        return f"Model load failed (previous model still active): {exc}.{hint}"
    model_ctx.spec = new_spec
    elapsed = time.monotonic() - t0
    return (
        f"Model ready: {new_spec.model_path}\n"
        f"(loaded in {elapsed:.1f}s; conversation history kept.)"
    )


def load_model_from_catalog_entry(
    entry: object,
    *,
    settings: Any,
    model_ctx: JournalerChatModelContext,
    engine: Any,
    delegator: Any | None = None,
) -> str:
    """Load a :class:`~engineering_hub.journaler.model_catalog.ModelCatalogEntry`."""
    from engineering_hub.journaler.model_catalog import resolve_catalog_entry_spec

    import time

    t0 = time.monotonic()
    try:
        new_spec = resolve_catalog_entry_spec(entry, settings, model_ctx.spec)
        reload_journaler_model_into_engine(
            new_spec,
            engine,
            delegator,
            thinking_floor=int(
                getattr(settings, "journaler_thinking_max_tokens", DEFAULT_THINKING_MAX_TOKENS)
            ),
        )
    except Exception as exc:
        logger.exception("Journaler model reload failed")
        hint = " Try /model path mlx-community/<name>."
        return f"Model load failed (previous model still active): {exc}.{hint}"
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
    *,
    thinking_floor: int = DEFAULT_THINKING_MAX_TOKENS,
) -> None:
    """Load *spec* as a new backend, swap *engine*'s backend, sync *delegator* if set."""
    backend = build_journaler_mlx_backend(spec)
    engine.replace_backend(
        backend,
        model_context_window=spec.model_context_window,
        max_tokens=spec.max_tokens,
        max_thinking_tokens=spec.max_thinking_tokens,
    )
    if delegator is not None:
        delegator.set_mlx_backend(backend)


def ensure_spec_model_path(spec: JournalerModelSpec, default_id: str) -> JournalerModelSpec:
    """If *spec.model_path* is empty, return a copy with *default_id*."""
    if spec.model_path and spec.model_path.strip():
        return spec
    return replace(spec, model_path=default_id)


def journaler_model_display_label(spec: JournalerModelSpec) -> str:
    """Short label for status bars (profile name or checkpoint basename)."""
    if spec.profile_name:
        return spec.profile_name
    path = (spec.model_path or "").strip()
    if not path:
        return "(no model)"
    return Path(path).name if "/" in path else path


def spec_from_journaler_config(config: Any) -> JournalerModelSpec:
    """Build a spec from :class:`JournalerConfig` (daemon / HTTP runtime)."""
    return JournalerModelSpec(
        model_path=config.model_path,
        model_context_window=config.model_context_window,
        max_tokens=config.max_tokens,
        max_thinking_tokens=getattr(config, "max_thinking_tokens", 8192),
        temp=config.temp,
        top_p=config.top_p,
        min_p=config.min_p,
        repetition_penalty=config.repetition_penalty,
        mlx_backend=getattr(config, "mlx_backend", "auto"),
        enable_thinking=getattr(config, "enable_thinking", None),
        profile_name=None,
    )
