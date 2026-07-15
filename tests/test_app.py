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

    def test_normalizes_pronouns_column(self, admin_client):
        captured = {}
        def fake_batch(rows, rubric):
            captured["rows"] = rows
            return DET_RESULT
        with patch.object(flask_app._validator, "run_batch", side_effect=fake_batch), \
             patch.object(flask_app._reviewer, "run_review", return_value=LLM_RESULT):
            admin_client.post("/api/review", json={"rows": ROWS})
        assert "pronoun" in captured["rows"][0]

    def test_status_critical_issue(self, admin_client):
        llm = [{"student_name": "Aarav Sharma", "status": "critical_issue", "issues": [
            {"rule_id": "B2", "severity": "critical", "exact_phrase": "she", "explanation": "pronoun mismatch", "teacher_action": "fix", "requires_record_verification": False}
        ], "character_count": 38}]
        with patch.object(flask_app._validator, "run_batch", return_value=DET_RESULT), \
             patch.object(flask_app._reviewer, "run_review", return_value=llm):
            data = admin_client.post("/api/review", json={"rows": ROWS}).get_json()
        assert data[0]["status"] == "critical_issue"

    def test_no_issues_gives_no_major_issues(self, admin_client):
        det = [{"student_name": "Aarav Sharma", "issues": [], "character_count": 38}]
        llm = [{"student_name": "Aarav Sharma", "status": "no_major_issues", "issues": [], "character_count": 38}]
        with patch.object(flask_app._validator, "run_batch", return_value=det), \
             patch.object(flask_app._reviewer, "run_review", return_value=llm):
            data = admin_client.post("/api/review", json={"rows": ROWS}).get_json()
        assert data[0]["status"] == "no_major_issues"
        assert data[0]["issues"] == []
