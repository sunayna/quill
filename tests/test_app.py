"""
Flask app tests — auth handled via test fixtures; LLM calls mocked.
"""
import os
import pytest
from unittest.mock import patch
from werkzeug.security import generate_password_hash

os.environ.setdefault("GEMINI_API_KEY", "test-key")

import app as flask_app


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path):
    """Fresh SQLite DB per test, patched onto flask_app.DB_PATH, with seed users."""
    db = tmp_path / "test.db"
    with patch.object(flask_app, "DB_PATH", db):
        flask_app.init_db()
        conn = flask_app.get_db()
        conn.execute(
            "INSERT INTO users (name, email, password_hash, role, sections) VALUES (?,?,?,?,?)",
            ("Admin", "admin@test.com", generate_password_hash("pass"), "admin", ""),
        )
        conn.execute(
            "INSERT INTO users (name, email, password_hash, role, sections) VALUES (?,?,?,?,?)",
            ("Teacher", "teacher@test.com", generate_password_hash("pass"), "teacher", "Grade 6A,Grade 6B"),
        )
        conn.commit()
        conn.close()
        yield db


@pytest.fixture
def client(tmp_db):
    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as c:
        yield c


@pytest.fixture
def admin_client(client):
    client.post("/login", data={"email": "admin@test.com", "password": "pass"})
    return client


@pytest.fixture
def teacher_client(client):
    client.post("/login", data={"email": "teacher@test.com", "password": "pass"})
    return client


# ── Shared test data ───────────────────────────────────────────────────────────

ROWS = [{"Student Name": "Aarav Sharma", "Pronouns": "he/him", "Teacher Remark": "He works hard and participates well."}]

DET_RESULT = [{"student_name": "Aarav Sharma", "issues": [], "character_count": 38}]

LLM_RESULT = [{
    "student_name": "Aarav Sharma",
    "status": "needs_revision",
    "issues": [{"rule_id": "A4", "severity": "required", "exact_phrase": "", "explanation": "No development point.", "teacher_action": "Add a growth point.", "requires_record_verification": False}],
    "character_count": 38,
}]


# ── Auth routes ────────────────────────────────────────────────────────────────

class TestSetup:
    def test_setup_redirects_when_users_exist(self, client):
        resp = client.get("/setup")
        assert resp.status_code == 302

    def test_setup_accessible_on_empty_db(self, tmp_path):
        db = tmp_path / "empty.db"
        with patch.object(flask_app, "DB_PATH", db):
            flask_app.init_db()
            flask_app.app.config["TESTING"] = True
            with flask_app.app.test_client() as c:
                resp = c.get("/setup")
                assert resp.status_code == 200
                assert b"Create administrator" in resp.data


class TestLogin:
    def test_login_page_renders(self, client):
        resp = client.get("/login")
        assert resp.status_code == 200
        assert b"Sign in" in resp.data

    def test_valid_credentials_redirect(self, client):
        resp = client.post("/login", data={"email": "admin@test.com", "password": "pass"})
        assert resp.status_code == 302

    def test_invalid_credentials_show_error(self, client):
        resp = client.post("/login", data={"email": "admin@test.com", "password": "wrong"})
        assert resp.status_code == 200
        assert b"Invalid" in resp.data

    def test_unauthenticated_redirects_to_login(self, client):
        resp = client.get("/")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_logout_redirects(self, admin_client):
        resp = admin_client.get("/logout")
        assert resp.status_code == 302


# ── Index ──────────────────────────────────────────────────────────────────────

class TestIndex:
    def test_serves_html_when_authenticated(self, admin_client):
        resp = admin_client.get("/")
        assert resp.status_code == 200
        assert b"Quill" in resp.data


# ── /api/me ────────────────────────────────────────────────────────────────────

