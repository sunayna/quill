# Retrieval Layer (Version 2 — not yet built)

Vector search over past-reviewed remarks, used to pull similar accepted/rejected examples into the LLM prompt for a new remark. Improves consistency across teachers and terms without fine-tuning.

## Depends on
The Feedback Database (`../feedback-db/`) having accumulated a meaningful number of reviewed remarks with teacher decisions attached. **Do not start this before that data exists** — there's nothing to retrieve yet.

## Flow (planned)
```
New remark → embed → vector search over reviewed-remarks store
           → top 3 similar examples (with teacher's accepted/rejected verdict)
           → included in LLM evaluation prompt
```

## Open questions to resolve when building
- What to embed: the remark text alone, or remark + flagged issue context?
- Store choice: pgvector (fits the recommended Postgres stack in `../ARCHITECTURE.md`) vs. a dedicated vector DB.
- How many examples to retrieve, and how to keep them from dominating the prompt over the rubric itself.
