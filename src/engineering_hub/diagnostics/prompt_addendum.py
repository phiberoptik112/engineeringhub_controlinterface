"""Temporary diagnostic text appended to agent system prompts when enabled."""

DIAGNOSTIC_CONTEXT_AUDIT_ADDENDUM = """
## DIAGNOSTIC MODE — CONTEXT AUDIT

Before producing your output, complete this audit block verbatim:

---
CONTEXT AUDIT:
- Memory block present: [yes/no] | Relevant to task: [yes/no/partially]
- Corpus block present: [yes/no] | Relevant to task: [yes/no/partially]
- Standards listed: [list them, or "none"]
- Referenced documents: [list filenames, or "none"]
- Gaps identified: [what information was missing that would have improved the output]
- Clarifying questions I would ask before proceeding: [list, or "none"]
---

Then produce your normal output.
""".strip()
