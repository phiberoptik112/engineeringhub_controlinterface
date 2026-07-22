"""Task-Integrator: a call-and-response loop over the daily org journal.

The user writes free-form notes in their daily journal.  This loop reads the
whole note body (minus agent-managed sections), interviews the user inline in
an ``* Agent Conversation`` section, and — once the user replies inline —
proposes ready-to-run ``@agent:`` tasks behind a checkbox.  Ticking a box
promotes a proposal into the ``* Overnight Agent Tasks`` queue that the
Orchestrator already scans, so the user never has to remember the delegation
syntax.

State (which chunks have been interviewed, which conversations are awaiting a
reply, which proposals have been queued) lives in a JSON file under the state
directory.  All org interaction is append-only to today's journal, anchored by
a ``:CONV_ID:`` drawer property, so no fragile in-place edits are required.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from engineering_hub.journaler.briefing_tasks import _read_recent_chat_context
from engineering_hub.journaler.org_writer import (
    _create_journal_file,
    _today_journal_path,
    append_to_today_journal,
    read_section_body,
)

if TYPE_CHECKING:
    from engineering_hub.journaler.delegator import AgentDelegator
    from engineering_hub.journaler.engine import ConversationEngine

logger = logging.getLogger(__name__)

DEFAULT_EXCLUDED_SECTIONS = (
    "Agent Conversation",
    "Overnight Agent Tasks",
    "Completed Agent Tasks",
    "Pending Agent Tasks",
    "Timesheet",
    "Journaler Cross-References",
    "Morning Briefing",
    "Discussion Briefing",
)

_REPLY_MARKER = "Reply (type your answer below this line):"

INTERVIEW_PROMPT = """\
You are the Journaler Task-Integrator.  The user wrote the free-form daily \
notes below.  Identify implied or explicit tasks/topics that an AI agent could \
eventually help with, and for each, ask a few short clarifying interview \
questions so the task can later be delegated cleanly.

Rules:
- Only surface items that are genuinely actionable by an agent (research, \
draft, audit, scan, review, summarize, plan).  Ignore pure logging, feelings, \
or physical chores.
- Ask at most {max_questions} questions per topic; questions must be specific \
(scope, acceptance criteria, deadline, which artifact/project, which agent).
- Do NOT invent topics that are not grounded in the notes.

For each topic, output ONE JSON object on its own line (JSONL):
{{"topic": "<short title>", "questions": ["q1", "q2"]}}

Output ONLY JSON lines, no commentary.  If nothing is actionable, output nothing.

## Daily Notes
{notes}

## Recent Chat Activity
{chat_excerpt}

## Available Agent Skills
{skills_list}
"""

RESOLUTION_PROMPT = """\
You are the Journaler Task-Integrator.  Earlier you asked the user interview \
questions about a topic from their daily notes.  Using the topic, your \
questions, and the user's reply below, draft concrete agent tasks ready for \
delegation.

Rules:
- Each task must map to one of the available agent skills (use its exact \
agent_type).
- Write a self-contained description: what to produce, scope, and any \
artifact/project reference the user gave.
- Prefer 1-2 tasks.  If the reply is too vague to act on, output nothing.

For each task, output ONE JSON object on its own line (JSONL):
{{"agent_type": "<skill agent_type>", "description": "<full task description>"}}

Output ONLY JSON lines, no commentary.

## Topic
{topic}

## Your Questions
{questions}

## User Reply
{reply}