class TestApiMe:
    def test_admin_role_returned(self, admin_client):
        data = admin_client.get("/api/me").get_json()
        assert data["role"] == "admin"
        assert data["name"] == "Admin"

    def test_admin_sections_is_empty(self, admin_client):
        """Admin has no assigned sections so the UI falls back to showing all sections."""
        data = admin_client.get("/api/me").get_json()
        assert data["sections"] == []

    def test_teacher_sections_returned(self, teacher_client):
        data = teacher_client.get("/api/me").get_json()
        assert data["role"] == "teacher"
        assert "Grade 6A" in data["sections"]
        assert "Grade 6B" in data["sections"]

    def test_unauthenticated_returns_302(self, client):
        resp = client.get("/api/me")
        assert resp.status_code == 302


# ── /api/sections ──────────────────────────────────────────────────────────────

class TestApiSections:
    def test_returns_seeded_sections(self, admin_client):
        data = admin_client.get("/api/sections").get_json()
        assert "Grade 4A" in data
        assert "Grade 7I" in data
        assert len(data) == 36

    def test_add_section(self, admin_client):
        resp = admin_client.post("/api/sections", json={"name": "Grade 8A"})
        assert resp.status_code == 200
        data = admin_client.get("/api/sections").get_json()
        assert "Grade 8A" in data

    def test_duplicate_section_returns_409(self, admin_client):
        resp = admin_client.post("/api/sections", json={"name": "Grade 4A"})
        assert resp.status_code == 409

    def test_teacher_cannot_add_section(self, teacher_client):
        resp = teacher_client.post("/api/sections", json={"name": "Grade 8A"})
        assert resp.status_code == 403

    def test_teacher_can_list_all_sections(self, teacher_client):
        """Teachers need all sections for the upload dropdown, not just their own."""
        data = teacher_client.get("/api/sections").get_json()
        assert len(data) == 36
        assert "Grade 4A" in data
        assert "Grade 7I" in data


# ── /api/users ─────────────────────────────────────────────────────────────────

class TestApiUsers:
    def test_admin_can_list_users(self, admin_client):
        data = admin_client.get("/api/users").get_json()
        assert isinstance(data, list)
        assert len(data) == 2

    def test_teacher_cannot_list_users(self, teacher_client):
        resp = teacher_client.get("/api/users")
        assert resp.status_code == 403

    def test_add_teacher(self, admin_client):
        resp = admin_client.post("/api/users", json={
            "name": "New Teacher",
            "email": "new@test.com",
            "password": "pass",
            "role": "teacher",
            "sections": ["Grade 5A"],
        })
        assert resp.status_code == 200
        users = admin_client.get("/api/users").get_json()
        assert any(u["email"] == "new@test.com" for u in users)

    def test_add_user_sections_stored(self, admin_client):
        admin_client.post("/api/users", json={
            "name": "Ms Multi", "email": "multi@test.com",
            "password": "pass", "role": "teacher",
            "sections": ["Grade 6A", "Grade 6B"],
        })
        users = admin_client.get("/api/users").get_json()
        u = next(u for u in users if u["email"] == "multi@test.com")
        assert "Grade 6A" in u["sections"]
        assert "Grade 6B" in u["sections"]

    def test_duplicate_email_returns_409(self, admin_client):
        resp = admin_client.post("/api/users", json={
            "name": "Dup", "email": "teacher@test.com",
            "password": "pass", "role": "teacher", "sections": [],
        })
        assert resp.status_code == 409

    def test_missing_fields_returns_400(self, admin_client):
        resp = admin_client.post("/api/users", json={"name": "No Email"})
        assert resp.status_code == 400

    def test_invalid_role_returns_400(self, admin_client):
        resp = admin_client.post("/api/users", json={
            "name": "X", "email": "x@test.com", "password": "pass", "role": "superuser", "sections": []
        })
        assert resp.status_code == 400


# ── /api/review ────────────────────────────────────────────────────────────────

