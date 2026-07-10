import pytest
from rules.j_naming import check_j1, check_j2, check_j3, check_j4, check_j5

# ── Fixtures: rubric-shaped canonical form lists ───────────────────────────────

J1_FORMS = [
    {"display": '"Bully to Buddy"', "requires_quotation_marks": True},
    {"display": "LEAD Collective", "requires_quotation_marks": False},
    {"display": "Eco Club", "requires_quotation_marks": False},
    {"display": "Student Council", "requires_quotation_marks": False},
]

J3_SUBJECTS = [
    "Mathematics", "English", "Hindi", "Sanskrit", "Science",
    "Social Science", "Digital Literacy", "Physical Education",
    "Visual Arts", "Performing Arts",
]

J4_SPORTS = [
    "Cricket", "Football", "Basketball", "Swimming",
    "Table Tennis", "Track and Field", "Volleyball", "Karate", "Yoga",
]

J5_EVENTS = [
    "Sports Day", "Class Assembly", "Cyber Safety Ambassador",
    "Transition In-Charge", "Event Management Team",
]


# ── J1 ─────────────────────────────────────────────────────────────────────────

class TestJ1:
    def test_bully_to_buddy_with_quotes_passes(self):
        remark = 'She participated in "Bully to Buddy" this year.'
        assert check_j1(remark, J1_FORMS) == []

    def test_bully_to_buddy_without_quotes_flagged(self):
        remark = "She participated in Bully to Buddy this year."
        issues = check_j1(remark, J1_FORMS)
        assert len(issues) == 1
        assert issues[0]["rule_id"] == "J1"
        assert issues[0]["severity"] == "required"
        assert "quotation" in issues[0]["explanation"].lower()

    def test_lead_collective_correct_passes(self):
        remark = "She is a member of LEAD Collective."
        assert check_j1(remark, J1_FORMS) == []

    def test_lead_collective_wrong_case_flagged(self):
        remark = "She is a member of lead collective."
        issues = check_j1(remark, J1_FORMS)
        assert len(issues) == 1
        assert issues[0]["rule_id"] == "J1"
        assert "LEAD Collective" in issues[0]["explanation"]

    def test_lead_collective_partial_wrong_case(self):
        remark = "She is a member of LEAD collective."
        issues = check_j1(remark, J1_FORMS)
        assert len(issues) == 1

    def test_eco_club_correct_passes(self):
        remark = "He contributes actively to Eco Club."
        assert check_j1(remark, J1_FORMS) == []

    def test_eco_club_lowercase_flagged(self):
        remark = "He contributes actively to eco club."
        issues = check_j1(remark, J1_FORMS)
        assert any(i["rule_id"] == "J1" for i in issues)

    def test_student_council_correct_passes(self):
        remark = "She served on Student Council this year."
        assert check_j1(remark, J1_FORMS) == []

    def test_programme_not_in_remark_no_issue(self):
        remark = "He has shown great improvement in his written work."
        assert check_j1(remark, J1_FORMS) == []

    def test_empty_canonical_forms_no_issue(self):
        remark = "She participated in Bully to Buddy."
        assert check_j1(remark, []) == []


# ── J2 ─────────────────────────────────────────────────────────────────────────

class TestJ2:
    def test_correct_khoj_passes(self):
        remark = "She represented her class at Khoj this year."
        assert check_j2(remark) == []

    def test_all_caps_khoj_flagged(self):
        remark = "She represented her class at KHOJ this year."
        issues = check_j2(remark)
        assert len(issues) == 1
        assert issues[0]["rule_id"] == "J2"
        assert issues[0]["severity"] == "required"
        assert "KHOJ" in issues[0]["exact_phrase"]

    def test_all_lowercase_khoj_flagged(self):
        remark = "She attended the khoj fair."
        issues = check_j2(remark)
        assert len(issues) == 1
        assert "khoj" in issues[0]["exact_phrase"]

    def test_khoj_not_in_remark_no_issue(self):
        remark = "She has shown great improvement in Mathematics."
        assert check_j2(remark) == []

    def test_multiple_wrong_occurrences_each_flagged(self):
        remark = "She attended KHOJ last year and khoj this year."
        issues = check_j2(remark)
        assert len(issues) == 2

    def test_custom_canonical_form(self):
        remark = "He attended KHOJ."
        issues = check_j2(remark, canonical_form="Khoj")
        assert len(issues) == 1


