# Student Evidence Store (Version 3+ — not yet built)

Structured records of what's actually true about a student — grades, attendance, activities, observations — so the system can move from *review* ("does this claim look plausible?") to *verification* ("is this claim true?").

## Example record shape (illustrative, not final)
```json
{
  "student": "Rahul",
  "grades": { "Mathematics": 89 },
  "attendance_percent": 95,
  "activities": ["Eco Club"],
  "observations": ["Presented confidently during assembly"]
}
```

## What this unlocks
Rubric rules `B3` (academic claims) and `B4` (activities/awards/roles) currently always resolve to `requires_record_verification: true` because the reviewing LLM has no access to real records. Once this store exists, those rules can be checked directly against evidence instead of only flagged for manual teacher lookup.

## Do not build ahead of need
This is explicitly gated behind Version 1 (workflow) and Version 2 (retrieval) being in place and in use — see `../ARCHITECTURE.md` §7–8. Building this early means maintaining a data source nothing consumes yet.
