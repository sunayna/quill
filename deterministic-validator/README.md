# Deterministic Validator (Version 1 — not yet built)

Fast, rule-based checks that run before any LLM call. No model needed — these should be pure functions over the remark text and available roster/spreadsheet columns.

## Covers (roughly these rubric rules today)
- Character count (`K1`)
- Duplicate remarks / repeated sentences (`H2`)
- Spacing, punctuation, spelling mechanics (`I1`–`I3`)
- Canonical programme/subject/sport/event naming, including required punctuation (`J1`–`J5`)
- Basic pronoun-format consistency (the format check only — actual roster verification stays with the LLM/teacher step per `B2`)

## Why this exists as a separate step
These checks are cheap and 100% repeatable. Running them before the LLM step means:
- Obvious issues get caught without spending an LLM call.
- The LLM step can focus on judgement-based rules (content, tone, safeguarding, equity) rather than re-deriving character counts.

## Interface (to be defined when built)
Should take a remark (+ rubric settings, e.g. `max_characters_including_spaces_and_line_breaks`) and return findings in the same shape as `review_output_schema.issues[]` from the rubric, so deterministic and LLM findings can be merged into one list downstream.
