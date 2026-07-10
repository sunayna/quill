"""
I1 — Grammar (pattern-based: repeated words, missing capital, run-on sentences)
I2 — Spelling (static misspelling lookup)
I3 — Punctuation and spacing (mechanical checks)
"""
import re

# ── Shared ─────────────────────────────────────────────────────────────────────

def _issue(rule_id, severity, exact_phrase, explanation, teacher_action):
    return {
        "rule_id": rule_id,
        "severity": severity,
        "exact_phrase": exact_phrase,
        "explanation": explanation,
        "teacher_action": teacher_action,
        "requires_record_verification": False,
    }


# ── I1: Grammar ────────────────────────────────────────────────────────────────

_RUN_ON_WORD_THRESHOLD = 60  # words in a single sentence


def check_i1(remark: str) -> list:
    issues = []

    # Repeated adjacent words (e.g. "the the", "is is")
    m = re.search(r'\b(\w+)[ \t]+\1\b', remark, re.IGNORECASE)
    if m:
        issues.append(_issue(
            "I1", "required", m.group(),
            f'Repeated word: "{m.group()}".',
            "Remove the duplicate word.",
        ))

    # Missing capital letter at the start of the remark
    stripped = remark.lstrip()
    if stripped and not stripped[0].isupper():
        preview = stripped[:30].replace("\n", " ")
        issues.append(_issue(
            "I1", "required", preview,
            "Remark does not begin with a capital letter.",
            "Capitalise the first word of the remark.",
        ))

    # Run-on sentences — any sentence exceeding the word threshold
    sentences = re.split(r'(?<=[.!?])\s+', remark.strip())
    for sentence in sentences:
        word_count = len(sentence.split())
        if word_count > _RUN_ON_WORD_THRESHOLD:
            preview = " ".join(sentence.split()[:8]) + "…"
            issues.append(_issue(
                "I1", "required", preview,
                f"Sentence is {word_count} words long; consider splitting it.",
                "Break this into two shorter sentences for clarity.",
            ))

    return issues


# ── I2: Spelling ───────────────────────────────────────────────────────────────

# wrong → correct  (British English context; purely wrong spellings, not variant spellings)
_MISSPELLINGS: dict[str, str] = {
    "acheive": "achieve",
    "acheived": "achieved",
    "acheiving": "achieving",
    "acheivements": "achievements",
    "achievments": "achievements",
    "accomodate": "accommodate",
    "accomadation": "accommodation",
    "recieve": "receive",
    "recieved": "received",
    "beleive": "believe",
    "beleived": "believed",
    "occured": "occurred",
    "occurance": "occurrence",
    "seperate": "separate",
    "proffesional": "professional",
    "managment": "management",
    "enviroment": "environment",
    "noticable": "noticeable",
    "sportmanship": "sportsmanship",
    "independant": "independent",
    "freindship": "friendship",
    "oppertunity": "opportunity",
    "oppurtunity": "opportunity",
    "organsational": "organisational",
    "particpated": "participated",
    "particpate": "participate",
    "demeanur": "demeanour",
    "constructave": "constructive",
    "consistant": "consistent",
    "persistant": "persistent",
    "enthuisiasm": "enthusiasm",
    "enthusiasim": "enthusiasm",
    "responsibilty": "responsibility",
    "partcipation": "participation",
    "comunicates": "communicates",
    "comunication": "communication",
    "assesment": "assessment",
    "assesments": "assessments",
    "neccessary": "necessary",
    "occassion": "occasion",
    "recomend": "recommend",
    "recomended": "recommended",
}


def check_i2(remark: str, extra_misspellings: dict | None = None) -> list:
    """
    Check for known misspellings.
    Pass ``extra_misspellings`` (wrong→correct) to augment the built-in table.
    """
    lookup = dict(_MISSPELLINGS)
    if extra_misspellings:
        lookup.update(extra_misspellings)

    issues = []
    lower = remark.lower()
    for wrong, correct in lookup.items():
        pattern = r'\b' + re.escape(wrong) + r'\b'
        m = re.search(pattern, lower)
        if m:
            exact = remark[m.start(): m.end()]
            issues.append(_issue(
                "I2", "required", exact,
                f'"{exact}" appears to be misspelled; correct form is "{correct}".',
                f'Replace "{exact}" with "{correct}".',
            ))
    return issues


# ── I3: Punctuation and spacing ────────────────────────────────────────────────

def check_i3(remark: str) -> list:
    issues = []

    # Double (or more) spaces
    if re.search(r'[ \t]{2,}', remark):
        issues.append(_issue(
            "I3", "required", "[double space]",
            "Remark contains consecutive spaces.",
            "Remove the extra spaces.",
        ))

    # Missing space after sentence-ending punctuation before an uppercase letter.
    # Skip likely abbreviations: if the word immediately before the punctuation
    # is 1–3 characters (e.g. Mr, Dr, St, vs), treat it as an abbreviation.
    for m in re.finditer(r'[.!?](?=[A-Z])', remark):
        pos = m.start()
        word_before = re.search(r'([A-Za-z]+)$', remark[:pos])
        if word_before and len(word_before.group(1)) <= 3:
            continue  # likely abbreviation
        issues.append(_issue(
            "I3", "required", remark[max(0, pos - 2): pos + 3],
            f'Missing space after punctuation at "{m.group()}".',
            "Add a space after the punctuation mark.",
        ))
        break  # report once per remark; teacher will scan the rest

    # Missing terminal punctuation
    stripped = remark.rstrip()
    if stripped and stripped[-1] not in '.!?':
        tail = stripped[-20:] if len(stripped) > 20 else stripped
        issues.append(_issue(
            "I3", "required", tail,
            "Remark does not end with terminal punctuation.",
            "Add a full stop at the end of the remark.",
        ))

    # Multiple consecutive exclamation or question marks
    m = re.search(r'[!?]{2,}', remark)
    if m:
        issues.append(_issue(
            "I3", "required", m.group(),
            f'Multiple punctuation marks: "{m.group()}".',
            "Use a single punctuation mark.",
        ))

    # Space immediately before punctuation (e.g. "word .")
    m = re.search(r'[ \t][,;:.!?]', remark)
    if m:
        issues.append(_issue(
            "I3", "required", m.group(),
            f'Space before punctuation: "{m.group().strip()}".',
            "Remove the space before the punctuation mark.",
        ))

    return issues