class TestApiReview:
    def _post(self, client, rows=None):
        with patch.object(flask_app._validator, "run_batch", return_value=DET_RESULT), \
             patch.object(flask_app._reviewer, "run_review", return_value=LLM_RESULT):
            return client.post("/api/review", json={"rows": rows or ROWS})

    def test_returns_200(self, admin_client):
        assert self._post(admin_client).status_code == 200

    def test_returns_list(self, admin_client):
        assert isinstance(self._post(admin_client).get_json(), list)

    def test_student_name_present(self, admin_client):
        data = self._post(admin_client).get_json()
        assert data[0]["student_name"] == "Aarav Sharma"

    def test_teacher_can_also_review(self, teacher_client):
        assert self._post(teacher_client).status_code == 200

    def test_merges_det_and_llm_issues(self, admin_client):
        det = [{"student_name": "Aarav Sharma", "issues": [
            {"rule_id": "I3", "severity": "required", "exact_phrase": "  ", "explanation": "double space", "teacher_action": "fix"}
        ], "character_count": 38}]
        with patch.object(flask_app._validator, "run_batch", return_value=det), \
             patch.object(flask_app._reviewer, "run_review", return_value=LLM_RESULT):
            data = admin_client.post("/api/review", json={"rows": ROWS}).get_json()
        rule_ids = {i["rule_id"] for i in data[0]["issues"]}
        assert "I3" in rule_ids
        assert "A4" in rule_ids

    def test_det_rule_wins_over_llm_duplicate(self, admin_client):
        det = [{"student_name": "Aarav Sharma", "issues": [
            {"rule_id": "A4", "severity": "critical", "exact_phrase": "det", "explanation": "det explanation", "teacher_action": "det action"}
        ], "character_count": 38}]
        llm = [{"student_name": "Aarav Sharma", "status": "critical_issue", "issues": [
            {"rule_id": "A4", "severity": "required", "exact_phrase": "llm", "explanation": "llm explanation", "teacher_action": "llm action", "requires_record_verification": False}
        ], "character_count": 38}]
        with patch.object(flask_app._validator, "run_batch", return_value=det), \
             patch.object(flask_app._reviewer, "run_review", return_value=llm):
            data = admin_client.post("/api/review", json={"rows": ROWS}).get_json()
        a4 = [i for i in data[0]["issues"] if i["rule_id"] == "A4"]
        assert len(a4) == 1
        assert a4[0]["explanation"] == "det explanation"

    def test_empty_rows_returns_400(self, admin_client):
        resp = admin_client.post("/api/review", json={"rows": []})
        assert resp.status_code == 400

    def test_missing_api_key_returns_500(self, admin_client):
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            resp = admin_client.post("/api/review", json={"rows": ROWS})
        assert resp.status_code == 500

    def test_normalizes_teacher_remark_column(self, admin_client):
        captured = {}
        def fake_batch(rows, rubric):
            captured["rows"] = rows
            return DET_RESULT
        with patch.object(flask_app._validator, "run_batch", side_effect=fake_batch), \
             patch.object(flask_app._reviewer, "run_review", return_value=LLM_RESULT):
            admin_client.post("/api/review", json={"rows": ROWS})
        assert "remark" in captured["rows"][0]

    def test_normalizes_teachers_comments_column(self, admin_client):
        """New template column 'Teacher's Comments' maps to 'remark'."""
        new_template_rows = [{"Sr.No": "1", "Admission No.": "ADM001", "Name": "Aarav Sharma", "Teacher's Comments": "He works hard."}]
        captured = {}
        def fake_batch(rows, rubric):
            captured["rows"] = rows
            return [{"student_name": "Aarav Sharma", "issues": [], "character_count": 14}]
        with patch.object(flask_app._validator, "run_batch", side_effect=fake_batch), \
             patch.object(flask_app._reviewer, "run_review", return_value=[{"student_name": "Aarav Sharma", "status": "no_major_issues", "issues": [], "character_count": 14}]):
            resp = admin_client.post("/api/review", json={"rows": new_template_rows})
        assert resp.status_code == 200
        assert captured["rows"][0]["remark"] == "He works hard."

    def test_normalizes_name_column_to_student_name(self, admin_client):
        """New template column 'Name' maps to 'student_name'."""
        new_template_rows = [{"Sr.No": "1", "Admission No.": "ADM001", "Name": "Priya Mehta", "Teacher's Comments": "She is diligent."}]
        captured = {}
        def fake_batch(rows, rubric):
            captured["rows"] = rows
            return [{"student_name": "Priya Mehta", "issues": [], "character_count": 16}]
        with patch.object(flask_app._validator, "run_batch", side_effect=fake_batch), \
             patch.object(flask_app._reviewer, "run_review", return_value=[{"student_name": "Priya Mehta", "status": "no_major_issues", "issues": [], "character_count": 16}]):
            admin_client.post("/api/review", json={"rows": new_template_rows})
        assert captured["rows"][0]["student_name"] == "Priya Mehta"

    def test_normalizes_pronouns_column(self, admin_client):
        captured = {}
        def fake_batch(rows, rubric):
            captured["rows"] = rows
            return DET_RESULT
        with patch.object(flask_app._validator, "run_batch", side_effect=fake_batch), \
             patch.object(flask_app._reviewer, "run_review", return_value=LLM_RESULT):
            admin_client.post("/api/review", json={"rows": ROWS})
        assert "pronoun" in captured["rows"][0]

    def test_skip_llm_mode_returns_deterministic_only(self, admin_client):
        """SKIP_LLM=1 bypasses the LLM call and returns deterministic results only."""
        det = [{"student_name": "Aarav Sharma", "issues": [
            {"rule_id": "I3", "severity": "required", "exact_phrase": "  ", "explanation": "double space", "teacher_action": "fix"}
        ], "character_count": 38}]
        with patch.object(flask_app._validator, "run_batch", return_value=det), \
             patch.dict(os.environ, {"SKIP_LLM": "1"}):
            data = admin_client.post("/api/review", json={"rows": ROWS}).get_json()
        assert len(data[0]["issues"]) == 1
        assert data[0]["issues"][0]["rule_id"] == "I3"

    def test_skip_llm_does_not_require_api_key(self, admin_client):
        """With SKIP_LLM=1, a missing API key should not return 500."""
        with patch.object(flask_app._validator, "run_batch", return_value=DET_RESULT), \
             patch.dict(os.environ, {"SKIP_LLM": "1", "GEMINI_API_KEY": ""}):
            resp = admin_client.post("/api/review", json={"rows": ROWS})
        assert resp.status_code == 200

    def test_status_critical_issue(self, admin_client):
        rows = [{"Student Name": "Aarav Sharma", "Pronouns": "he/him", "Teacher Remark": "He works hard and we look forward to seeing her continue to grow."}]
        llm = [{"student_name": "Aarav Sharma", "status": "critical_issue", "issues": [
            {"rule_id": "B2", "severity": "critical", "exact_phrase": "her", "explanation": "pronoun mismatch", "teacher_action": "fix", "requires_record_verification": False}
        ], "character_count": 38}]
        with patch.object(flask_app._validator, "run_batch", return_value=DET_RESULT), \
             patch.object(flask_app._reviewer, "run_review", return_value=llm):
            data = admin_client.post("/api/review", json={"rows": rows}).get_json()
        assert data[0]["status"] == "critical_issue"

    def test_no_issues_gives_no_major_issues(self, admin_client):
        det = [{"student_name": "Aarav Sharma", "issues": [], "character_count": 38}]
        llm = [{"student_name": "Aarav Sharma", "status": "no_major_issues", "issues": [], "character_count": 38}]
        with patch.object(flask_app._validator, "run_batch", return_value=det), \
             patch.object(flask_app._reviewer, "run_review", return_value=llm):
            data = admin_client.post("/api/review", json={"rows": ROWS}).get_json()
        assert data[0]["status"] == "no_major_issues"
        assert data[0]["issues"] == []


