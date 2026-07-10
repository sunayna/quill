"""
Tests for the pure functions in reviewer.py: build_prompt and parse_response.
No API calls — run_review is integration-level and is not tested here.
"""
import json
import pytest
from reviewer import build_prompt, parse_response, DET_RULE_IDS


# ── Fixtures ───────────────────────────────────────────────────────────────────

MINIMAL_RUBRIC = {
    "settings": {"max_characters_including_spaces_and_line_breaks": 1000},
    "review_output_schema": {},
    "categories": [],
}

ONE_ROW = [{"student_name": "Aarav Sharma", "remark": "He works hard and participates well."}]

TWO_ROWS = [
    {"student_name": "Aarav Sharma", "remark": "He works hard and participates well."},
    {"student_name": "Priya Mehta", "remark": "She is a thoughtful and engaged learner."},
]


# ── build_prompt ───────────────────────────────────────────────────────────────

class TestBuildPrompt:
    def test_contains_student_name(self):
        prompt = build_prompt(ONE_ROW, MINIMAL_RUBRIC)
        assert "Aarav Sharma" in prompt

    def test_contains_remark_text(self):
        prompt = build_prompt(ONE_ROW, MINIMAL_RUBRIC)
        assert "He works hard and participates well." in prompt

    def test_contains_rubric_json(self):
        prompt = build_prompt(ONE_ROW, MINIMAL_RUBRIC)
        assert "max_characters_including_spaces_and_line_breaks" in prompt

    def test_numbers_rows_sequentially(self):
        prompt = build_prompt(TWO_ROWS, MINIMAL_RUBRIC)
        assert "1. Student: Aarav Sharma" in prompt
        assert "2. Student: Priya Mehta" in prompt

    def test_skip_note_present_by_default(self):
        prompt = build_prompt(ONE_ROW, MINIMAL_RUBRIC)
        assert "deterministic" in prompt.lower()
        for rule_id in DET_RULE_IDS:
            assert rule_id in prompt

    def test_skip_note_lists_all_det_rule_ids(self):
        prompt = build_prompt(ONE_ROW, MINIMAL_RUBRIC, skip_rule_ids={"I1", "K1"})
        assert "I1" in prompt
        assert "K1" in prompt

    def test_no_skip_note_when_empty_set(self):
        prompt = build_prompt(ONE_ROW, MINIMAL_RUBRIC, skip_rule_ids=set())
        assert "deterministic" not in prompt.lower()

    def test_pronoun_included_when_present(self):
        rows = [{"student_name": "Aarav", "remark": "He works hard.", "pronoun": "he/him"}]
        prompt = build_prompt(rows, MINIMAL_RUBRIC)
        assert "he/him" in prompt
        assert "verified from roster" in prompt

    def test_pronoun_line_absent_when_empty(self):
        rows = [{"student_name": "Priya", "remark": "She is a great student.", "pronoun": ""}]
        prompt = build_prompt(rows, MINIMAL_RUBRIC)
        assert "verified from roster" not in prompt

    def test_pronoun_line_absent_when_key_missing(self):
        rows = [{"student_name": "Priya", "remark": "She is a great student."}]
        prompt = build_prompt(rows, MINIMAL_RUBRIC)
        assert "verified from roster" not in prompt

    def test_fallback_name_when_no_name_key(self):
        rows = [{"remark": "Works well."}]
        prompt = build_prompt(rows, MINIMAL_RUBRIC)
        assert "Row 1" in prompt

    def test_remarks_key_alias_accepted(self):
        rows = [{"student_name": "Aarav", "remarks": "He works hard."}]
        prompt = build_prompt(rows, MINIMAL_RUBRIC)
        assert "He works hard." in prompt

    def test_name_key_alias_accepted(self):
        rows = [{"name": "Priya", "remark": "She participates well."}]
        prompt = build_prompt(rows, MINIMAL_RUBRIC)
        assert "Priya" in prompt

    def test_multiple_rows_all_present(self):
        prompt = build_prompt(TWO_ROWS, MINIMAL_RUBRIC)
        assert "Aarav Sharma" in prompt
        assert "Priya Mehta" in prompt

    def test_returns_string(self):
        assert isinstance(build_prompt(ONE_ROW, MINIMAL_RUBRIC), str)

    def test_rubric_embedded_as_json(self):
        rubric = {"settings": {"require_closing": True}, "categories": []}
        prompt = build_prompt(ONE_ROW, rubric)
        assert '"require_closing": true' in prompt

    def test_schema_instruction_present(self):
        prompt = build_prompt(ONE_ROW, MINIMAL_RUBRIC)
        assert "review_output_schema" in prompt