## Available Agent Skills
{skills_list}
"""


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class Proposal:
    """A single proposed agent task awaiting user approval."""

    agent_type: str
    description: str
    approved: bool = False
    queued: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Proposal:
        allowed = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in allowed})

    def task_line(self) -> str:
        return f"@{self.agent_type}: {self.description}"


@dataclass
class ConversationEntry:
    """A single inline interview thread anchored by ``:CONV_ID:`` in the org file."""

    id: str
    source_hash: str
    topic: str
    status: str  # "awaiting_reply" | "proposed" | "queued"
    questions: list[str] = field(default_factory=list)
    reply_hash: str = ""
    proposals: list[Proposal] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        data["proposals"] = [p.to_dict() for p in self.proposals]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> ConversationEntry:
        allowed = {f.name for f in cls.__dataclass_fields__.values()}
        kwargs = {k: v for k, v in data.items() if k in allowed}
        kwargs["proposals"] = [
            Proposal.from_dict(p) for p in data.get("proposals", [])
        ]
        return cls(**kwargs)


@dataclass
class DayState:
    """Per-day Task-Integrator state."""

    conversations: list[ConversationEntry] = field(default_factory=list)
    processed_source_hashes: list[str] = field(default_factory=list)

    def find(self, conv_id: str) -> ConversationEntry | None:
        for c in self.conversations:
            if c.id == conv_id:
                return c
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash(text: str) -> str:
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()[:12]


def _org_timestamp() -> str:
    now = datetime.now()
    return now.strftime(f"[%Y-%m-%d {now.strftime('%a')} %H:%M]")


_SUBHEADING_RE = re.compile(r"^\*\* (.+)$", re.MULTILINE)
_APPROVED_LINE_RE = re.compile(
    r"^\s*[-*]\s+\[[xX]\]\s+@(?P<agent>[\w-]+):\s+(?P<text>.+?)\s*$",
    re.MULTILINE,
)


def _split_subblocks(body: str) -> list[str]:
    """Split a section body into ``** subheading`` blocks (with bodies)."""
    matches = list(_SUBHEADING_RE.finditer(body))
    if not matches:
        return []
    blocks: list[str] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        blocks.append(body[start:end])
    return blocks


def _drawer_prop(block: str, key: str) -> str:
    m = re.search(rf"^\s*:{re.escape(key)}:\s*(.+)$", block, re.MULTILINE)
    return m.group(1).strip() if m else ""


def _reply_text(block: str) -> str:
    """Return the user's reply text from an awaiting-reply block, if any."""
    idx = block.find(_REPLY_MARKER)
    if idx == -1:
        return ""
    return block[idx + len(_REPLY_MARKER):].strip()


# ---------------------------------------------------------------------------
# State store
# ---------------------------------------------------------------------------