class TestB2Filter:
    """
    Independent backend guardrail on B2 pronoun issues — the LLM doesn't
    reliably honor the "don't flag unverifiable pronouns" rubric instruction,
    so this re-verifies every B2 issue against the actual remark text/roster
    before it reaches the teacher.
    """
    def _b2_issue(self, phrase="her"):
        return {"rule_id": "B2", "severity": "critical", "exact_phrase": phrase,
                "explanation": "No roster data is provided, so pronoun correctness cannot be verified.",
                "teacher_action": "Verify pronouns against the class roster.", "requires_record_verification": False}

    def test_drops_unverifiable_flag_when_no_roster_and_no_contradiction(self, admin_client):
        rows = [{"Student Name": "Aarav Sharma", "Teacher Remark": "He works hard and participates well."}]
        llm = [{"student_name": "Aarav Sharma", "status": "critical_issue", "issues": [self._b2_issue()], "character_count": 38}]
        with patch.object(flask_app._validator, "run_batch", return_value=[{"student_name": "Aarav Sharma", "issues": [], "character_count": 38}]), \
             patch.object(flask_app._reviewer, "run_review", return_value=llm):
            data = admin_client.post("/api/review", json={"rows": rows}).get_json()
        assert data[0]["issues"] == []
        assert data[0]["status"] == "no_major_issues"

    def test_keeps_genuine_internal_contradiction(self, admin_client):
        rows = [{"Student Name": "Aarav Sharma", "Teacher Remark": "He works hard and we look forward to seeing her continue to grow."}]
        llm = [{"student_name": "Aarav Sharma", "status": "critical_issue", "issues": [self._b2_issue()], "character_count": 38}]
        with patch.object(flask_app._validator, "run_batch", return_value=[{"student_name": "Aarav Sharma", "issues": [], "character_count": 38}]), \
             patch.object(flask_app._reviewer, "run_review", return_value=llm):
            data = admin_client.post("/api/review", json={"rows": rows}).get_json()
        assert len(data[0]["issues"]) == 1
        assert data[0]["issues"][0]["rule_id"] == "B2"

    def test_keeps_genuine_roster_contradiction(self, admin_client):
        rows = [{"Student Name": "Aarav Sharma", "Pronouns": "he/him", "Teacher Remark": "She works hard and participates well."}]
        llm = [{"student_name": "Aarav Sharma", "status": "critical_issue", "issues": [self._b2_issue("She")], "character_count": 38}]
        with patch.object(flask_app._validator, "run_batch", return_value=[{"student_name": "Aarav Sharma", "issues": [], "character_count": 38}]), \
             patch.object(flask_app._reviewer, "run_review", return_value=llm):
            data = admin_client.post("/api/review", json={"rows": rows}).get_json()
        assert len(data[0]["issues"]) == 1

    def test_drops_flag_when_roster_matches_remark(self, admin_client):
        rows = [{"Student Name": "Aarav Sharma", "Pronouns": "he/him", "Teacher Remark": "He works hard and participates well."}]
        llm = [{"student_name": "Aarav Sharma", "status": "critical_issue", "issues": [self._b2_issue("he")], "character_count": 38}]
        with patch.object(flask_app._validator, "run_batch", return_value=[{"student_name": "Aarav Sharma", "issues": [], "character_count": 38}]), \
             patch.object(flask_app._reviewer, "run_review", return_value=llm):
            data = admin_client.post("/api/review", json={"rows": rows}).get_json()
        assert data[0]["issues"] == []

    def test_non_b2_issues_unaffected(self, admin_client):
        rows = [{"Student Name": "Aarav Sharma", "Teacher Remark": "He works hard and participates well."}]
        llm = [{"student_name": "Aarav Sharma", "status": "needs_revision", "issues": [
            {"rule_id": "A4", "severity": "required", "exact_phrase": "", "explanation": "No development point.", "teacher_action": "Add a growth point.", "requires_record_verification": False},
            self._b2_issue(),
        ], "character_count": 38}]
        with patch.object(flask_app._validator, "run_batch", return_value=[{"student_name": "Aarav Sharma", "issues": [], "character_count": 38}]), \
             patch.object(flask_app._reviewer, "run_review", return_value=llm):
            data = admin_client.post("/api/review", json={"rows": rows}).get_json()
        rule_ids = {i["rule_id"] for i in data[0]["issues"]}
        assert rule_ids == {"A4"}


