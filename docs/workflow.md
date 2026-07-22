# Engineering Hub — Agent Workflow Guide

A practical reference for writing, dispatching, and reviewing agent tasks using the org-roam daily journal workflow.

---

## How It Works

```
Org journal (.org file)
    → engineering-hub run-once
        → task parser picks up - [ ] @agent: items
            → agent runs with project context + input files
                → output written to outputs/
                    → checkbox marked [x] in journal
                        → result message appended under * Engineering Hub Messages
```

The system watches your daily org-roam journal files at `~/org-roam/journal/YYYY-MM-DD.org`. Tasks written under the `* Overnight Agent Tasks` heading are picked up when you run the CLI.

---

## Directory Layout

```
~/org-roam/engineering-hub/       ← workspace root
├── config.yaml                   ← your live config
├── inputs/                       ← drop input files here
│   └── project-name/
│       ├── draft.tex
│       └── review-notes.md
└── outputs/                      ← agent output lands here
    ├── research/
    ├── docs/
    ├── reviews/
    └── analysis/

~/org-roam/journal/               ← your org-roam daily journals
    └── YYYY-MM-DD.org            ← tasks go here
```

---

## Writing a Task

Tasks go under the `* Overnight Agent Tasks` heading in today's journal file. The required format is:

```org
* Overnight Agent Tasks
- [ ] @agent-type: description [[inputs/path/file.ext]] → [[/outputs/subdir/output.md]]
```

**Rules the parser enforces:**
- Must start with `- [ ]` (space inside the brackets is required)
- `@agent-type:` must follow immediately — no extra characters between `]` and `@`
- Wikilinks use `[[double brackets]]`
- The `→` arrow is optional but recommended to specify the output path
- `[[roam:SomePage]]` links are ignored and safe to include

**Personal notes** that aren't agent tasks can sit in the same section without `@agent:`:

```org
* Overnight Agent Tasks
- TODO Check in with Jesse about LVT project tomorrow
- [ ] @research: Summarise ASTM E336 field test requirements [[django://project/42]]
```

---

## Emacs Capture Templates

The capture templates are defined inline in `$DOOMDIR/config.el`. The file `config/engineering-hub-capture.el` in this repo is kept as a reference only — do not load it, or you will get duplicate entries.

| Key | Agent dispatched |
|-----|-----------------|
| `SPC X A` | Prompt to choose agent type |
| `SPC X Ar` | `@research` |
| `SPC X Aw` | `@technical-writer` |
| `SPC X Av` | `@technical-reviewer` |
| `SPC X As` | `@standards-checker` |
| `SPC h e` | Jump to today's *Overnight Agent Tasks* heading |

---

## Available Agents

### `@research`
Synthesizes technical information into a structured markdown report with executive summary, findings, recommendations, and references. Focuses on acoustics standards and building science.

```org
- [ ] @research: Summarise ASTM E336 field test requirements [[django://project/42]]
- [ ] @research: Compare STC vs OITC ratings for operable partition systems
- [ ] @research: Review client brief [[django://project/42]] [[inputs/LVT/client-brief.pdf]]
```

**Default output dir:** `outputs/research/`

---

### `@technical-writer`
Produces complete draft documents — field reports, test protocols, executive summaries, specifications. Calibrates language to client technical level. Uses `[INSERT: ...]` placeholders where data is missing.

```org
- [ ] @technical-writer: Draft exec summary [[django://project/42]] → [[/outputs/docs/exec-42.md]]
- [ ] @technical-writer: Draft test protocol [[django://project/42]] [[inputs/NMP/site-data.md]] → [[/outputs/docs/protocol-42.md]]
```

**Default output dir:** `outputs/docs/`

---

### `@technical-reviewer`
Five-phase arbitration workflow. Takes a draft document and peer review comments, produces a decision matrix (ACCEPT / ACCEPT WITH MODIFICATION / REJECT / NEEDS VERIFICATION) and a revised document with change log.

**Input file role labeling:**
- `.tex` files → automatically labeled **Draft Document**
- `.md` / `.pdf` / `.docx` files → automatically labeled **Review Comments**

```org
- [ ] @technical-reviewer: arbitrate draft [[inputs/NMP/draft-v7.tex]] [[inputs/NMP/review-comments.md]] → [[/outputs/reviews/NMP-v7-arbitration.md]]
- [ ] @technical-reviewer: arbitrate draft [[inputs/NMP/NMP_Technical_review_Findings_v7_1.tex]] → [[/outputs/reviews/NMP_v7_1_editorial_review.md]]
```

**Default output dir:** `outputs/reviews/`

---

### `@standards-checker`
Four-phase compliance audit. Checks ASTM/ISO/IBC citations for correctness, edition years, scope coverage gaps, and mandatory vs recommended language. Produces a gap analysis with an overall PASS / CONDITIONAL PASS / FAIL assessment.

