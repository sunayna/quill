# Changelog

## v1.2 — Stop flagging pronouns as unverifiable when no roster data exists
- **B2 (pronouns):** removed the `required`-severity "cannot be verified against roster" condition, which fired on every remark whenever the uploaded file had no pronoun/roster column — producing repetitive, low-value noise. B2 now only flags a `critical` issue when a pronoun actually contradicts roster data (if provided) or is internally inconsistent within the remark itself (e.g. switches between he/she for the same student).
- Updated `severity_levels.required.triggers`, `final_checklist_reference`, and `prompts/system_prompt.md` to match.

## v1.1 — Structural rework for application use
- Added top-level `settings` block (`allow_strengths_only`, `require_growth_point`, `require_closing`, character limits, language variant, verification flags, etc.).
- Added top-level `constraints` block, encoding `reviewer_must_not_rewrite_remark: true` explicitly rather than leaving it implied by the purpose statement.
- Added `review_output_schema` — a fixed output contract (`student_name`, `status`, `issues[]`, `character_count`, `teacher_revision_required`) so different LLMs return consistent, parseable results.
- Restructured every rule to a consistent shape: `policy`, `detection`, `exceptions`, `severity` (or `severity_conditions`), `teacher_message`.
- **B2 (pronouns):** split into graded severity — roster mismatch is `critical`; unverifiable is `required` with a `teacher_action` to check the roster. Previously always `critical`.
- **B5 (attendance):** split into three conditions — disclosed private/medical/family reason (`critical`), over-detailed but non-sensitive (`required`), general continuity statement (`none`). Previously always `critical`.
- **E3 (repeated directives):** now only flags when directive language ("must," "needs to," "should," "has to") repeats past `settings.e3_directive_repeat_threshold` (default 3) or combines with harsh phrasing. Previously could flag a single instance.
- **F4 (screen time):** added an exception path via `settings.screen_time_reporting_authorised` for schools where digital habits are a formal part of reporting.
- **J1 ("Bully to Buddy"):** canonical form now explicit — `requires_quotation_marks: true` — instead of an unmarked string.
- **J3/J4 (subject and sport capitalisation):** marked `configurable: true`, tied to `settings.subject_capitalisation_style` / `settings.sport_capitalisation_style`, since house style varies by school.
- **K1 (character limit):** clarified that line breaks count toward the 1,000-character limit.
- **A5 (balance):** now explicitly deferential to `settings.allow_strengths_only` rather than describing the exception only in prose.
- **A1 (clear strength):** no longer requires a supporting example in every case — only "where appropriate."
- **A4 (growth point):** relaxed from requiring the full issue/action/benefit structure to requiring only a specific, actionable statement of what to practise or improve. The fuller structure moved to `E1` as a "preferred, not required" refinement.
- **New rule C0:** observable behaviour vs. personality judgement (e.g. "submits work on time" vs. "is lazy").

## v1.0 — Initial rubric
- Original 13-section rubric (A–N) covering required content, accuracy, tone, inflated language, development points, safeguarding, equity, internal consistency, mechanics, naming conventions, length, and closings.
- Four severity levels: critical, required, warning, none.
- No machine-readable settings, constraints, or output schema — rules described policy and examples only.
