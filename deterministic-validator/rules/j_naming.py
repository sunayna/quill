"""
J1 — Programme names (canonical forms, required punctuation)
J2 — Khoj (exact capitalisation)
J3 — Subjects (capitalisation)
J4 — Sports and activities (capitalisation)
J5 — Events and roles (canonical capitalisation)
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


# ── J1: Programme names ────────────────────────────────────────────────────────

def check_j1(remark: str, canonical_forms: list) -> list:
    """
    canonical_forms: list of dicts with keys ``display`` and ``requires_quotation_marks``.
    Extracted from rubric categories[J1].detection.canonical_forms.
    """
    issues = []

    for form in canonical_forms:
        raw_display = form.get("display", "")
        needs_quotes = form.get("requires_quotation_marks", False)

        # Strip surrounding escaped quotes from display to get the bare name
        name = raw_display.strip('"')

        if not re.search(re.escape(name), remark, re.IGNORECASE):
            continue  # this programme name doesn't appear in the remark at all

        if needs_quotes:
            # Must appear as "Name" (with double-quote characters surrounding it)
            if f'"{name}"' not in remark:
                m = re.search(re.escape(name), remark, re.IGNORECASE)
                issues.append(_issue(
                    "J1", "required", m.group(),
                    f'"{name}" must be enclosed in double quotation marks.',
                    f'Write as \\"{name}\\".',
                ))
        else:
            # Must appear in exact capitalisation
            if name not in remark:
                m = re.search(re.escape(name), remark, re.IGNORECASE)
                issues.append(_issue(
                    "J1", "required", m.group(),
                    f'Incorrect form of programme name; canonical form is "{name}".',
                    f'Write as "{name}".',
                ))

    return issues


# ── J2: Khoj ──────────────────────────────────────────────────────────────────

def check_j2(remark: str, canonical_form: str = "Khoj", avoid_forms: list | None = None) -> list:
    """Flag any occurrence of 'khoj' that isn't the exact canonical form."""
    issues = []
    for m in re.finditer(r'\bkhoj\b', remark, re.IGNORECASE):
        if m.group() != canonical_form:
            issues.append(_issue(
                "J2", "required", m.group(),
                f'"{m.group()}" should be "{canonical_form}".',
                f'Correct to the canonical form "{canonical_form}".',
            ))
    return issues


# ── J3: Subjects ───────────────────────────────────────────────────────────────

def check_j3(remark: str, reference_forms: list) -> list:
    """
    Flag subject names that appear in lowercase when the canonical form is capitalised.
    Severity is 'warning' because capitalisation style is configurable per school.
    """
    issues = []
    for subject in reference_forms:
        pattern = r'\b' + re.escape(subject) + r'\b'
        # Only flag if the lowercase version appears but the correct form does not
        lower_subject = subject.lower()
        lower_pattern = r'\b' + re.escape(lower_subject) + r'\b'
        if re.search(lower_pattern, remark) and not re.search(pattern, remark):
            m = re.search(lower_pattern, remark)
            issues.append(_issue(
                "J3", "warning", m.group(),
                f'Subject name "{m.group()}" may need to be capitalised as "{subject}".',
                "Apply the school's configured subject-capitalisation style consistently.",
            ))
    return issues


# ── J4: Sports and activities ─────────────────────────────────────────────────

def check_j4(remark: str, reference_forms: list) -> list:
    """
    Flag sport names that appear in lowercase when the canonical form is capitalised.
    Severity is 'warning' because capitalisation style is configurable per school.
    """
    issues = []
    for sport in reference_forms:
        lower_sport = sport.lower()
        lower_pattern = r'\b' + re.escape(lower_sport) + r'\b'
        if re.search(lower_pattern, remark) and sport not in remark:
            m = re.search(lower_pattern, remark)
            issues.append(_issue(
                "J4", "warning", m.group(),
                f'Sport name "{m.group()}" may need to be capitalised as "{sport}".',
                "Apply the school's configured sport-capitalisation style consistently.",
            ))
    return issues


# ── J5: Events and roles ──────────────────────────────────────────────────────

def check_j5(remark: str, canonical_forms: list) -> list:
    """
    Flag event/role names that appear in the remark but not in their canonical capitalisation.
    """
    issues = []
    for form in canonical_forms:
        lower_form = form.lower()
        lower_pattern = r'\b' + re.escape(lower_form) + r'\b'
        if re.search(lower_pattern, remark, re.IGNORECASE) and form not in remark:
            m = re.search(lower_pattern, remark, re.IGNORECASE)
            issues.append(_issue(
                "J5", "warning", m.group(),
                f'"{m.group()}" should use the canonical capitalisation "{form}".',
                f'Write as "{form}".',
            ))
    return issues
