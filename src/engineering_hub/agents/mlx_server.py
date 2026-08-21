"""OpenAI-compatible client for ``mlx_lm.server``.

Journaler chat and tool-use agents talk to a separately running
``mlx_lm.server`` process over HTTP (default ``http://127.0.0.1:8081/v1``)
instead of loading weights in-process.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from engineering_hub.agents.backends import ToolAwareResponse, ToolCall
from engineering_hub.core.exceptions import LLMBackendError

logger = logging.getLogger(__name__)

PROVIDER = "mlx_server"


def probe_mlx_server(
    base_url: str,
    *,
    api_key: str = "not-needed",
    timeout_s: float = 2.0,
) -> bool:
    """Return True if ``mlx_lm.server`` answers a cheap health/models probe."""
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3]
    headers = {
        "Authorization": f"Bearer {api_key or 'not-needed'}",
        "Content-Type": "application/json",
    }
    for url in (f"{root}/health", f"{base_url.rstrip('/')}/models"):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout_s)
            if resp.status_code < 500:
                return True
        except requests.RequestException:
            continue
    return False


def parse_mlx_server_base_url(base_url: str) -> tuple[str, int]:
    """Parse host and port from an OpenAI-style base URL (e.g. ``http://127.0.0.1:8081/v1``)."""
    parsed = urlparse(base_url if "://" in base_url else f"http://{base_url}")
    host = parsed.hostname or "127.0.0.1"
    if parsed.port is not None:
        return host, int(parsed.port)
    if (parsed.scheme or "http").lower() == "https":
        return host, 443
    return host, 80


def resolve_mlx_lm_server_command() -> list[str]:
    """Return argv prefix for ``mlx_lm.server`` (PATH binary or ``python -m``)."""
    which = shutil.which("mlx_lm.server")
    if which:
        return [which]
    return [sys.executable, "-m", "mlx_lm.server"]


def start_mlx_lm_server(
    model: str,
    *,
    host: str = "127.0.0.1",
    port: int = 8081,
    max_tokens: int = 4096,
    log_path: Path | None = None,
) -> subprocess.Popen[Any]:
    """Spawn a detached ``mlx_lm.server`` process; leave it running after Journaler exits."""
    cmd = [
        *resolve_mlx_lm_server_command(),
        "--model",
        model,
        "--host",
        host,
        "--port",
        str(port),
        "--max-tokens",
        str(max_tokens),
    ]
    log_file = None
    stdout: Any = subprocess.DEVNULL
    stderr: Any = subprocess.DEVNULL
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = log_path.open("a", encoding="utf-8")
        stdout = log_file
        stderr = subprocess.STDOUT

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=stdout,
            stderr=stderr,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        if log_file is not None:
            log_file.close()
        raise

    logger.info(
        "Started mlx_lm.server pid=%s model=%s %s:%s (log=%s)",
        proc.pid,
        model,
        host,
        port,
        log_path,
    )
    return proc


def wait_for_mlx_server(
    base_url: str,
    *,
    api_key: str = "not-needed",
    timeout_s: float = 120.0,
    poll_interval_s: float = 1.0,
) -> bool:
    """Poll until ``probe_mlx_server`` succeeds or *timeout_s* elapses."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if probe_mlx_server(base_url, api_key=api_key, timeout_s=min(2.0, timeout_s)):
            return True
        time.sleep(poll_interval_s)
    return False


def offer_start_mlx_server_if_needed(
    settings: Any,
    model_path: str,
    *,
    console: Any | None = None,
    state_dir: Path | None = None,
    prompt_fn: Any | None = None,
    isatty: bool | None = None,
) -> bool:
    """Ensure mlx_lm.server is up for interactive Journaler sessions.

    Returns True when the server is reachable (already up or started after yes).
    Returns False when disabled, declined, non-interactive, or start failed —
    caller should proceed with in-process fallback via ``build_journaler_mlx_backend``.
    """
    enabled = bool(getattr(settings, "mlx_server_enabled", True))
    if not enabled:
        return False

    base_url = str(
        getattr(settings, "mlx_server_base_url", "http://127.0.0.1:8081/v1")
    )
    api_key = str(getattr(settings, "mlx_server_api_key", "not-needed"))
    if probe_mlx_server(base_url, api_key=api_key):
        return True

    offer = bool(getattr(settings, "mlx_server_offer_start", True))
    tty = sys.stdin.isatty() if isatty is None else bool(isatty)
    if not offer or not tty:
        return False

    def _print(msg: str) -> None:
        if console is not None and hasattr(console, "print"):
            console.print(msg)
        else:
            print(msg)

    _print(f"mlx_lm.server is not running at {base_url}.")
    ask = prompt_fn or input
    try:
        answer = str(ask("Start mlx_lm.server now for tool calling (/agent blender, etc.)? [y/N]: ")).strip().lower()
    except (EOFError, KeyboardInterrupt):
        _print("Declined — continuing without mlx_lm.server.")
        return False

    if answer not in ("y", "yes"):
        _print("Declined — continuing with in-process MLX (no tool calling).")
        return False

    host, port = parse_mlx_server_base_url(base_url)
    max_tokens = int(getattr(settings, "mlx_max_tokens", 4096) or 4096)
    log_dir = state_dir
    if log_dir is None:
        log_dir = getattr(settings, "journaler_state_dir", None)
    log_path = Path(log_dir) / "mlx_lm.server.log" if log_dir else None

    _print(f"Starting mlx_lm.server ({model_path} on {host}:{port})...")
    try:
        start_mlx_lm_server(
            model_path,
            host=host,
            port=port,
            max_tokens=max_tokens,
            log_path=log_path,
        )
    except Exception as exc:
        logger.warning("Failed to start mlx_lm.server: %s", exc)
        _print(f"Failed to start mlx_lm.server: {exc}")
        return False

    _print("Waiting for mlx_lm.server to become ready (model load can take a while)...")
    if wait_for_mlx_server(base_url, api_key=api_key, timeout_s=120.0):
        _print(f"mlx_lm.server ready at {base_url}")
        return True

    _print(
        "Timed out waiting for mlx_lm.server — continuing with in-process MLX. "
        f"Check log: {log_path}" if log_path else "Timed out waiting for mlx_lm.server."
    )
    return False


@dataclass
class MLXServerSamplingConfig:
    """Sampling parameters forwarded on each chat-completions request."""

    temp: float = 0.7
    top_p: float = 0.9
    min_p: float | None = None
    repetition_penalty: float | None = None


def _normalize_arguments(raw: Any) -> dict[str, Any]:
    """Coerce tool-call arguments to a dict (server may return a JSON string)."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"_raw": text}
        return parsed if isinstance(parsed, dict) else {"_raw": parsed}
    return {"_raw": raw}


def _openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Anthropic-style tool schemas to OpenAI function tools."""
    out: list[dict[str, Any]] = []
    for tool in tools:
        out.append(
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {}),
                },
            }
        )
    return out


def _parse_sse_data_lines(line: str) -> dict[str, Any] | None:
    """Parse one SSE ``data:`` payload; return None for keepalives / DONE."""
    text = line.strip()
    if not text or text.startswith(":"):
        return None
    if not text.startswith("data:"):
        return None
    payload = text[5:].strip()
    if payload == "[DONE]":
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


class OpenAIMLXServerBackend:
    """HTTP backend for ``mlx_lm.server`` (OpenAI chat-completions API).

    Implements both the agent ``LLMBackend`` surface (``complete`` /
    ``complete_with_tools``) and the Journaler conversational surface
    (``chat`` / ``stream_generate`` / sampling setters).
    """

    tool_protocol = "openai"

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:8081/v1",
        model: str,
        api_key: str = "not-needed",
        timeout_s: float = 600.0,
        sampling: MLXServerSamplingConfig | None = None,
        enable_thinking: bool | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key or "not-needed"
        self._timeout_s = timeout_s
        self._sampling = sampling or MLXServerSamplingConfig()
        self._enable_thinking = enable_thinking
        self._model_path = model  # alias for status / catalogs that inspect path

    # ------------------------------------------------------------------
    # Runtime setters (Journaler /model set)
    # ------------------------------------------------------------------

    @property
    def model(self) -> str:
        return self._model

    def set_model(self, model: str) -> None:
        """Change the request ``model`` id (server unloads/reloads on next call)."""
        self._model = model
        self._model_path = model

    def set_enable_thinking(self, value: bool | None) -> None:
        self._enable_thinking = value

    def set_sampling_params(
        self,
        *,
        temp: float | None = None,
        top_p: float | None = None,
        min_p: float | None = None,
        repetition_penalty: float | None = None,
    ) -> None:
        if temp is not None:
            self._sampling.temp = temp
        if top_p is not None:
            self._sampling.top_p = top_p
        if min_p is not None:
            self._sampling.min_p = min_p
        if repetition_penalty is not None:
            self._sampling.repetition_penalty = repetition_penalty

    def is_loaded(self) -> bool:
        return self.test_connection()

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _chat_url(self) -> str:
        return f"{self._base_url}/chat/completions"

    def _base_payload(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int,
        *,
        stream: bool,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": self._sampling.temp,
            "top_p": self._sampling.top_p,
            "stream": stream,
        }
        if self._sampling.min_p is not None:
            payload["min_p"] = self._sampling.min_p
        if self._sampling.repetition_penalty is not None:
            payload["repetition_penalty"] = self._sampling.repetition_penalty
        if self._enable_thinking is not None:
            payload["chat_template_kwargs"] = {
                "enable_thinking": self._enable_thinking,
            }
        if tools:
            payload["tools"] = _openai_tools(tools)
        return payload

    def _post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = requests.post(
                self._chat_url(),
                headers=self._headers(),
                json=payload,
                timeout=self._timeout_s,
            )
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict):
                raise LLMBackendError(
                    "mlx_lm.server returned a non-object JSON body",
                    provider=PROVIDER,
                )
            return data
        except requests.ConnectionError as exc:
            raise LLMBackendError(
                f"Cannot reach mlx_lm.server at {self._base_url}. "
                "Start it with: mlx_lm.server --model <id> --host 127.0.0.1 "
                "--port 8081 --max-tokens 4096",
                provider=PROVIDER,
            ) from exc
        except requests.HTTPError as exc:
            body = ""
            if exc.response is not None:
                body = (exc.response.text or "")[:300]
                status = exc.response.status_code
            else:
                status = "?"
            raise LLMBackendError(
                f"mlx_lm.server HTTP error {status}: {body}",
                provider=PROVIDER,
            ) from exc
        except LLMBackendError:
            raise
        except Exception as exc:
            raise LLMBackendError(
                f"mlx_lm.server request failed: {exc}",
                provider=PROVIDER,
            ) from exc

    def _extract_message(self, data: dict[str, Any]) -> dict[str, Any]:
        choices = data.get("choices") or []
        if not choices:
            return {}
        first = choices[0] if isinstance(choices[0], dict) else {}
        msg = first.get("message") or {}
        return msg if isinstance(msg, dict) else {}

    def _finish_reason(self, data: dict[str, Any]) -> str | None:
        choices = data.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            return None
        reason = choices[0].get("finish_reason")
        return str(reason) if reason is not None else None

    def _compose_text(self, message: dict[str, Any]) -> str:
        """Merge content + optional reasoning into one string for Journaler."""
        parts: list[str] = []
        reasoning = message.get("reasoning")
        if isinstance(reasoning, str) and reasoning.strip():
            parts.append(f"<think>\n{reasoning.strip()}\n</think>")
        content = message.get("content")
        if isinstance(content, str) and content:
            parts.append(content)
        elif content is not None and not isinstance(content, str):
            parts.append(str(content))
        return "\n".join(parts).strip()

    def _append_truncation_notice(
        self,
        text: str,
        *,
        finish_reason: str | None,
        max_tokens: int,
        max_thinking_tokens: int,
    ) -> str:
        if finish_reason != "length":
            return text
        if "<think>" in text and "</think>" not in text:
            return (
                text
                + "\n\n---\n"
                "⚠ Generation stopped mid-thinking after hitting the token limit "
                "— no final answer was produced. Raise "
                "`journaler.max_thinking_tokens` (or free context with /clear) "
                "and try again."
            )
        return (
            text
            + "\n\n---\n"
            f"⚠ Response truncated at the {max_tokens}-token answer limit"
            + (
                f" (thinking budget {max_thinking_tokens})"
                if max_thinking_tokens
                else ""
            )
            + ". Raise `journaler.max_tokens` or ask for a continuation."
        )

    # ------------------------------------------------------------------
    # LLMBackend / agent API
    # ------------------------------------------------------------------

    def complete(self, system: str, user_message: str, max_tokens: int) -> str:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ]
        return self.chat(messages, max_tokens)

    def complete_with_tools(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> ToolAwareResponse:
        openai_messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            *messages,
        ]
        payload = self._base_payload(
            openai_messages,
            max_tokens,
            stream=False,
            tools=tools,
        )
        data = self._post_json(payload)
        message = self._extract_message(data)
        finish = self._finish_reason(data)

        tool_calls: list[ToolCall] = []
        for i, tc in enumerate(message.get("tool_calls") or []):
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            if not isinstance(fn, dict):
                continue
            name = fn.get("name")
            if not isinstance(name, str) or not name:
                continue
            tool_calls.append(
                ToolCall(
                    id=str(tc.get("id") or f"call_{i}"),
                    name=name,
                    arguments=_normalize_arguments(fn.get("arguments")),
                )
            )

        text = message.get("content")
        if isinstance(text, str) and not text.strip():
            text = None
        elif text is not None and not isinstance(text, str):
            text = str(text)

        stop = "tool_use" if tool_calls or finish == "tool_calls" else (
            finish or "end_turn"
        )
        if stop == "tool_calls":
            stop = "tool_use"

        return ToolAwareResponse(
            text=text,
            tool_calls=tool_calls,
            stop_reason=stop,
            raw=data,
        )

    def test_connection(self) -> bool:
        try:
            return probe_mlx_server(
                self._base_url,
                api_key=self._api_key,
                timeout_s=min(5.0, self._timeout_s),
            )
        except Exception as exc:
            logger.error("mlx_lm.server connection test failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Journaler conversational API
    # ------------------------------------------------------------------

    def chat(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
        max_thinking_tokens: int = 0,
    ) -> str:
        """Non-streaming chat; thinking budget is advisory (server may merge)."""
        hard_cap = max_tokens + max(0, max_thinking_tokens)
        payload = self._base_payload(list(messages), hard_cap, stream=False)
        data = self._post_json(payload)
        message = self._extract_message(data)
        text = self._compose_text(message)
        return self._append_truncation_notice(
            text,
            finish_reason=self._finish_reason(data),
            max_tokens=max_tokens,
            max_thinking_tokens=max_thinking_tokens,
        )

    def stream_generate(
        self, messages: list[dict[str, str]], max_tokens: int
    ) -> Iterator[str]:
        """Yield text deltas from SSE ``chat/completions`` streaming."""
        payload = self._base_payload(list(messages), max_tokens, stream=True)
        try:
            with requests.post(
                self._chat_url(),
                headers=self._headers(),
                json=payload,
                timeout=self._timeout_s,
                stream=True,
            ) as resp:
                resp.raise_for_status()
                reasoning_open = False
                for raw_line in resp.iter_lines(decode_unicode=True):
                    if raw_line is None:
                        continue
                    data = _parse_sse_data_lines(raw_line)
                    if data is None:
                        continue
                    choices = data.get("choices") or []
                    if not choices or not isinstance(choices[0], dict):
                        continue
                    delta = choices[0].get("delta") or {}
                    if not isinstance(delta, dict):
                        continue
                    reasoning = delta.get("reasoning")
                    if isinstance(reasoning, str) and reasoning:
                        if not reasoning_open:
                            yield "<think>\n"
                            reasoning_open = True
                        yield reasoning
                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        if reasoning_open:
                            yield "\n</think>\n"
                            reasoning_open = False
                        yield content
                if reasoning_open:
                    yield "\n</think>\n"
        except requests.ConnectionError as exc:
            raise LLMBackendError(
                f"Cannot reach mlx_lm.server at {self._base_url}. "
                "Start it with: mlx_lm.server --model <id> --host 127.0.0.1 "
                "--port 8081 --max-tokens 4096",
                provider=PROVIDER,
            ) from exc
        except requests.HTTPError as exc:
            body = ""
            status: Any = "?"
            if exc.response is not None:
                body = (exc.response.text or "")[:300]
                status = exc.response.status_code
            raise LLMBackendError(
                f"mlx_lm.server HTTP error {status}: {body}",
                provider=PROVIDER,
            ) from exc
        except LLMBackendError:
            raise
        except Exception as exc:
            raise LLMBackendError(
                f"mlx_lm.server stream failed: {exc}",
                provider=PROVIDER,
            ) from exc
