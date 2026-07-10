"""
H2 — Duplicate content:
  - Within a remark: repeated sentences
  - Across remarks: identical remark text assigned to different students
"""
import re


def _issue(rule_id, severity, exact_phrase, explanation, teacher_action):
    return {
        "rule_id": rule_id,
        "severity": severity,
        "exact_phrase": exact_phrase,
        "explanation": explanation,
        "teacher_action": teacher_action,
        "requires_record_verification": False,
    }


def _normalize(text: str) -> str:
    """Collapse whitespace and lowercase for comparison."""
    return re.sub(r'\s+', ' ', text.strip().lower())


def _split_sentences(remark: str) -> list:
    parts = re.split(r'(?<=[.!?])\s+', remark.strip())
    return [s.strip() for s in parts if len(s.strip()) > 10]


def check_h2_within(remark: str) -> list:
    """Flag repeated sentences within a single remark."""
    sentences = _split_sentences(remark)
    seen: dict[str, str] = {}
    issues = []
    for sentence in sentences:
        key = _normalize(sentence)
        if key in seen:
            preview = sentence[:80] + ("…" if len(sentence) > 80 else "")
            issues.append(_issue(
                "H2", "required", preview,
                "This sentence (or a near-identical one) appears more than once in the remark.",
                "Remove the duplicated content.",
            ))
        else:
            seen[key] = sentence
    return issues


def check_h2_across(remarks: list, student_names: list) -> list:
    """
    Detect identical remarks assigned to different students.

    Args:
        remarks: remark text for each student, in row order.
        student_names: corresponding student name for each remark.

    Returns:
        A list (one entry per student) of issue lists. Most will be empty.
    """
    result: list[list] = [[] for _ in remarks]
    normalized = [_normalize(r) for r in remarks]

    for i in range(len(remarks)):
        if not normalized[i]:
            continue
        for j in range(i + 1, len(remarks)):
            if normalized[i] == normalized[j]:
                other_i = student_names[j] or f"student {j + 1}"
                other_j = student_names[i] or f"student {i + 1}"
                preview = remarks[i][:60] + ("…" if len(remarks[i]) > 60 else "")
                result[i].append(_issue(
                    "H2", "required", preview,
                    f"Remark is identical to the one submitted for {other_i}.",
                    "Verify this remark was not copied from another student's record.",
                ))
                result[j].append(_issue(
                    "H2", "required", preview,
                    f"Remark is identical to the one submitted for {other_j}.",
                    "Verify this remark was not copied from another student's record.",
                ))

    return result
