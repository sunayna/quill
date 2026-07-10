"""K1 — Character limit check."""


def _issue(rule_id, severity, exact_phrase, explanation, teacher_action):
    return {
        "rule_id": rule_id,
        "severity": severity,
        "exact_phrase": exact_phrase,
        "explanation": explanation,
        "teacher_action": teacher_action,
        "requires_record_verification": False,
    }


def check_k1(remark: str, max_chars: int) -> list:
    count = len(remark)
    if count > max_chars:
        return [_issue(
            "K1", "required",
            f"[{count} characters]",
            f"Remark is {count} characters; limit is {max_chars} (including spaces and line breaks).",
            "Shorten the remark to fit within the character limit.",
        )]
    return []