# ── /api/review-batches ──────────────────────────────────────────────────────────

SAMPLE_BATCH = {
    "id": "batch-1",
    "className": "Grade 6A",
    "termName": "Term 1",
    "createdAt": "2026-07-21T10:00:00.000Z",
    "status": "in_progress",
    "rubricVersion": "1.2",
    "remarks": [{"id": "batch-1-0", "name": "Aarav Sharma", "srNo": "1", "admissionNo": "ADM001",
                 "originalText": "He works hard.", "currentText": "He works hard.", "issues": [], "status": "pass"}],
}


class TestApiReviewBatches:
    def test_list_empty(self, admin_client):
        resp = admin_client.get("/api/review-batches")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_create_and_list(self, admin_client):
        resp = admin_client.post("/api/review-batches", json=SAMPLE_BATCH)
        assert resp.status_code == 201
        listed = admin_client.get("/api/review-batches").get_json()
        assert len(listed) == 1
        assert listed[0]["id"] == "batch-1"
        assert listed[0]["className"] == "Grade 6A"

    def test_create_missing_field_returns_400(self, admin_client):
        bad = {**SAMPLE_BATCH}
        del bad["className"]
        resp = admin_client.post("/api/review-batches", json=bad)
        assert resp.status_code == 400

    def test_update_persists_changes(self, admin_client):
        admin_client.post("/api/review-batches", json=SAMPLE_BATCH)
        updated = {**SAMPLE_BATCH, "status": "resolved"}
        resp = admin_client.put("/api/review-batches/batch-1", json=updated)
        assert resp.status_code == 200
        listed = admin_client.get("/api/review-batches").get_json()
        assert listed[0]["status"] == "resolved"

    def test_update_missing_batch_returns_404(self, admin_client):
        resp = admin_client.put("/api/review-batches/does-not-exist", json=SAMPLE_BATCH)
        assert resp.status_code == 404

    def test_delete_removes_batch(self, admin_client):
        admin_client.post("/api/review-batches", json=SAMPLE_BATCH)
        resp = admin_client.delete("/api/review-batches/batch-1")
        assert resp.status_code == 200
        assert admin_client.get("/api/review-batches").get_json() == []

    def test_delete_missing_batch_returns_404(self, admin_client):
        resp = admin_client.delete("/api/review-batches/does-not-exist")
        assert resp.status_code == 404

    def test_teacher_can_create_and_delete(self, teacher_client):
        assert teacher_client.post("/api/review-batches", json=SAMPLE_BATCH).status_code == 201
        assert teacher_client.delete("/api/review-batches/batch-1").status_code == 200

    def test_duplicate_section_and_term_both_kept(self, admin_client):
        b2 = {**SAMPLE_BATCH, "id": "batch-2", "createdAt": "2026-07-21T11:00:00.000Z"}
        admin_client.post("/api/review-batches", json=SAMPLE_BATCH)
        admin_client.post("/api/review-batches", json=b2)
        listed = admin_client.get("/api/review-batches").get_json()
        assert len(listed) == 2
        assert {b["id"] for b in listed} == {"batch-1", "batch-2"}


