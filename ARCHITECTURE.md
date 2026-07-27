# Architecture — Quillwarden Verification Platform

> Status: **planning document**. Nothing described past "Version 1" is built yet. This file exists so Claude Code has the long-term shape of the system before writing code, and so each phase gets built in the right order instead of skipped ahead of.

## 1. Core idea

The system is a **rubric-driven evaluation engine**, not a remark-checker specifically.

Remarks are the first use case because the rubric already exists and the workflow is well understood. The same engine — deterministic checks → rubric retrieval → LLM evaluation → human review → feedback capture — is meant to later evaluate student answers, projects, and assignments without changing its core shape. Design decisions from here on should be made with that reuse in mind: nothing in the workflow engine, rubric store, or feedback loop should be remarks-specific if it can reasonably be general.

## 2. High-level flow

```
Teacher uploads CSV/XLSX
        │
        ▼
   Workflow Engine (LangChain chain)
        │
        ├─ Step 1: Deterministic checks
        ├─ Step 2: Rubric retrieval
        ├─ Step 3: LLM evaluation
        └─ Step 4: Teacher review
        │
        ▼
   Feedback database
        │
        ▼ (future)
   Student evidence retrieval
```

## 3. Components

### Rubric Store — *have this*
- **What it stores:** every rule (e.g. `A1`, `B2`, `K1`), its severity, examples, exceptions, and settings.
- **Format:** versioned JSON. See `rubric/quillwarden_rubric.json` (currently v1.1 — see `CHANGELOG.md`).
- **Why it's separate from code:** admins/teachers can change policy (add a rule, loosen a severity, adjust a threshold) without touching the evaluation code. This is already true of the current prototype — `settings` and `categories` in the rubric JSON are the entire policy surface.
- **Convention going forward:** as the rubric evolves, keep versioned files (`rubric-v1.0.json`, `rubric-v1.1.json`, …) in `rubric/`, with a clear "current" pointer, rather than only mutating one file in place. This preserves the ability to re-run old evaluations against the rubric version that was active at the time — important once evaluations are stored and compared over time (see §5).

