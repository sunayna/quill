"""
B-section accuracy checks: student identity (B1) and pronoun consistency (B2).
"""

import re

_NON_NAMES = {
    'Grade', 'Term', 'Sports', 'Day', 'English', 'Hindi', 'Mathematics', 'Science',
    'Khoj', 'Eco', 'Club', 'Student', 'Council', 'His', 'Her', 'Hers', 'Him', 'She',
    'They', 'Their', 'Them', 'Reading', 'Writing', 'Spelling', 'Speaking', 'Listening',
    'Drawing', 'Painting', 'Swimming', 'Running', 'Dancing', 'Singing', 'Assembly',
    'Festival', 'Module', 'Expedition', 'Annual', 'Digital', 'Literacy', 'Physical',
    'Education', 'Visual', 'Arts', 'Performing', 'Sanskrit', 'Social', 'Cricket',
    'Football', 'Basketball', 'Volleyball', 'Karate', 'Yoga', 'Track', 'Field',
    'Table', 'Tennis', 'Bully', 'Buddy', 'LEAD', 'Collective', 'Cyber', 'Safety',
    'Ambassador', 'Transition', 'Event', 'Management', 'With', 'Moving', 'During',
    'Regular', 'Continued', 'Reviewing', 'Although', 'However', 'Additionally',
    'While', 'Having', 'Being', 'Showing', 'Throughout', 'Another', 'Further',
    'Greater', 'Stronger', 'Higher', 'Lower', 'Wider', 'Deeper', 'Programme',
    'Activity', 'Project', 'Round', 'Final', 'Class', 'Week', 'Month', 'Year',
    'Special', 'Cultural', 'Community', 'Leadership', 'January', 'February',
    'March', 'April', 'June', 'July', 'August', 'September', 'October', 'November',
    'December', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday',
}

_SENTENCE_SPLIT = re.compile(r'(?<=[.!?])\s+')
_CAP_WORD = re.compile(r'^([A-Z][a-z]{2,})\b')


def _name_parts(name: str) -> list:
    return [p for p in name.strip().split() if len(p) > 1]


def _issue(rule_id, severity, phrase, explanation, action):
    return {
        "rule_id": rule_id,
        "severity": severity,
        "exact_phrase": phrase,
        "explanation": explanation,
        "teacher_action": action,
        "requires_record_verification": False,
    }


def check_b1_identity(remark: str, student_name: str) -> list:
    """
    B1: student's name must appear (any name part), and no other
    mid-sentence capitalized proper nouns besides the student's own name.
    """
    issues = []

    if student_name:
        parts = _name_parts(student_name)
        name_found = any(
            re.search(r'\b' + re.escape(p) + r'\b', remark, re.IGNORECASE)
            for p in parts
        )
        if not name_found:
            issues.append(_issue(
                'B1', 'critical', 'Student name not found',
                'The remark may not refer clearly to the selected student.',
                'Verify the student identity.',
            ))

    exclusions = set(_NON_NAMES)
    if student_name:
        for p in _name_parts(student_name):
            exclusions.add(p)

    suspicious = []
    for sentence in _SENTENCE_SPLIT.split(remark):
        words = sentence.strip().split()
        for word in words[1:]:
            m = _CAP_WORD.match(word)
            if m and m.group(1) not in exclusions:
                suspicious.append(m.group(1))

    other_names = list(dict.fromkeys(suspicious))
    if other_names:
        issues.append(_issue(
            'B1', 'critical', ', '.join(other_names[:3]),
            'Another student name may be present.',
            'Verify all names in the remark.',
        ))

    return issues


def check_b2_pronouns(remark: str, pronouns: str) -> list:
    """B2: pronouns must be internally consistent and match roster."""
    issues = []
    if not pronouns:
        return issues
    p = pronouns.lower()
    if re.search(r'\bhe\b', p):
        m = re.search(r'\b(she|her|hers)\b', remark, re.IGNORECASE)
        if m:
            issues.append(_issue(
                'B2', 'critical', m.group(0),
                'Pronoun may conflict with roster information.',
                'Verify and correct the pronoun.',
            ))
    if re.search(r'\bshe\b', p):
        m = re.search(r'\b(he|him|his)\b', remark, re.IGNORECASE)
        if m:
            issues.append(_issue(
                'B2', 'critical', m.group(0),
                'Pronoun may conflict with roster information.',
                'Verify and correct the pronoun.',
            ))
    return issues