# ── J3 ─────────────────────────────────────────────────────────────────────────

class TestJ3:
    def test_capitalised_subject_passes(self):
        remark = "She excels in Mathematics and English."
        assert check_j3(remark, J3_SUBJECTS) == []

    def test_lowercase_mathematics_flagged(self):
        remark = "She excels in mathematics this term."
        issues = check_j3(remark, J3_SUBJECTS)
        assert any(i["rule_id"] == "J3" and "mathematics" in i["exact_phrase"].lower() for i in issues)

    def test_lowercase_english_flagged(self):
        remark = "Her english skills have improved."
        issues = check_j3(remark, J3_SUBJECTS)
        assert any(i["rule_id"] == "J3" for i in issues)

    def test_severity_is_warning(self):
        remark = "She works hard in science."
        issues = check_j3(remark, J3_SUBJECTS)
        assert all(i["severity"] == "warning" for i in issues)

    def test_subject_not_present_no_issue(self):
        remark = "She has shown excellent progress in her work."
        assert check_j3(remark, J3_SUBJECTS) == []

    def test_empty_reference_forms_no_issue(self):
        remark = "She excels in mathematics."
        assert check_j3(remark, []) == []


# ── J4 ─────────────────────────────────────────────────────────────────────────

class TestJ4:
    def test_capitalised_sport_passes(self):
        remark = "He plays Cricket and Football with great enthusiasm."
        assert check_j4(remark, J4_SPORTS) == []

    def test_lowercase_cricket_flagged(self):
        remark = "He plays cricket every afternoon."
        issues = check_j4(remark, J4_SPORTS)
        assert any(i["rule_id"] == "J4" and "cricket" in i["exact_phrase"].lower() for i in issues)

    def test_lowercase_football_flagged(self):
        remark = "She enjoys football and swimming."
        issues = check_j4(remark, J4_SPORTS)
        assert len(issues) >= 1
        assert all(i["severity"] == "warning" for i in issues)

    def test_sport_not_present_no_issue(self):
        remark = "She has shown improvement in her academic work."
        assert check_j4(remark, J4_SPORTS) == []

    def test_empty_reference_forms_no_issue(self):
        remark = "He plays cricket."
        assert check_j4(remark, []) == []


# ── J5 ─────────────────────────────────────────────────────────────────────────

class TestJ5:
    def test_correct_sports_day_passes(self):
        remark = "She represented her class on Sports Day."
        assert check_j5(remark, J5_EVENTS) == []

    def test_lowercase_sports_day_flagged(self):
        remark = "She performed well at sports day."
        issues = check_j5(remark, J5_EVENTS)
        assert any(i["rule_id"] == "J5" for i in issues)
        assert any("Sports Day" in i["explanation"] for i in issues)

    def test_correct_class_assembly_passes(self):
        remark = "He delivered a confident speech at Class Assembly."
        assert check_j5(remark, J5_EVENTS) == []

    def test_lowercase_class_assembly_flagged(self):
        remark = "He spoke at the class assembly this term."
        issues = check_j5(remark, J5_EVENTS)
        assert any(i["rule_id"] == "J5" for i in issues)

    def test_cyber_safety_ambassador_correct_passes(self):
        remark = "She served as Cyber Safety Ambassador this year."
        assert check_j5(remark, J5_EVENTS) == []

    def test_cyber_safety_ambassador_lowercase_flagged(self):
        remark = "She was the cyber safety ambassador."
        issues = check_j5(remark, J5_EVENTS)
        assert any(i["rule_id"] == "J5" for i in issues)

    def test_severity_is_warning(self):
        remark = "He helped organise the event management team."
        issues = check_j5(remark, J5_EVENTS)
        assert all(i["severity"] == "warning" for i in issues)

    def test_event_not_in_remark_no_issue(self):
        remark = "She has worked hard throughout the year."
        assert check_j5(remark, J5_EVENTS) == []