### Deterministic Validator — *not yet built*
- Fast, rule-based checks that need no LLM call: character count, duplicate remarks, repeated sentences, spacing/punctuation mechanics, canonical name/programme matching (e.g. "Khoj," "Bully to Buddy"), pronoun-format consistency.
- Runs first in the chain because it's cheap, deterministic, and catches a meaningful share of issues (roughly rules `I1`–`K1`, `J1`–`J5`, `H2` in the current rubric) before spending an LLM call.
- Lives in `deterministic-validator/` (scaffolded, empty — see that folder's README).

### LLM Rubric Reviewer — *have this, as a prompt*
- **Input:** one remark + the relevant rubric rules (today: the whole rubric, pasted via `prompts/system_prompt.md`).
- **Output:** structured findings conforming to `review_output_schema` in the rubric JSON.
- **Key design constraint carried over from the prototype:** the rubric is data passed to the model, not logic hard-coded into the prompt or the app. Adding a rule should never require touching the system prompt.
- Today this step is manual (teacher pastes the prompt + rubric + spreadsheet into a chat). Turning it into an actual chain step is Version 1 work.

## 4. Workflow — Version 1

1. Upload CSV/XLSX.
2. Parse rows → student objects (name, class, remark text, any roster columns available).
3. Run deterministic checks (fast, no LLM).
4. Run LLM rubric review (rules requiring judgement — content, tone, safeguarding, equity, consistency).
5. Combine findings from steps 3 and 4 into one `issues[]` list per student, per `review_output_schema`.
6. Teacher edits and approves in a review UI (or, in the interim, in the spreadsheet as today).
7. Export corrected CSV/XLSX.

This is explicitly **a LangChain chain, not an agent** — the sequence of steps is fixed and known in advance. See §8 for when that stops being true.

## 5. Feedback loop — makes the system learn (Version 1, incremental)

Every teacher decision on a flagged issue is a labeled data point:

| AI flagged | Teacher action |
|---|---|
| `A4` (growth point) | Corrected |
| `D1` (excessive praise) | Not applicable |
| `B3` (academic claim) | Corrected |

Store, per decision: `rule_id`, the exact flagged phrase, the teacher's action (corrected / dismissed / accepted-as-is), and the rubric version active at the time.

**Important design choice:** the system learns from accumulated teacher behavior (e.g. "teachers dismiss `D1` on this phrase 80% of the time — maybe it needs an exception"), not by silently rewriting the system prompt or rubric based on a single interaction. Any rubric change driven by this feedback is a deliberate, versioned edit a human makes — see `CHANGELOG.md`'s existing convention for exactly this reason.

## 6. Retrieval layer — Version 2

Before evaluating a new remark, retrieve similar past-reviewed examples (via vector search) and include the top few accepted/rejected examples in the LLM prompt.

```
New remark → vector search → top 3 similar reviewed remarks → included in LLM prompt → evaluation
```

This improves consistency across teachers and terms without fine-tuning a model. It depends on the feedback loop (§5) already having accumulated reviewed examples to search over — so this is meaningfully a Version 2 step, not parallel work to Version 1.

## 7. Student evidence store — Version 3+

Only needed once the goal shifts from *review* ("does this claim look plausible and appropriately worded?") to *verification* ("is this claim actually true?").

Example: for the claim *"has shown growing confidence in presentations,"* the evidence store would hold structured records like:

- Grade: Mathematics 89%
- Attendance: 95%
- Activity: Eco Club
- Observation: "Presented confidently during assembly"

With this in place, rules like `B3` (academic claims) and `B4` (activities/awards) stop always resolving to `requires_record_verification: true` and can instead be checked directly — a meaningful upgrade from the current prototype's necessarily conservative "flag for teacher verification" behavior.

## 8. When does this become an agent?

**Not in Version 1.** The Version 1 chain is a fixed sequence — deterministic checks, then LLM review, then teacher review — decided in advance, not decided by the model at runtime.

An agent appears specifically when the system has to **decide what evidence to gather**, rather than following a fixed retrieval step:

```
Claim detected: "Significant improvement in Mathematics"
        │
        ▼
Agent decides: check grades? attendance? previous remarks?
        │
        ▼
Retrieve the evidence it chose
        │
        ▼
Evaluate the claim
```

That decision — *which* evidence is relevant to *this* claim — is the agentic step. It only makes sense once the Student Evidence Store (§7) exists to give the agent something to choose between. Don't reach for agent frameworks before that; a fixed chain over a rubric is sufficient through Version 2.

## 9. Long-term knowledge bases

Three distinct knowledge bases, not one undifferentiated store:

| KB | Contains | Answers |
|---|---|---|
| Rubric KB | Policies, style guide, severity rules | *How should we judge?* |
| Student Evidence KB | Grades, attendance, observations, activities | *What is true?* |
| Review Decisions KB | Accepted/rejected AI findings, teacher corrections | *How do teachers interpret the rubric?* |

Keeping these separate matters: the rubric changes by deliberate policy edit, the evidence store changes by record-of-fact updates (grades, attendance), and the review-decisions KB grows automatically from usage. Conflating them would make it unclear, later, whether a given piece of stored data is a rule, a fact, or a precedent.

## 10. Future extension: answer verification

Same engine, different input — this is the reason the architecture is described generically in §1 rather than as "a remarks tool."

| Today | Tomorrow |
|---|---|
| Input: a report remark | Input: a student's answer to a graded question |
| Rubric: report-writing policy (`A`–`L` rules) | Rubric: a marking scheme / criteria for that question |
| Output: flagged issues, severities | Output: pass / partial / missing, per criterion |

For a 3-mark question, the engine would return a per-criterion verdict rather than a single score — the same "rubric is data, evaluation is generic" principle applies directly.

## 11. Recommended stack (proposed, not yet decided)

| Layer | Technology |
|---|---|
| Frontend | React / Next.js |
| Workflow | LangChain chains |
| LLM | GPT-4.1 / GPT-4o (or Claude, given this project already lives in Claude Code) |
| Database | Postgres |
| Vector store | pgvector |
| Rubrics | JSON + versioning (existing convention) |
| Background jobs | Celery / BullMQ / Temporal |

Treat this table as a starting proposal to validate in Claude Code, not a locked-in decision — in particular, the LLM choice and background-job framework should be picked based on what's actually being deployed on (e.g. if this ends up as a Claude-based tool throughout, LangChain's LLM abstraction may be unnecessary overhead).

## 12. The one-sentence architecture

> A rubric-driven evaluation platform where deterministic checks, LLM reasoning, student evidence, and teacher feedback are combined through a workflow to produce explainable, verifiable educational judgements.

**Key design choice:** start with a workflow (Version 1), add retrieval next (Version 2), and add agents only when the system must decide what evidence to gather (Version 3+). Do not build retrieval or agent behavior ahead of the phase that needs it — each depends on data (reviewed examples, evidence records) that only exists once the prior phase has been running.