# ── parse_response ─────────────────────────────────────────────────────────────

class TestParseResponse:
    def test_valid_json_array(self):
        data = [{"student_name": "Aarav", "status": "no_major_issues", "issues": []}]
        assert parse_response(json.dumps(data)) == data

    def test_json_embedded_in_prose(self):
        data = [{"student_name": "Aarav", "status": "no_major_issues", "issues": []}]
        text = f"Here is the result:\n{json.dumps(data)}\nEnd of response."
        assert parse_response(text) == data

    def test_empty_string_returns_empty_list(self):
        assert parse_response("") == []

    def test_whitespace_only_returns_empty_list(self):
        assert parse_response("   \n  ") == []

    def test_invalid_json_returns_empty_list(self):
        assert parse_response("This is not JSON.") == []

    def test_json_object_not_array_returns_empty_list(self):
        # Top-level object instead of array — not the expected schema
        assert parse_response('{"student_name": "Aarav"}') == []

    def test_preserves_all_schema_fields(self):
        data = [{
            "student_name": "Aarav",
            "status": "needs_revision",
            "issues": [{
                "rule_id": "A1",
                "severity": "required",
                "exact_phrase": "wonderful student",
                "explanation": "Generic praise with no observable strength.",
                "teacher_action": "Add a specific, observable strength.",
                "requires_record_verification": False,
            }],
            "character_count": 220,
            "teacher_revision_required": True,
        }]
        result = parse_response(json.dumps(data))
        assert result[0]["student_name"] == "Aarav"
        assert result[0]["issues"][0]["rule_id"] == "A1"
        assert result[0]["issues"][0]["requires_record_verification"] is False

    def test_multiple_students(self):
        data = [
            {"student_name": "Aarav", "status": "no_major_issues", "issues": []},
            {"student_name": "Priya", "status": "minor_edits", "issues": []},
        ]
        result = parse_response(json.dumps(data))
        assert len(result) == 2
        assert result[1]["student_name"] == "Priya"

    def test_empty_json_array(self):
        assert parse_response("[]") == []

    def test_json_with_trailing_text(self):
        data = [{"student_name": "Aarav", "status": "no_major_issues", "issues": []}]
        text = json.dumps(data) + "\n\nSummary: 1 remark reviewed."
        result = parse_response(text)
        assert result == data

    def test_json_with_leading_text(self):
        data = [{"student_name": "Aarav", "status": "no_major_issues", "issues": []}]
        text = "Review complete.\n\n" + json.dumps(data)
        result = parse_response(text)
        assert result == data

    def test_requires_record_verification_flag_preserved(self):
        data = [{
            "student_name": "Priya",
            "status": "needs_revision",
            "issues": [{
                "rule_id": "B3",
                "severity": "required",
                "exact_phrase": "grades have improved",
                "explanation": "Academic claim requires verification.",
                "teacher_action": "Verify against academic records.",
                "requires_record_verification": True,
            }],
            "character_count": 180,
            "teacher_revision_required": True,
        }]
        result = parse_response(json.dumps(data))
        assert result[0]["issues"][0]["requires_record_verification"] is True
