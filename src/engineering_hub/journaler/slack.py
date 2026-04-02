"""Slack webhook integration for the Journaler daemon.

Posts messages to Slack via incoming webhooks.  No bot framework, no OAuth
— just HTTP POST.  Start with one-way (Journaler -> Slack); add bidirectional
Slack bot support later if briefings prove valuable enough to reply to.
"""

from __future__ import annotations

import logging
import re

import requests

logger = logging.getLogger(__name__)


class SlackPoster:
    """Posts messages to Slack via incoming webhook."""

    def __init__(self, webhook_url: str) -> None:
        self.webhook_url = webhook_url

    def post_briefing(self, briefing_markdown: str) -> bool:
        """Post the morning briefing.  Converts markdown to Slack mrkdwn."""
        blocks = self._format_briefing_blocks(briefing_markdown)
        return self._post(blocks)

    def post_alert(self, message: str) -> bool:
        """Post a short alert (stalled task, deadline approaching, etc.)."""
        return self._post([
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": message},
            }
        ])

    def _post(self, blocks: list[dict]) -> bool:
        """Send to Slack webhook.  Non-fatal on failure."""
        try:
            resp = requests.post(
                self.webhook_url,
                json={"blocks": blocks},
                timeout=10,
            )
            if resp.status_code == 200:
                logger.info("Slack post succeeded")
                return True
            logger.warning(f"Slack returned {resp.status_code}: {resp.text[:200]}")
            return False
        except Exception as exc:
            logger.warning(f"Slack post failed (non-fatal): {exc}")
            return False

    def _format_briefing_blocks(self, markdown: str) -> list[dict]:
        """Convert markdown briefing to Slack Block Kit format.

        Slack uses mrkdwn (not markdown):
          *bold*, _italic_, `code`, ~strike~, > blockquote
        Markdown differences: **bold** -> *bold*, # heading -> *heading*
        """
        mrkdwn = _markdown_to_mrkdwn(markdown)

        # Split on ## headings into sections with dividers
        sections = re.split(r"(?m)^\*\*(.+?)\*\*\n", mrkdwn)
        blocks: list[dict] = []

        # First chunk (before first heading) is preamble
        if sections[0].strip():
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": sections[0].strip()[:3000]},
            })

        # Remaining chunks alternate: heading, body, heading, body...
        for i in range(1, len(sections), 2):
            heading = sections[i].strip()
            body = sections[i + 1].strip() if i + 1 < len(sections) else ""

            blocks.append({"type": "divider"})
            blocks.append({
                "type": "header",
                "text": {"type": "plain_text", "text": heading[:150]},
            })
            if body:
                # Slack has a 3000 char limit per text block
                text = body[:3000]
                blocks.append({
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": text},
                })

        return blocks or [{
            "type": "section",
            "text": {"type": "mrkdwn", "text": mrkdwn[:3000]},
        }]


def _markdown_to_mrkdwn(text: str) -> str:
    """Best-effort markdown -> Slack mrkdwn conversion."""
    # Headings: # Foo -> *Foo*
    text = re.sub(r"^#{1,6}\s+(.+)$", r"**\1**", text, flags=re.MULTILINE)
    # Bold: **text** -> *text*  (Slack uses single * for bold)
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    # Links: [text](url) -> <url|text>
    text = re.sub(r"\[(.+?)\]\((.+?)\)", r"<\2|\1>", text)
    return text