```org
- [ ] @standards-checker: Verify E1007 compliance for [[django://project/42]]
- [ ] @standards-checker: Check draft [[django://project/42]] [[inputs/NMP/draft-report.docx]]
```

**Default output dir:** `outputs/analysis/`

---

## Referencing Input Files

Place files in the workspace inputs directory before running a task:

```
~/org-roam/engineering-hub/inputs/project-name/file.ext
```

Reference them with a short relative path in the task:

```org
[[inputs/project-name/file.ext]]
```

The system also accepts absolute paths and paths relative to the workspace root. If a referenced file does not exist when `run-once` is called, the task **fails immediately** with a clear error message before making any API call — check the terminal output for the exact path it expected.

**Supported formats:** `.pdf`, `.docx`, `.tex`, `.md`, and any plain text file.

---

## Django Project References

Link to a project in the Django backend to give the agent full project context (scope, standards, client name, budget, recent files):

```org
[[django://project/42]]
```

Tasks without a Django reference still run but receive minimal context. For `@research` and `@technical-writer` tasks, including the project reference significantly improves output relevance.

---

## Specifying an Output Path

Use the `→` arrow to control exactly where the output file is written:

```org
→ [[/outputs/reviews/NMP_v7_1_editorial_review.md]]
```

The path is relative to the workspace root (`~/org-roam/engineering-hub/`). If you omit the arrow, the system auto-generates a filename under the agent's default output subdirectory.

---

## Running Tasks

### Process pending tasks now and exit

```bash
cd ~/dev/engineeringhub_controlinterface
source .venv/bin/activate
engineering-hub run-once
```

Picks up all `- [ ]` tasks from the last 3 days of journal files (3-day lookback covers weekends), runs them, marks each checkbox `[x]` on completion, and appends a result message under `* Engineering Hub Messages` in the same journal file.

### Check what tasks are pending (no API calls)

```bash
engineering-hub status
```

### Watch the journal directory and dispatch tasks automatically on save

```bash
engineering-hub start
```

Stays running until `Ctrl-C`. Triggers on any save to the journal directory — useful for overnight/background use.

### Run the weekly review

```bash
engineering-hub weekly-review
# or look back 14 days with a focus area:
engineering-hub weekly-review --days 14 --focus "NMP project and client delivery"
```

Reads journal entries + agent memory for the period and produces a structured weekly synthesis at `outputs/reviews/weekly-YYYY-WNN.md`. This agent is invoked via CLI only — do not add it as a journal task.

---

## Zettelkasten Proposal Workflow

Marked daily-journal ideas can be mined into reviewable permanent-note proposals:

```org
* Notes
The acoustic value of an operable partition depends on installed boundary conditions. #idea
```

Run:

```bash
engineering-hub zettel propose
```

This scans recent daily journals for configured markers (`#idea`, `#extract`, `TODO extract`, `TODO: extract`) and writes two files under `outputs/zettelkasten/`:

- `<batch>.org` — a human-readable review buffer
- `<batch>.json` — the approved/apply source of truth

After reviewing or editing the JSON sidecar, apply approved notes:

```bash
engineering-hub zettel apply outputs/zettelkasten/<batch>.json
```

The apply step writes permanent org-roam nodes with `:ID:`, `#+title:`, source links back to the daily journal, tags, and conservative related-link suggestions. The workflow records source-span hashes in `.journaler/zettelkasten_state.json` so unchanged journal spans are not proposed repeatedly.

In `journaler chat`, the same flow is available with `/zettel propose [days]`, `/zettel status`, and `/zettel apply <proposal-json>`.

---

## How Tasks Are Tracked

After a run, your journal file is updated in place:

```org
* Overnight Agent Tasks
- [x] @technical-reviewer: arbitrate draft [[inputs/NMP/draft-v7.tex]] → [[/outputs/reviews/NMP-v7.md]]

* Engineering Hub Messages

** [2026-03-06 22:45] @technical-reviewer
Task completed successfully.
Output: [[/Users/jakepfitsch/org-roam/engineering-hub/outputs/reviews/NMP-v7.md]]
```

A task that fails (e.g. missing input file) is marked `(blocked: ...)` rather than `[x]`, and the error message is written to `* Engineering Hub Messages`.

---

## Lookback Windows

| Purpose | Setting | Default |
|---------|---------|---------|
| Task pickup | `org_lookback_days` | 3 days (covers weekend gap) |
| Agent history context | `org_context_lookback_days` | 7 days |

If a task is not picked up, check that it was written within the last 3 days and uses correct `- [ ] @agent:` syntax. Tasks marked `[x]`, `(in progress)`, or `(blocked:...)` are skipped.

---

## Disabled Agents (Phase 5)

These agent types are registered in the codebase but currently disabled. Using them in a task will produce an immediate error:

| Token | Status |
|-------|--------|
| `ref_engineer` | Disabled |
| `evaluator` | Disabled |