class TaskIntegratorState:
    """JSON-backed per-day store mirroring the BackgroundWorkQueue style."""

    def __init__(self, state_dir: Path) -> None:
        self._dir = state_dir / "task_integrator"
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, day: date | None = None) -> Path:
        return self._dir / f"{(day or date.today()).isoformat()}.json"

    def load(self, day: date | None = None) -> DayState:
        path = self._path(day)
        if not path.exists():
            return DayState()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load task-integrator state %s: %s", path, exc)
            return DayState()
        return DayState(
            conversations=[
                ConversationEntry.from_dict(c) for c in data.get("conversations", [])
            ],
            processed_source_hashes=list(data.get("processed_source_hashes", [])),
        )

    def save(self, state: DayState, day: date | None = None) -> None:
        path = self._path(day)
        payload = {
            "conversations": [c.to_dict() for c in state.conversations],
            "processed_source_hashes": state.processed_source_hashes,
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Task Integrator
# ---------------------------------------------------------------------------


@dataclass
class CycleResult:
    """Summary of one ``run_cycle`` invocation."""

    questions_asked: int = 0
    proposals_written: int = 0
    tasks_queued: int = 0
    awaiting_reply: int = 0
    skipped_reason: str = ""

    def summary(self) -> str:
        if self.skipped_reason:
            return f"Task-Integrator: {self.skipped_reason}"
        return (
            f"Task-Integrator: asked {self.questions_asked} question set(s), "
            f"wrote {self.proposals_written} proposal(s), "
            f"queued {self.tasks_queued} task(s); "
            f"{self.awaiting_reply} thread(s) awaiting reply."
        )


class TaskIntegrator:
    """Drives the inline interview/proposal/approval loop over the daily note."""

    def __init__(
        self,
        *,
        engine: ConversationEngine,
        delegator: AgentDelegator | None,
        journal_dir: Path,
        state_dir: Path,
        conversation_section: str = "Agent Conversation",
        output_section: str = "Overnight Agent Tasks",
        excluded_sections: list[str] | None = None,
        max_questions: int = 3,
        weekdays_only: bool = True,
        max_tokens: int = 1024,
        chat_lookback_days: int = 3,
    ) -> None:
        self._engine = engine
        self._delegator = delegator
        self._journal_dir = journal_dir.expanduser().resolve()
        self._state_dir = state_dir
        self._conversation_section = conversation_section
        self._output_section = output_section
        excluded = set(excluded_sections or DEFAULT_EXCLUDED_SECTIONS)
        # The agent-managed sections must always be excluded from intake.
        excluded.add(conversation_section)
        excluded.add(output_section)
        self._excluded_sections = excluded
        self._max_questions = max_questions
        self._weekdays_only = weekdays_only
        self._max_tokens = max_tokens
        self._chat_lookback_days = chat_lookback_days
        self._store = TaskIntegratorState(state_dir)

    @property
    def conversation_section(self) -> str:
        return self._conversation_section

    @property
    def output_section(self) -> str:
        return self._output_section

    # -- skills ----------------------------------------------------------

    def _skills_list(self) -> str:
        if self._delegator is None:
            return "(no agent skills available)"
        skills = self._delegator.list_skills()
        if not skills:
            return "(no agent skills loaded)"
        return "\n".join(
            f"- {s.agent_type}: {s.description.splitlines()[0] if s.description else s.display_name}"
            for s in skills
        )

    def _resolve_agent_type(self, name: str) -> str:
        if self._delegator is not None:
            resolved = self._delegator.resolve_agent_type(name)
            if resolved:
                return resolved
        return name.strip() or "research"

    # -- intake ----------------------------------------------------------

    def _today_path(self) -> Path:
        return _today_journal_path(self._journal_dir)

    def _read_intake_chunks(self) -> list[tuple[str, str]]:
        """Return ``(chunk_text, hash)`` units from non-managed note sections."""
        path = self._today_path()
        if not path.is_file():
            return []
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return []

        # Split on top-level headings; keep only non-excluded section bodies.
        heading_re = re.compile(r"^\* (.+)$", re.MULTILINE)
        matches = list(heading_re.finditer(raw))
        bodies: list[str] = []
        for i, m in enumerate(matches):
            heading = m.group(1).strip()
            # Strip any trailing org tags on the heading (e.g. ":foo:")
            heading_name = re.sub(r"\s+:[\w:@]+:\s*$", "", heading).strip()
            if heading_name in self._excluded_sections:
                continue
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
            bodies.append(raw[start:end])

        chunks: list[tuple[str, str]] = []
        for body in bodies:
            for para in re.split(r"\n\s*\n", body):
                text = para.strip()
                # Drop drawers and short noise.
                if not text or text.startswith(":"):
                    continue
                if len(text) < 12:
                    continue
                chunks.append((text, _hash(text)))
        return chunks

    # -- conversation section parsing ------------------------------------

    def _conversation_blocks(self) -> list[str]:
        body = read_section_body(self._today_path(), self._conversation_section)
        return _split_subblocks(body)

    # -- phases ----------------------------------------------------------

    def _phase_interview(self, state: DayState) -> int:
        chunks = self._read_intake_chunks()
        processed = set(state.processed_source_hashes)
        new_chunks = [(t, h) for (t, h) in chunks if h not in processed]
        if not new_chunks:
            return 0

        notes = "\n\n".join(t for (t, _) in new_chunks)
        chat_excerpt = _read_recent_chat_context(
            self._state_dir, lookback_days=self._chat_lookback_days
        )
        prompt = INTERVIEW_PROMPT.format(
            max_questions=self._max_questions,
            notes=notes[:8000],
            chat_excerpt=chat_excerpt,
            skills_list=self._skills_list(),
        )
        try:
            raw = self._engine._raw_complete(prompt, max_tokens=self._max_tokens)
        except Exception as exc:
            logger.warning("Task-Integrator interview failed: %s", exc)
            return 0

        topics = self._parse_interview(raw)

        # Mark all new chunks processed regardless, to avoid re-interviewing.
        for _, h in new_chunks:
            if h not in processed:
                state.processed_source_hashes.append(h)
                processed.add(h)

        combined_hash = _hash(notes)
        asked = 0
        for topic, questions in topics:
            if not questions:
                continue
            conv_id = uuid.uuid4().hex[:8]
            now = datetime.now().isoformat(timespec="seconds")
            entry = ConversationEntry(
                id=conv_id,
                source_hash=combined_hash,
                topic=topic,
                status="awaiting_reply",
                questions=questions[: self._max_questions],
                created_at=now,
                updated_at=now,
            )
            state.conversations.append(entry)
            self._write_question_block(entry)
            asked += 1
        return asked

    def _phase_resolution(self, state: DayState) -> int:
        blocks = self._conversation_blocks()
        by_id: dict[str, str] = {}
        for block in blocks:
            cid = _drawer_prop(block, "CONV_ID")
            if cid:
                by_id[cid] = block

        written = 0
        for entry in state.conversations:
            if entry.status != "awaiting_reply":
                continue
            block = by_id.get(entry.id, "")
            if not block:
                continue
            reply = _reply_text(block)
            if not reply:
                continue
            reply_h = _hash(reply)
            if reply_h == entry.reply_hash:
                continue  # already processed this reply
            entry.reply_hash = reply_h

            proposals = self._resolve_reply(entry, reply)
            if not proposals:
                continue
            entry.proposals = proposals
            entry.status = "proposed"
            entry.updated_at = datetime.now().isoformat(timespec="seconds")
            self._write_proposal_block(entry)
            written += 1
        return written

    def _phase_approval(self, state: DayState) -> int:
        if self._weekdays_only and date.today().weekday() >= 5:
            return 0

        blocks = self._conversation_blocks()
        # Map proposal-block (anchored by PROPOSAL_FOR) -> approved task lines.
        approved_by_conv: dict[str, set[str]] = {}
        for block in blocks:
            cid = _drawer_prop(block, "PROPOSAL_FOR")
            if not cid:
                continue
            approved = approved_by_conv.setdefault(cid, set())
            for m in _APPROVED_LINE_RE.finditer(block):
                approved.add(f"@{m.group('agent')}: {m.group('text')}".strip())

        queued = 0
        for entry in state.conversations:
            if entry.status not in ("proposed", "queued"):
                continue
            approved_lines = approved_by_conv.get(entry.id, set())
            if not approved_lines:
                continue
            for proposal in entry.proposals:
                if proposal.queued:
                    continue
                if proposal.task_line() not in approved_lines:
                    continue
                ok, _msg = append_to_today_journal(
                    self._journal_dir,
                    self._output_section,
                    f"- [ ] @{proposal.agent_type}: {proposal.description}",
                )
                if ok:
                    proposal.approved = True
                    proposal.queued = True
                    queued += 1
            if entry.proposals and all(p.queued for p in entry.proposals):
                entry.status = "queued"
            entry.updated_at = datetime.now().isoformat(timespec="seconds")
        return queued

    # -- org writers -----------------------------------------------------

    def _write_question_block(self, entry: ConversationEntry) -> None:
        lines = [
            f"** {_org_timestamp()} {entry.topic}",
            ":PROPERTIES:",
            f":CONV_ID: {entry.id}",
            ":END:",
        ]
        for i, q in enumerate(entry.questions, start=1):
            lines.append(f"Q{i}: {q}")
        lines.append("")
        lines.append(_REPLY_MARKER)
        lines.append("")
        append_to_today_journal(
            self._journal_dir, self._conversation_section, "\n".join(lines)
        )

    def _write_proposal_block(self, entry: ConversationEntry) -> None:
        lines = [
            f"** {_org_timestamp()} Proposed tasks (re: {entry.id})",
            ":PROPERTIES:",
            f":PROPOSAL_FOR: {entry.id}",
            ":END:",
            "Tick a box to approve; approved tasks are queued to "
            f"* {self._output_section}.",
        ]
        for proposal in entry.proposals:
            lines.append(f"- [ ] @{proposal.agent_type}: {proposal.description}")
        lines.append("")
        append_to_today_journal(
            self._journal_dir, self._conversation_section, "\n".join(lines)
        )

    # -- parsers ---------------------------------------------------------

    def _parse_interview(self, raw: str) -> list[tuple[str, list[str]]]:
        out: list[tuple[str, list[str]]] = []
        for line in raw.strip().splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            topic = str(obj.get("topic", "")).strip()
            questions = [str(q).strip() for q in obj.get("questions", []) if str(q).strip()]
            if topic and questions:
                out.append((topic, questions))
        return out

    def _resolve_reply(
        self, entry: ConversationEntry, reply: str
    ) -> list[Proposal]:
        prompt = RESOLUTION_PROMPT.format(
            topic=entry.topic,
            questions="\n".join(f"Q{i}: {q}" for i, q in enumerate(entry.questions, 1)),
            reply=reply[:4000],
            skills_list=self._skills_list(),
        )
        try:
            raw = self._engine._raw_complete(prompt, max_tokens=self._max_tokens)
        except Exception as exc:
            logger.warning("Task-Integrator resolution failed: %s", exc)
            return []

        proposals: list[Proposal] = []
        for line in raw.strip().splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            desc = str(obj.get("description", "")).strip()
            if not desc:
                continue
            agent_type = self._resolve_agent_type(str(obj.get("agent_type", "research")))
            proposals.append(Proposal(agent_type=agent_type, description=desc))
        return proposals

    # -- public API ------------------------------------------------------

    def run_cycle(self) -> CycleResult:
        """Run the interview, resolution, and approval phases once."""
        # Ensure today's journal exists so reads/writes are consistent.
        _create_journal_file(self._today_path())

        state = self._store.load()
        result = CycleResult()
        try:
            result.questions_asked = self._phase_interview(state)
            result.proposals_written = self._phase_resolution(state)
            result.tasks_queued = self._phase_approval(state)
        finally:
            self._store.save(state)

        result.awaiting_reply = sum(
            1 for c in state.conversations if c.status == "awaiting_reply"
        )
        return result

    def status_summary(self) -> str:
        """Return a human-readable status line for today's conversations."""
        state = self._store.load()
        counts: dict[str, int] = {"awaiting_reply": 0, "proposed": 0, "queued": 0}
        for c in state.conversations:
            counts[c.status] = counts.get(c.status, 0) + 1
        return (
            f"Task-Integrator status ({date.today().isoformat()}): "
            f"{counts['awaiting_reply']} awaiting reply, "
            f"{counts['proposed']} proposed (awaiting approval), "
            f"{counts['queued']} queued."
        )


def build_task_integrator(
    config,
    engine: ConversationEngine,
    delegator: AgentDelegator | None,
) -> TaskIntegrator:
    """Construct a :class:`TaskIntegrator` from a ``JournalerConfig``.

    Shared by the daemon and ``journaler chat`` so both honor the same
    org-roam section configuration.
    """
    return TaskIntegrator(
        engine=engine,
        delegator=delegator,
        journal_dir=config.journal_dir,
        state_dir=config.state_dir,
        conversation_section=config.task_integrator_conversation_section,
        output_section=config.task_integrator_output_section,
        excluded_sections=config.task_integrator_excluded_sections,
        max_questions=config.task_integrator_max_questions,
        weekdays_only=config.task_integrator_weekdays_only,
        max_tokens=config.task_integrator_max_tokens,
        chat_lookback_days=config.background_work_chat_lookback_days,
    )
