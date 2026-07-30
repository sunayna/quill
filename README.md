# Quillwarden — Project

A rubric-driven evaluation engine, starting with report-card remarks and planned to grow toward evaluating student answers and assignments later. See **`ARCHITECTURE.md`** for the full platform roadmap (Version 1 workflow → Version 2 retrieval → Version 3+ evidence & verification). This README covers what exists *today*.

## How the pieces fit together

```
quillwarden/
├── ARCHITECTURE.md                    ← long-term platform roadmap (read this first)
├── rubric/
│   └── quillwarden_rubric.json        ← source of truth. All rules, severities,
│                                         settings, and the required AI output schema.
├── prompts/
│   └── system_prompt.md               ← paste into any LLM chat, alongside the
│                                         rubric JSON and the remarks spreadsheet.
├── docs/
│   ├── teacher_checklist.md           ← plain-text version of the one-page checklist
│   ├── Quillwarden_Teacher_Checklist.docx    ← formatted, printable version
│   └── how_to_check_report_remarks.md ← the 4-step process teachers follow
├── deterministic-validator/           ← Version 1 (not yet built) — fast rule-based checks
├── retrieval/                         ← Version 2 (not yet built) — similar-example search
├── evidence-store/                    ← Version 3+ (not yet built) — verified student records
├── feedback-db/                       ← Version 1, incremental (not yet built) — teacher decisions
├── apps-script/
│   └── Code.gs                        ← standalone Google Apps Script version — runs the
│                                         deterministic checks plus a batched Gemini call
│                                         directly inside the remarks Google Sheet via a
│                                         custom menu, no server required
├── archive/                           ← superseded versions, kept for reference
└── CHANGELOG.md                       ← what changed between rubric versions
```

## The workflow

1. **Teacher writes remarks** in the school spreadsheet, self-checking against `docs/teacher_checklist.md` (or the printable `.docx`).
2. **AI check** — in a fresh chat with any capable LLM: paste `prompts/system_prompt.md`, upload `rubric/quillwarden_rubric.json`, upload that batch's remarks spreadsheet.
3. **Teacher corrects** flagged issues directly in the spreadsheet, then re-runs the check on just the revisions.
4. **Teacher submits** once all `critical` and `required` issues are resolved.

The full process is written out for teachers in `docs/how_to_check_report_remarks.md`.

Two automated alternatives to the manual copy-paste-into-a-chat flow above also exist: a Flask app (`app.py`, deployed via ngrok for remote access) and a Google Apps Script (`apps-script/Code.gs`) that runs the same checks directly inside the remarks Google Sheet — see the setup comment at the top of that file.

## Two things this system is built to guarantee

- **The AI never rewrites a remark.** It only flags issues and explains them (`constraints.reviewer_must_not_rewrite_remark` in the rubric, reinforced in the system prompt).
- **The teacher owns final accuracy and wording.** The AI can flag an unsupported claim; it cannot verify it against actual grades or records. Anything tagged `requires_record_verification: true` in the rubric needs the teacher to check the school's own records.

## Rubric versioning

The rubric is versioned (`rubric_name` / `version` fields at the top of the JSON). See `CHANGELOG.md` for what changed and why between versions — this matters because severities and settings drive the AI's behavior directly, so a rubric edit changes what gets flagged school-wide.

## Extending this project

The phased plan for turning today's manual workflow into an actual platform lives in `ARCHITECTURE.md`. Short version: build the deterministic validator and a real workflow chain first (Version 1), add retrieval once there's real feedback data to search over (Version 2), and only reach for evidence verification / agent behavior once there's a student evidence store to make those decisions meaningful (Version 3+). Each scaffolded folder (`deterministic-validator/`, `retrieval/`, `evidence-store/`, `feedback-db/`) has its own README with the specifics for that component.