# ── /api/rubric ────────────────────────────────────────────────────────────────

MINIMAL_RUBRIC = {
    "rubric_name": "Test Rubric",
    "version": "2.0",
    "categories": [{"id": "A1", "section": "Required Content", "title": "Clear strength", "severity": "required"}],
}


class TestApiRubric:
    def test_get_returns_rubric_with_categories(self, admin_client):
        data = admin_client.get("/api/rubric").get_json()
        assert "categories" in data
        assert isinstance(data["categories"], list)

    def test_teacher_can_also_get_rubric(self, teacher_client):
        resp = teacher_client.get("/api/rubric")
        assert resp.status_code == 200

    def test_admin_can_upload_rubric(self, admin_client, tmp_path):
        with patch.object(flask_app, "ACTIVE_RUBRIC_PATH", tmp_path / "active_rubric.json"):
            resp = admin_client.post("/api/rubric", json=MINIMAL_RUBRIC)
        assert resp.status_code == 200
        assert resp.get_json().get("ok") is True

    def test_uploaded_rubric_is_persisted(self, admin_client, tmp_path):
        active = tmp_path / "active_rubric.json"
        with patch.object(flask_app, "ACTIVE_RUBRIC_PATH", active):
            admin_client.post("/api/rubric", json=MINIMAL_RUBRIC)
        assert active.exists()
        import json
        saved = json.loads(active.read_text())
        assert saved["version"] == "2.0"

    def test_teacher_cannot_upload_rubric(self, teacher_client):
        resp = teacher_client.post("/api/rubric", json=MINIMAL_RUBRIC)
        assert resp.status_code == 403

    def test_upload_missing_categories_returns_400(self, admin_client):
        resp = admin_client.post("/api/rubric", json={"rubric_name": "Bad", "version": "1.0"})
        assert resp.status_code == 400

    def test_unauthenticated_cannot_get_rubric(self, client):
        resp = client.get("/api/rubric")
        assert resp.status_code == 302


