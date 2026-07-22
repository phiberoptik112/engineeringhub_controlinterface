"""Helpers for thinking-model output (Qwen3-style ``<think>...</think>`` blocks).

Thinking models interleave a reasoning transcript with the final answer.
These helpers track tag state during streamed generation (so thinking and
answer tokens can be budgeted separately) and strip reasoning blocks from
text that is stored in conversation history or written to notes.
"""

from __future__ import annotations

import re

THINK_OPEN = "<think>"
THINK_CLOSE = "</think>"
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


def strip_think_blocks(text: str) -> str:
    """Remove ``<think>...</think>`` reasoning blocks from *text*.

    Handles two degenerate forms produced by real models:

    - A trailing unclosed ``<think>`` block (thinking truncated at the token
      limit) is removed.
    - An orphan leading ``</think>`` with no opening tag (Qwen3.5/3.6-style
      templates prime the prompt with ``<think>``, so the output contains only
      the close tag): everything up to and including the close tag is thinking.
    """
    close_idx = text.find(THINK_CLOSE)
    open_idx = text.find(THINK_OPEN)
    if close_idx != -1 and (open_idx == -1 or close_idx < open_idx):
        text = text[close_idx + len(THINK_CLOSE):]
    cleaned = _THINK_BLOCK_RE.sub("", text)
    open_idx = cleaned.find(THINK_OPEN)
    if open_idx != -1:
        cleaned = cleaned[:open_idx]
    return cleaned.strip()


class ThinkTagTracker:
    """Incrementally tracks ``<think>`` tag state across streamed text segments.

    Pass ``start_open=True`` when the chat template primes the assistant turn
    with ``<think>`` (Qwen3.5/3.6): the model is then already thinking from the
    first generated token and only emits the closing ``</think>`` itself.
    """

    _TAIL_KEEP = len(THINK_CLOSE) - 1

    def __init__(self, start_open: bool = False) -> None:
        self.open = start_open
        self.saw_think = start_open
        self._tail = ""

    def feed(self, segment: str) -> None:
        s = self._tail + segment
        pos = 0
        while True:
            tag = THINK_CLOSE if self.open else THINK_OPEN
            idx = s.find(tag, pos)
            if idx == -1:
                break
            if self.open:
                self.open = False
            else:
                self.open = True
                self.saw_think = True
            pos = idx + len(tag)
        # Keep only unprocessed trailing chars so a tag split across segment
        # boundaries is still detected, without re-matching processed tags.
        self._tail = s[max(pos, len(s) - self._TAIL_KEEP):]


def prompt_primes_thinking(prompt: str) -> bool:
    """Return True when the rendered chat prompt ends inside a ``<think>`` block.

    Qwen3.5/3.6-style templates append ``<think>\\n`` after the generation
    prompt (thinking mode) or a closed ``<think>\\n\\n</think>\\n\\n`` pair
    (thinking disabled). When the last ``<think>`` in the prompt is unclosed,
    generation starts mid-thought and the output will contain only the closing
    tag.
    """
    last_open = prompt.rfind(THINK_OPEN)
    if last_open == -1:
        return False
    return prompt.find(THINK_CLOSE, last_open) == -1
