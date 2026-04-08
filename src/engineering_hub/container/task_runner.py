"""Container entry point for executing a single agent task.

Designed to run inside a Docker container:

    docker run --rm \\
        -v /tmp/payload:/task:ro \\
        -v /tmp/output:/output \\
        -e ANTHROPIC_API_KEY=... \\
        engineering-hub-task:latest

Reads ``/task/payload.json``, runs the LLM backend, writes
``/output/result.json``.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import traceback
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

TASK_DIR = Path(os.environ.get("TASK_DIR", "/task"))
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "/output"))


def _write_result(success: bool, response: str = "", error: str = "") -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    result = {"success": success, "response": response, "error": error}
    (OUTPUT_DIR / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")


def main() -> int:
    from engineering_hub.container.task_payload import TaskPayload

    payload_path = TASK_DIR / "payload.json"
    if not payload_path.is_file():
        msg = f"Payload not found at {payload_path}"
        logger.error(msg)
        _write_result(False, error=msg)
        return 1

    try:
        payload = TaskPayload.read(payload_path)
    except Exception as exc:
        msg = f"Failed to parse payload: {exc}"
        logger.error(msg)
        _write_result(False, error=msg)
        return 1

    provider = payload.backend.provider.lower()
    logger.info(f"Provider: {provider}, model: {payload.backend.model}")

    try:
        if provider == "anthropic":
            from engineering_hub.agents.backends import AnthropicBackend

            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not api_key:
                raise RuntimeError("ANTHROPIC_API_KEY env var is required")
            backend = AnthropicBackend(
                api_key=api_key,
                model=payload.backend.anthropic_model,
            )

        elif provider == "ollama":
            from engineering_hub.agents.backends import OllamaBackend, OllamaSamplingConfig

            host = os.environ.get("OLLAMA_HOST", payload.backend.ollama_host)
            sampling = OllamaSamplingConfig(
                temp=payload.backend.ollama_temp,
                top_p=payload.backend.ollama_top_p,
            )
            backend = OllamaBackend(
                host=host,
                model=payload.backend.model,
                timeout=payload.backend.ollama_timeout,
                sampling=sampling,
            )

        else:
            raise RuntimeError(
                f"Unsupported provider '{provider}' in container. "
                "Only 'anthropic' and 'ollama' are available."
            )

        logger.info("Running completion...")
        response = backend.complete(
            payload.system_prompt,
            payload.context,
            payload.backend.max_tokens,
        )
        logger.info(f"Completion finished ({len(response)} chars)")
        _write_result(True, response=response)
        return 0

    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        logger.error(f"Task failed: {msg}")
        _write_result(False, error=msg)
        return 1


if __name__ == "__main__":
    sys.exit(main())
