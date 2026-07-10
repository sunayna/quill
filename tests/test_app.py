"""
Flask app tests — LLM call is mocked; no Gemini API calls made.
"""
import os
import pytest
from unittest.mock import patch

os.environ.setdefault("GEMINI_API_KEY", "test-key")

import app as flask_app


@pytest.fixture
def client():
    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as c:
        yield c


ROWS = [{"Student Name": "Aarav Sharma", "Pronouns": "he/him", "Teacher Remark": "He works hard and participates well."}]

DET_RESULT = [{
    "student_name": "Aarav Sharma",
    "issues": [],
    "character_count": 38,
}]

LLM_RESULT = [{
    "student_name": "Aarav Sharma",
    "status": "needs_revision",
    "issues": [{
        "rule_id": "A4",
        "severity": "required",
        "exact_phrase": "",
        "explanation": "No clear development point.",
        "teacher_action": "Add a specific growth point.",
        "requires_record_verification": False,
    }],
    "character_count": 38,
}]


class TestIndex:
    def test_serves_html(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Report Remark Reviewer" in resp.data


class TestApiReview:
    def _post(self, client, rows=None):
        with patch.object(flask_app._validator, "run_batch", return_value=DET_RESULT), \
             patch.object(flask_app._reviewer, "run_review", return_value=LLM_RESULT):
            return client.post("/api/review", json={"rows": rows or ROWS})

    def test_returns_200(self, client):
        resp = self._post(client)
        assert resp.status_code == 200

    def test_returns_list(self, client):
        data = self._post(client).get_json()
        assert isinstance(data, list)

    def test_student_name_present(self, client):
        data = self._post(client).get_json()
        assert data[0]["student_name"] == "Aarav Sharma"

    def test_merges_det_and_llm_issues(self, client):
        det = [{
            "student_name": "Aarav Sharma",
            "issues": [{"rule_id": "I3", "severity": "required", "exact_phrase": "  ", "explanation": "double space", "teacher_action": "fix"}],
            "character_count": 38,
        }]
        with patch.object(flask_app._validator, "run_batch", return_value=det), \
             patch.object(flask_app._reviewer, "run_review", return_value=LLM_RESULT):
            data = client.post("/api/review", json={"rows": ROWS}).get_json()
        rule_ids = {i["rule_id"] for i in data[0]["issues"]}
        assert "I3" in rule_ids
        assert "A4" in rule_ids

    def test_det_rule_wins_over_llm_duplicate(self, client):
        det = [{
            "student_name": "Aarav Sharma",
            "issues": [{"rule_id": "A4", "severity": "critical", "exact_phrase": "det phrase", "explanation": "det explanation", "teacher_action": "det action"}],
            "character_count": 38,
        }]
        llm = [{
            "student_name": "Aarav Sharma",
            "status": "critical_issue",
            "issues": [{"rule_id": "A4", "severity": "required", "exact_phrase": "llm phrase", "explanation": "llm explanation", "teacher_action": "llm action", "requires_record_verification": False}],
            "character_count": 38,
        }]
        with patch.object(flask_app._validator, "run_batch", return_value=det), \
             patch.object(flask_app._reviewer, "run_review", return_value=llm):
            data = client.post("/api/review", json={"rows": ROWS}).get_json()
        a4_issues = [i for i in data[0]["issues"] if i["rule_id"] == "A4"]
        assert len(a4_issues) == 1
        assert a4_issues[0]["explanation"] == "det explanation"

    def test_empty_rows_returns_400(self, client):
        resp = client.post("/api/review", json={"rows": []})
        assert resp.status_code == 400

    def test_missing_api_key_returns_500(self, client):
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            resp = client.post("/api/review", json={"rows": ROWS})
        assert resp.status_code == 500

    def test_normalizes_teacher_remark_column(self, client):
        """'Teacher Remark' column maps to 'remark' key before validator/reviewer receive rows."""
        captured = {}
        def fake_batch(rows, rubric):
            captured["rows"] = rows
            return DET_RESULT
        with patch.object(flask_app._validator, "run_batch", side_effect=fake_batch), \
             patch.object(flask_app._reviewer, "run_review", return_value=LLM_RESULT):
            client.post("/api/review", json={"rows": ROWS})
        assert "remark" in captured["rows"][0]

    def test_normalizes_pronouns_column(self, client):
        """'Pronouns' column maps to 'pronoun' key."""
        captured = {}
        def fake_batch(rows, rubric):
            captured["rows"] = rows
            return DET_RESULT
        with patch.object(flask_app._validator, "run_batch", side_effect=fake_batch), \
             patch.object(flask_app._reviewer, "run_review", return_value=LLM_RESULT):
            client.post("/api/review", json={"rows": ROWS})
        assert "pronoun" in captured["rows"][0]

    def test_status_critical_issue(self, client):
        llm = [{
            "student_name": "Aarav Sharma",
            "status": "critical_issue",
            "issues": [{"rule_id": "B2", "severity": "critical", "exact_phrase": "she", "explanation": "pronoun mismatch", "teacher_action": "fix pronoun", "requires_record_verification": False}],
            "character_count": 38,
        }]
        with patch.object(flask_app._validator, "run_batch", return_value=DET_RESULT), \
             patch.object(flask_app._reviewer, "run_review", return_value=llm):
            data = client.post("/api/review", json={"rows": ROWS}).get_json()
        assert data[0]["status"] == "critical_issue"

    def test_no_issues_gives_no_major_issues(self, client):
        det = [{"student_name": "Aarav Sharma", "issues": [], "character_count": 38}]
        llm = [{"student_name": "Aarav Sharma", "status": "no_major_issues", "issues": [], "character_count": 38}]
        with patch.object(flask_app._validator, "run_batch", return_value=det), \
             patch.object(flask_app._reviewer, "run_review", return_value=llm):
            data = client.post("/api/review", json={"rows": ROWS}).get_json()
        assert data[0]["status"] == "no_major_issues"
        assert data[0]["issues"] == []
