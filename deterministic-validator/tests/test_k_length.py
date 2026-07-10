from rules.k_length import check_k1


def test_within_limit_no_issue():
    remark = "A" * 1000
    assert check_k1(remark, max_chars=1000) == []


def test_exactly_at_limit_no_issue():
    remark = "A" * 1000
    assert check_k1(remark, max_chars=1000) == []


def test_one_over_limit_flags():
    remark = "A" * 1001
    issues = check_k1(remark, max_chars=1000)
    assert len(issues) == 1
    assert issues[0]["rule_id"] == "K1"
    assert issues[0]["severity"] == "required"
    assert "1001" in issues[0]["explanation"]
    assert "1000" in issues[0]["explanation"]


def test_well_over_limit_flags():
    remark = "Hello world. " * 100  # ~1300 chars
    issues = check_k1(remark, max_chars=1000)
    assert len(issues) == 1
    assert issues[0]["rule_id"] == "K1"


def test_empty_remark_no_issue():
    assert check_k1("", max_chars=1000) == []


def test_line_breaks_counted_in_length():
    # A remark that fits with line breaks counted
    remark = "A" * 990 + "\n" * 15  # 1005 chars total
    issues = check_k1(remark, max_chars=1000)
    assert len(issues) == 1


def test_custom_limit_respected():
    remark = "A" * 500
    assert check_k1(remark, max_chars=600) == []
    assert len(check_k1(remark, max_chars=400)) == 1


def test_issue_shape():
    remark = "A" * 1001
    issue = check_k1(remark, max_chars=1000)[0]
    assert "rule_id" in issue
    assert "severity" in issue
    assert "exact_phrase" in issue
    assert "explanation" in issue
    assert "teacher_action" in issue
    assert "requires_record_verification" in issue
    assert issue["requires_record_verification"] is False
