"""Structured intent classification for Journaler task routing (MLX one-shot)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from engineering_hub.journaler.engine import ConversationEngine

logger = logging.getLogger(__name__)

_INTENT_PROMPT = """You are a routing classifier for an engineering assistant. Output ONLY valid JSON, no markdown fences, no commentary.

The JSON object must have:
- "classification": one of "conversational", "immediate_task", "queued_task"

If classification is "immediate_task" or "queued_task", also include:
- "agent_type": string (research, technical-writer, standards-checker, technical-reviewer, weekly-reviewer)
- "description": string — the concrete task for the agent
- "project_id": number or null
- "keywords": array of 2-4 short keyword strings (for queued_task)
- "output_path": string or null (suggested path like outputs/research/topic.md)
- "input_paths": array of strings (file paths or wikilinks, may be empty)
- "confidence": number from 0.0 to 1.0
- "clarification_needed": boolean

STRICT RULES:
- Use "queued_task" ONLY if the user clearly asks to queue, schedule overnight, run later, add to batch, or defer execution (e.g. "queue for tonight", "overnight", "run later", "don't run now", "add to the batch", "schedule").
- Use "immediate_task" when the user wants agent work done now (draft, summarize, research, standards check, review) WITHOUT those queue signals.
- Use "conversational" for greetings, thanks, general discussion, or questions that do not require delegating a deliverable to an agent.
- If unsure between conversational and immediate_task, prefer "conversational".

User message:
"""


def _extract_json_object(text: str) -> str | None:
    text = text.strip()
    m = re.match(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if m:
        text = m.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    return text[start : end + 1]


def classify_journaler_intent(
    engine: ConversationEngine,
    user_message: str,
    *,
    max_tokens: int = 384,
) -> tuple[str, dict[str, Any]]:
    """Return (classification, payload dict). Payload empty for conversational."""
    msg = user_message.strip()
    if not msg:
        return "conversational", {}

    try:
        raw = engine._raw_complete(_INTENT_PROMPT + msg, max_tokens=max_tokens)
        blob = _extract_json_object(raw)
        if not blob:
            return "conversational", {}
        data = json.loads(blob)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.debug("Intent JSON parse failed: %s", exc)
        return "conversational", {}

    kind = str(data.get("classification", "")).lower().strip()
    if kind not in ("conversational", "immediate_task", "queued_task"):
        return "conversational", {}

    if kind == "conversational":
        return "conversational", {}

    payload = {
        "agent_type": str(data.get("agent_type", "research")).strip().lower(),
        "description": str(data.get("description", "")).strip(),
        "project_id": data.get("project_id"),
        "keywords": data.get("keywords") or [],
        "output_path": data.get("output_path"),
        "input_paths": data.get("input_paths") or [],
        "confidence": float(data.get("confidence", 0.0)),
        "clarification_needed": bool(data.get("clarification_needed", False)),
    }
    if isinstance(payload["keywords"], str):
        payload["keywords"] = [payload["keywords"]]
    if not isinstance(payload["input_paths"], list):
        payload["input_paths"] = []
    pid = payload["project_id"]
    if pid is not None:
        try:
            payload["project_id"] = int(pid)
        except (TypeError, ValueError):
            payload["project_id"] = None

    return kind, payload
