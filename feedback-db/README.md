# Feedback Database (Version 1, incremental — not yet built)

Captures every teacher decision on a flagged issue as a labeled data point. This is what the system "learns" from — not by rewriting the rubric or prompt automatically, but by giving a human maintainer the data to make a deliberate, versioned rubric edit later.

## Record shape (illustrative)
```json
{
  "rule_id": "A4",
  "exact_phrase": "she needs to work harder",
  "teacher_action": "corrected | dismissed | accepted_as_is",
  "rubric_version": "1.1",
  "student_id": "opaque id, not the student's name",
  "timestamp": "..."
}
```

## Why this exists before the Retrieval Layer
`../retrieval/` (Version 2) depends on this store having accumulated real reviewed examples. This store should be populated from Version 1 onward, even before retrieval is built, so there's a useful dataset by the time Version 2 starts.

## Privacy note
Store enough to analyze rubric performance (which rules get dismissed often, which get corrected often) without needing to retain the student's name or other identifying detail longer than necessary — this data will accumulate over terms and years.
