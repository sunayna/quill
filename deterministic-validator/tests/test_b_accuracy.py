from rules.b_accuracy import check_b1_identity, check_b2_pronouns


class TestB1NamePresent:
    def test_full_name_present_no_issue(self):
        issues = check_b1_identity(
            "Aashvath Sharma has shown consistent growth in his written work.",
            "Aashvath Sharma",
        )
        name_issues = [i for i in issues if i["exact_phrase"] == "Student name not found"]
        assert name_issues == []

    def test_first_name_only_no_false_positive(self):
        # Remark uses first name; full name is "Aashvath Sharma"
        issues = check_b1_identity(
            "Aashvath demonstrates real effort in class discussions and written tasks.",
            "Aashvath Sharma",
        )
        name_issues = [i for i in issues if i["exact_phrase"] == "Student name not found"]
        assert name_issues == []

    def test_last_name_only_no_false_positive(self):
        issues = check_b1_identity(
            "Sharma has made notable progress in Mathematics this term.",
            "Aashvath Sharma",
        )
        name_issues = [i for i in issues if i["exact_phrase"] == "Student name not found"]
        assert name_issues == []

    def test_name_missing_fires_flag(self):
        issues = check_b1_identity(
            "He works hard and participates well in all activities.",
            "Aashvath Sharma",
        )
        name_issues = [i for i in issues if i["exact_phrase"] == "Student name not found"]
        assert len(name_issues) == 1
        assert name_issues[0]["severity"] == "critical"
        assert name_issues[0]["rule_id"] == "B1"

    def test_no_flag_when_no_student_name_given(self):
        issues = check_b1_identity(
            "He works hard and contributes to discussions.",
            "",
        )
        assert issues == []

    def test_name_match_is_case_insensitive(self):
        issues = check_b1_identity(
            "AASHVATH has shown great progress this term.",
            "Aashvath Sharma",
        )
        name_issues = [i for i in issues if i["exact_phrase"] == "Student name not found"]
        assert name_issues == []


class TestB1OtherNames:
    def test_own_name_parts_not_flagged_as_other_names(self):
        issues = check_b1_identity(
            "Aashvath demonstrated leadership. Sharma excelled in Khoj this year.",
            "Aashvath Sharma",
        )
        other = [i for i in issues if i["explanation"] == "Another student name may be present."]
        assert other == []

    def test_pronouns_not_flagged_as_other_names(self):
        issues = check_b1_identity(
            "Aashvath demonstrates effort. His Reading has improved significantly.",
            "Aashvath Sharma",
        )
        other = [i for i in issues if i["explanation"] == "Another student name may be present."]
        assert other == []

    def test_school_terms_not_flagged(self):
        issues = check_b1_identity(
            "Priya participated in the Cricket tournament and the Khoj festival. "
            "Her Assembly performance was impressive.",
            "Priya Mehta",
        )
        other = [i for i in issues if i["explanation"] == "Another student name may be present."]
        assert other == []

    def test_actual_other_name_mid_sentence_is_flagged(self):
        issues = check_b1_identity(
            "Aashvath has grown in confidence. He often supports Rahul during group tasks.",
            "Aashvath Sharma",
        )
        other = [i for i in issues if i["explanation"] == "Another student name may be present."]
        assert len(other) == 1
        assert "Rahul" in other[0]["exact_phrase"]

    def test_word_starting_a_sentence_not_flagged(self):
        # "Rahul" starts the sentence — first word is excluded
        issues = check_b1_identity(
            "Aashvath works independently. Rahul is another matter entirely.",
            "Aashvath Sharma",
        )
        other = [i for i in issues if i["explanation"] == "Another student name may be present."]
        assert other == []

    def test_subjects_and_activities_not_flagged(self):
        issues = check_b1_identity(
            "Aashvath participates actively in Swimming and demonstrates skill in Drawing.",
            "Aashvath Sharma",
        )
        other = [i for i in issues if i["explanation"] == "Another student name may be present."]
        assert other == []

    def test_months_not_flagged(self):
        issues = check_b1_identity(
            "Aashvath showed strong improvement from January through March.",
            "Aashvath Sharma",
        )
        other = [i for i in issues if i["explanation"] == "Another student name may be present."]
        assert other == []

    def test_up_to_three_names_shown_in_phrase(self):
        issues = check_b1_identity(
            "Aashvath frequently assists Rahul, Priya, and Kabir in group work.",
            "Aashvath Sharma",
        )
        other = [i for i in issues if i["explanation"] == "Another student name may be present."]
        assert len(other) == 1
        flagged = other[0]["exact_phrase"]
        parts = [p.strip() for p in flagged.split(",")]
        assert len(parts) <= 3


class TestB2Pronouns:
    def test_matching_pronouns_no_issue(self):
        assert check_b2_pronouns(
            "He works hard and participates well.", "he/him"
        ) == []

    def test_she_pronoun_in_he_remark_flagged(self):
        issues = check_b2_pronouns(
            "He works hard, but she rarely submits work on time.", "he/him"
        )
        assert len(issues) == 1
        assert issues[0]["rule_id"] == "B2"
        assert issues[0]["severity"] == "critical"

    def test_he_pronoun_in_she_remark_flagged(self):
        issues = check_b2_pronouns(
            "She demonstrates strong analytical skills. His contributions are noted.", "she/her"
        )
        assert len(issues) == 1
        assert issues[0]["rule_id"] == "B2"

    def test_no_pronouns_in_roster_no_flag(self):
        issues = check_b2_pronouns(
            "He works hard and she contributes well.", ""
        )
        assert issues == []

    def test_her_in_he_remark_flagged(self):
        issues = check_b2_pronouns(
            "He participates well. Her written work is also strong.", "he/him"
        )
        assert len(issues) == 1
        assert "her" in issues[0]["exact_phrase"].lower()
