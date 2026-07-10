from rules.h_consistency import check_h2_within, check_h2_across


# ── H2 within-remark ───────────────────────────────────────────────────────────

class TestH2Within:
    def test_no_duplicate_sentences_no_issue(self):
        remark = (
            "He participates actively in class discussions. "
            "His written work has improved noticeably this term. "
            "Reviewing answers before submission will help him reduce errors."
        )
        assert check_h2_within(remark) == []

    def test_repeated_sentence_flagged(self):
        remark = (
            "She works hard and responds well to feedback. "
            "She has shown great progress this term. "
            "She works hard and responds well to feedback."
        )
        issues = check_h2_within(remark)
        assert len(issues) == 1
        assert issues[0]["rule_id"] == "H2"
        assert issues[0]["severity"] == "required"

    def test_repeated_sentence_case_insensitive(self):
        remark = (
            "She works hard and responds well to feedback. "
            "SHE WORKS HARD AND RESPONDS WELL TO FEEDBACK."
        )
        issues = check_h2_within(remark)
        assert len(issues) == 1

    def test_similar_but_different_sentences_no_flag(self):
        remark = (
            "She participates well in class. "
            "She participates well in group activities."
        )
        # Different enough after sentence split — should not flag
        issues = check_h2_within(remark)
        assert len(issues) == 0

    def test_very_short_fragments_not_flagged(self):
        # Fragments under 10 chars are excluded from duplicate detection
        remark = "Good. Good. She has shown progress."
        issues = check_h2_within(remark)
        assert len(issues) == 0

    def test_three_repeated_sentences_two_flagged(self):
        sentence = "She is a hardworking and diligent student."
        remark = f"{sentence} {sentence} {sentence}"
        issues = check_h2_within(remark)
        assert len(issues) == 2  # second and third occurrences flagged

    def test_issue_shape(self):
        remark = "She works hard. She has improved. She works hard."
        issues = check_h2_within(remark)
        for issue in issues:
            assert "rule_id" in issue
            assert "severity" in issue
            assert "exact_phrase" in issue
            assert "explanation" in issue
            assert "teacher_action" in issue
            assert issue["requires_record_verification"] is False


# ── H2 across remarks ─────────────────────────────────────────────────────────

class TestH2Across:
    def test_unique_remarks_no_issue(self):
        remarks = [
            "Aarav has shown great progress in Mathematics this term.",
            "Priya participates actively and submits work on time.",
            "Rohan has improved his focus during independent tasks.",
        ]
        names = ["Aarav", "Priya", "Rohan"]
        result = check_h2_across(remarks, names)
        assert all(issues == [] for issues in result)

    def test_identical_remarks_both_flagged(self):
        remark = "She is a hardworking student who participates actively in all lessons."
        remarks = [remark, remark, "A completely different remark about another student."]
        names = ["Aarav", "Priya", "Rohan"]
        result = check_h2_across(remarks, names)
        assert len(result[0]) == 1
        assert len(result[1]) == 1
        assert result[2] == []
        assert result[0][0]["rule_id"] == "H2"
        assert result[1][0]["rule_id"] == "H2"

    def test_identical_remarks_mention_other_student(self):
        remark = "She participates well."
        remarks = [remark, remark]
        names = ["Aarav", "Priya"]
        result = check_h2_across(remarks, names)
        assert "Priya" in result[0][0]["explanation"]
        assert "Aarav" in result[1][0]["explanation"]

    def test_whitespace_normalised_for_comparison(self):
        remarks = [
            "She is a good student.",
            "She is a good  student.",  # extra space
        ]
        names = ["A", "B"]
        result = check_h2_across(remarks, names)
        assert len(result[0]) == 1
        assert len(result[1]) == 1

    def test_three_identical_remarks_all_flagged(self):
        remark = "He is a diligent and responsible learner."
        remarks = [remark, remark, remark]
        names = ["A", "B", "C"]
        result = check_h2_across(remarks, names)
        # A is matched with B and C; B matched with A and C; C matched with A and B
        assert len(result[0]) == 2
        assert len(result[1]) == 2
        assert len(result[2]) == 2

    def test_empty_remarks_ignored(self):
        remarks = ["", "She is a good student."]
        names = ["A", "B"]
        result = check_h2_across(remarks, names)
        assert result[0] == []
        assert result[1] == []

    def test_returns_one_list_per_remark(self):
        remarks = ["remark one.", "remark two.", "remark three."]
        names = ["A", "B", "C"]
        result = check_h2_across(remarks, names)
        assert len(result) == 3

    def test_severity_is_required(self):
        remark = "She is a good student."
        remarks = [remark, remark]
        names = ["A", "B"]
        result = check_h2_across(remarks, names)
        assert result[0][0]["severity"] == "required"