# ── /api/students ──────────────────────────────────────────────────────────────

class TestApiStudentProfile:
    def test_student_not_found_returns_404(self, admin_client):
        resp = admin_client.get("/api/students/NOTEXIST")
        assert resp.status_code == 404

    def test_post_evidence_creates_student_and_returns_evidenced(self, admin_client):
        resp = admin_client.post("/api/students/ADM001/evidence", json={
            "term": "Term 2", "rule_id": "B3", "name": "Aarav Sharma",
            "claim": "Grades have improved", "evidence_text": "Confirmed by academic records",
        })
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "evidenced"

    def test_post_evidence_without_text_is_pending(self, admin_client):
        resp = admin_client.post("/api/students/ADM002/evidence", json={
            "term": "Term 2", "rule_id": "B4", "name": "Priya Mehta",
            "claim": "Activity claim", "evidence_text": "",
        })
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "pending"

    def test_post_evidence_missing_fields_returns_400(self, admin_client):
        resp = admin_client.post("/api/students/ADM003/evidence", json={"term": "Term 2"})
        assert resp.status_code == 400

    def test_get_evidence_returns_logged_items(self, admin_client):
        admin_client.post("/api/students/ADM004/evidence", json={
            "term": "Term 1", "rule_id": "B2", "name": "Test Student",
            "claim": "Pronoun check", "evidence_text": "Verified",
        })
        data = admin_client.get("/api/students/ADM004/evidence").get_json()
        assert isinstance(data, list)
        assert data[0]["claim"] == "Pronoun check"

    def test_post_goals_returns_correct_count(self, admin_client):
        resp = admin_client.post("/api/students/ADM005/goals", json={
            "term": "Term 2", "name": "Test Student",
            "goals": ["Would benefit from regular reading practice."],
            "source_remark": "Full remark text here.",
        })
        assert resp.status_code == 200
        assert resp.get_json()["count"] == 1

    def test_post_goals_missing_term_returns_400(self, admin_client):
        resp = admin_client.post("/api/students/ADM006/goals", json={
            "goals": ["Some goal"], "name": "Student",
        })
        assert resp.status_code == 400

    def test_get_goals_returns_logged_items(self, admin_client):
        admin_client.post("/api/students/ADM007/goals", json={
            "term": "Term 1", "name": "Test Student",
            "goals": ["Encouraged to practise handwriting."],
        })
        data = admin_client.get("/api/students/ADM007/goals").get_json()
        assert any("handwriting" in g["goal_text"] for g in data)

    def test_get_profile_returns_student_evidence_and_goals(self, admin_client):
        admin_client.post("/api/students/ADM008/evidence", json={
            "term": "Term 2", "rule_id": "B3", "name": "Full Profile Student",
            "claim": "Academic claim", "evidence_text": "Verified",
        })
        admin_client.post("/api/students/ADM008/goals", json={
            "term": "Term 2", "name": "Full Profile Student",
            "goals": ["Would benefit from extra reading."],
        })
        data = admin_client.get("/api/students/ADM008").get_json()
        assert data["student"]["name"] == "Full Profile Student"
        assert len(data["evidence"]) == 1
        assert len(data["goals"]) == 1

    def test_teacher_can_post_evidence(self, teacher_client):
        resp = teacher_client.post("/api/students/ADM009/evidence", json={
            "term": "Term 2", "rule_id": "B4", "name": "Teacher Student",
            "claim": "Club membership", "evidence_text": "On Eco Club register",
        })
        assert resp.status_code == 200

    def test_unauthenticated_cannot_access_student(self, client):
        resp = client.get("/api/students/ADM001")
        assert resp.status_code == 302
