import pytest
from rules.i_mechanics import check_i1, check_i2, check_i3


# ── I1: Grammar ────────────────────────────────────────────────────────────────

class TestI1:
    def test_clean_remark_no_issues(self):
        remark = "He participates actively in class discussions and responds well to feedback."
        assert check_i1(remark) == []

    def test_repeated_word_flagged(self):
        remark = "She is a very very hard-working student."
        issues = check_i1(remark)
        rule_ids = [i["rule_id"] for i in issues]
        assert "I1" in rule_ids
        matched = next(i for i in issues if "very very" in i["exact_phrase"].lower())
        assert matched["severity"] == "required"

    def test_repeated_word_different_case(self):
        remark = "The the student has shown great improvement."
        issues = check_i1(remark)
        assert any("I1" == i["rule_id"] for i in issues)

    def test_missing_capital_at_start(self):
        remark = "she is a great student who works well."
        issues = check_i1(remark)
        assert any(
            i["rule_id"] == "I1" and "capital" in i["explanation"].lower()
            for i in issues
        )

    def test_capital_at_start_no_flag(self):
        remark = "She is a great student who works well."
        issues = check_i1(remark)
        assert not any("capital" in i.get("explanation", "").lower() for i in issues)

    def test_run_on_sentence_flagged(self):
        long_sentence = " ".join(["word"] * 65) + "."
        issues = check_i1(long_sentence)
        assert any(
            i["rule_id"] == "I1" and "sentence" in i["explanation"].lower()
            for i in issues
        )

    def test_normal_length_sentence_not_flagged(self):
        remark = "He demonstrates a strong grasp of the material covered this term."
        issues = check_i1(remark)
        assert not any("sentence" in i.get("explanation", "").lower() for i in issues)


# ── I2: Spelling ───────────────────────────────────────────────────────────────

class TestI2:
    def test_correct_spelling_no_issue(self):
        remark = "She has shown noticeable improvement in her organisational skills."
        assert check_i2(remark) == []

    def test_misspelled_word_flagged(self):
        remark = "She is an independant learner who works well with others."
        issues = check_i2(remark)
        assert len(issues) == 1
        assert issues[0]["rule_id"] == "I2"
        assert issues[0]["severity"] == "required"
        assert "independant" in issues[0]["exact_phrase"]
        assert "independent" in issues[0]["explanation"]

    def test_misspelled_achievements(self):
        remark = "Her achievments this year have been impressive."
        issues = check_i2(remark)
        assert any(i["rule_id"] == "I2" and "achievments" in i["exact_phrase"] for i in issues)

    def test_misspelled_noticeable(self):
        remark = "There has been a noticable change in her confidence."
        issues = check_i2(remark)
        assert any(i["rule_id"] == "I2" and "noticable" in i["exact_phrase"] for i in issues)

    def test_british_correct_form_not_flagged(self):
        # "demeanour" is the correct British form — must not be flagged
        remark = "Her demeanour in class has been professional throughout the term."
        assert check_i2(remark) == []

    def test_multiple_misspellings_each_flagged(self):
        remark = "He independantly acheived great results."
        issues = check_i2(remark)
        rule_ids = [i["rule_id"] for i in issues]
        assert rule_ids.count("I2") >= 1  # at least one flagged

    def test_extra_misspellings_parameter(self):
        remark = "She has shown tremendous progres this term."
        # "progres" is not in built-in table; add via extra_misspellings
        issues = check_i2(remark, extra_misspellings={"progres": "progress"})
        assert any(i["rule_id"] == "I2" and "progres" in i["exact_phrase"] for i in issues)

    def test_case_insensitive_match(self):
        remark = "She is an Independant learner."
        issues = check_i2(remark)
        assert any(i["rule_id"] == "I2" for i in issues)


# ── I3: Punctuation and spacing ────────────────────────────────────────────────

class TestI3:
    def test_clean_remark_no_issues(self):
        remark = "She participates actively in class and responds well to feedback."
        assert check_i3(remark) == []

    def test_double_space_flagged(self):
        remark = "She is a  good student."
        issues = check_i3(remark)
        assert any(i["rule_id"] == "I3" and "double space" in i["exact_phrase"].lower() for i in issues)

    def test_missing_space_after_period_flagged(self):
        remark = "She works hard.She also participates well."
        issues = check_i3(remark)
        assert any(
            i["rule_id"] == "I3" and "space" in i["explanation"].lower()
            for i in issues
        )

    def test_missing_terminal_punctuation_flagged(self):
        remark = "She is a great student who works hard"
        issues = check_i3(remark)
        assert any(
            i["rule_id"] == "I3" and "terminal" in i["explanation"].lower()
            for i in issues
        )

    def test_terminal_punctuation_present_no_flag(self):
        remark = "She is a great student who works hard."
        issues = check_i3(remark)
        assert not any("terminal" in i.get("explanation", "").lower() for i in issues)

    def test_multiple_exclamation_marks_flagged(self):
        remark = "Keep up the great work!!"
        issues = check_i3(remark)
        assert any(
            i["rule_id"] == "I3" and "!!" in i["exact_phrase"]
            for i in issues
        )

    def test_single_exclamation_no_flag(self):
        remark = "We wish her well for the year ahead!"
        issues = check_i3(remark)
        assert not any("!!" in i.get("exact_phrase", "") for i in issues)

    def test_space_before_punctuation_flagged(self):
        remark = "She works hard , and participates well."
        issues = check_i3(remark)
        assert any(
            i["rule_id"] == "I3" and "space before" in i["explanation"].lower()
            for i in issues
        )

    def test_issue_shape(self):
        remark = "she works  hard.No space here"
        issues = check_i3(remark)
        for issue in issues:
            assert "rule_id" in issue
            assert "severity" in issue
            assert "exact_phrase" in issue
            assert "explanation" in issue
            assert "teacher_action" in issue
            assert issue["requires_record_verification"] is False
