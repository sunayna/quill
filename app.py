#!/usr/bin/env python3
import importlib.util
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, send_from_directory, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()

_ROOT = Path(__file__).parent


def _load_module(name, path):
    parent = str(path.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_validator = _load_module("validator", _ROOT / "deterministic-validator" / "validator.py")
_reviewer = _load_module("reviewer", _ROOT / "llm-reviewer" / "reviewer.py")

RUBRIC_PATH = _ROOT / "rubric" / "report_remark_rubric.json"
ACTIVE_RUBRIC_PATH = _ROOT / "rubric" / "active_rubric.json"
SYSTEM_PROMPT_PATH = _ROOT / "prompts" / "system_prompt.md"
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")


def _load_rubric():
    if ACTIVE_RUBRIC_PATH.exists():
        return json.loads(ACTIVE_RUBRIC_PATH.read_text(encoding="utf-8"))
    return json.loads(RUBRIC_PATH.read_text(encoding="utf-8"))


rubric = _load_rubric()
system_prompt = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")

_SEVERITY_RANK = {"critical": 3, "required": 2, "warning": 1}
_STATUS_MAP = {3: "critical_issue", 2: "needs_revision", 1: "minor_edits"}

DB_PATH = _ROOT / "quill.db"

_DEFAULT_SECTIONS = [
    f"Grade {grade}{sec}"
    for grade in range(4, 8)
    for sec in "ABCDEFGHI"
]

app = Flask(__name__, static_folder=str(_ROOT), static_url_path="")
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")

login_manager = LoginManager(app)
login_manager.login_view = "login_page"


# ── Database ───────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'teacher',
            sections TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS sections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        );
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admission_no TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            pronouns TEXT DEFAULT '',
            section TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS student_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admission_no TEXT NOT NULL,
            term TEXT NOT NULL,
            rule_id TEXT NOT NULL,
            claim TEXT NOT NULL,
            evidence_text TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            added_by TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS student_goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admission_no TEXT NOT NULL,
            name TEXT NOT NULL,
            term TEXT NOT NULL,
            goal_text TEXT NOT NULL,
            source_remark TEXT DEFAULT '',
            status TEXT DEFAULT 'open',
            addressed_term TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS review_batches (
            id TEXT PRIMARY KEY,
            class_name TEXT NOT NULL,
            term_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            created_by TEXT DEFAULT '',
            data TEXT NOT NULL
        );
    """)
    if conn.execute("SELECT COUNT(*) FROM sections").fetchone()[0] == 0:
        conn.executemany(
            "INSERT OR IGNORE INTO sections (name) VALUES (?)",
            [(s,) for s in _DEFAULT_SECTIONS],
        )
    conn.commit()
    conn.close()


init_db()


# ── Auth ───────────────────────────────────────────────────────────────────────

class User(UserMixin):
    def __init__(self, row):
        self.id = str(row["id"])
        self.name = row["name"]
        self.email = row["email"]
        self.role = row["role"]
        self.sections = (
            [s.strip() for s in row["sections"].split(",") if s.strip()]
            if row["sections"] else []
        )


@login_manager.user_loader
def load_user(user_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return User(row) if row else None


# ── Auth routes ────────────────────────────────────────────────────────────────

@app.route("/setup", methods=["GET", "POST"])
def setup():
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    if count > 0:
        return redirect(url_for("login_page"))
    error = None
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not (name and email and password):
            error = "All fields are required."
        else:
            conn = get_db()
            conn.execute(
                "INSERT INTO users (name, email, password_hash, role, sections) VALUES (?, ?, ?, 'admin', '')",
                (name, email, generate_password_hash(password)),
            )
            conn.commit()
            conn.close()
            return redirect(url_for("login_page"))
    return render_template("setup.html", error=error)


@app.route("/login", methods=["GET", "POST"])
def login_page():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    if count == 0:
        return redirect(url_for("setup"))
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        conn = get_db()
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        conn.close()
        if row and check_password_hash(row["password_hash"], password):
            login_user(User(row))
            return redirect(url_for("index"))
        error = "Invalid email or password."
    return render_template("login.html", error=error)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login_page"))


# ── App route ──────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    return send_from_directory(str(_ROOT), "index.html")


# ── API: me ────────────────────────────────────────────────────────────────────

@app.route("/api/me")
@login_required
def api_me():
    return jsonify({
        "name": current_user.name,
        "email": current_user.email,
        "role": current_user.role,
        "sections": current_user.sections,
    })


# ── API: sections ──────────────────────────────────────────────────────────────

@app.route("/api/sections", methods=["GET"])
@login_required
def api_sections_get():
    conn = get_db()
    rows = conn.execute("SELECT name FROM sections ORDER BY name").fetchall()
    conn.close()
    return jsonify([r["name"] for r in rows])


@app.route("/api/sections", methods=["POST"])
@login_required
def api_sections_post():
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403
    name = (request.get_json(force=True) or {}).get("name", "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    try:
        conn = get_db()
        conn.execute("INSERT INTO sections (name) VALUES (?)", (name,))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})
    except sqlite3.IntegrityError:
        return jsonify({"error": "Section already exists"}), 409


# ── API: users ─────────────────────────────────────────────────────────────────

@app.route("/api/users", methods=["GET"])
@login_required
def api_users_get():
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403
    conn = get_db()
    rows = conn.execute(
        "SELECT id, name, email, role, sections FROM users ORDER BY name"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/users", methods=["POST"])
@login_required
def api_users_post():
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403
    data = request.get_json(force=True) or {}
    name = data.get("name", "").strip()
    email = data.get("email", "").strip().lower()
    password = data.get("password", "").strip()
    role = data.get("role", "teacher")
    sections = ",".join(data.get("sections", []))
    if not (name and email and password):
        return jsonify({"error": "name, email and password are required"}), 400
    if role not in ("teacher", "admin"):
        return jsonify({"error": "role must be teacher or admin"}), 400
    try:
        conn = get_db()
        conn.execute(
            "INSERT INTO users (name, email, password_hash, role, sections) VALUES (?, ?, ?, ?, ?)",
            (name, email, generate_password_hash(password), role, sections),
        )
        conn.commit()
        conn.close()
        return jsonify({"ok": True})
    except sqlite3.IntegrityError:
        return jsonify({"error": "Email already exists"}), 409


# ── API: rubric ────────────────────────────────────────────────────────────────

@app.route("/api/rubric", methods=["GET"])
@login_required
def api_rubric_get():
    return jsonify(_load_rubric())


@app.route("/api/rubric", methods=["POST"])
@login_required
def api_rubric_post():
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403
    data = request.get_json(force=True) or {}
    if not isinstance(data.get("categories"), list):
        return jsonify({"error": "Invalid rubric: missing categories array"}), 400
    ACTIVE_RUBRIC_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    global rubric
    rubric = data
    return jsonify({"ok": True})


# ── API: review ────────────────────────────────────────────────────────────────

@app.route("/api/review", methods=["POST"])
@login_required
def api_review():
    data = request.get_json(force=True)
    rows = data.get("rows", [])
    if not rows:
        return jsonify({"error": "No rows provided"}), 400
    try:
        norm_rows = _normalize_rows(rows)
        det_results = _validator.run_batch(norm_rows, rubric)
        if os.environ.get("SKIP_LLM") == "1":
            llm_results = []
        else:
            api_key = os.environ.get("GEMINI_API_KEY")
            if not api_key:
                return jsonify({"error": "GEMINI_API_KEY not configured on the server"}), 500
            llm_results = _reviewer.run_review(
                norm_rows, rubric, system_prompt, api_key, model=GEMINI_MODEL
            )
            llm_results = _filter_b2_issues(llm_results, norm_rows)
        results = _merge_results(det_results, llm_results)
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── API: review batches ─────────────────────────────────────────────────────────

@app.route("/api/review-batches", methods=["GET"])
@login_required
def api_review_batches_list():
    conn = get_db()
    rows = conn.execute("SELECT data FROM review_batches ORDER BY created_at").fetchall()
    conn.close()
    return jsonify([json.loads(r["data"]) for r in rows])


@app.route("/api/review-batches", methods=["POST"])
@login_required
def api_review_batches_create():
    b = request.get_json(force=True)
    for field in ("id", "className", "termName"):
        if not b.get(field):
            return jsonify({"error": f"Missing {field}"}), 400
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO review_batches (id, class_name, term_name, created_at, created_by, data) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (b["id"], b["className"], b["termName"], b.get("createdAt", ""), current_user.name, json.dumps(b)),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True}), 201


@app.route("/api/review-batches/<batch_id>", methods=["PUT"])
@login_required
def api_review_batches_update(batch_id):
    b = request.get_json(force=True)
    conn = get_db()
    cur = conn.execute(
        "UPDATE review_batches SET class_name = ?, term_name = ?, data = ? WHERE id = ?",
        (b.get("className", ""), b.get("termName", ""), json.dumps(b), batch_id),
    )
    conn.commit()
    found = cur.rowcount > 0
    conn.close()
    if not found:
        return jsonify({"error": "Review not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/review-batches/<batch_id>", methods=["DELETE"])
@login_required
def api_review_batches_delete(batch_id):
    conn = get_db()
    cur = conn.execute("DELETE FROM review_batches WHERE id = ?", (batch_id,))
    conn.commit()
    found = cur.rowcount > 0
    conn.close()
    if not found:
        return jsonify({"error": "Review not found"}), 404
    return jsonify({"ok": True})


# ── API: student profiles ──────────────────────────────────────────────────────

@app.route("/api/students/<admission_no>", methods=["GET"])
@login_required
def api_student_get(admission_no):
    conn = get_db()
    student = conn.execute("SELECT * FROM students WHERE admission_no = ?", (admission_no,)).fetchone()
    evidence = conn.execute(
        "SELECT * FROM student_evidence WHERE admission_no = ? ORDER BY created_at DESC",
        (admission_no,),
    ).fetchall()
    goals = conn.execute(
        "SELECT * FROM student_goals WHERE admission_no = ? ORDER BY created_at DESC",
        (admission_no,),
    ).fetchall()
    conn.close()
    if not student:
        return jsonify({"error": "Student not found"}), 404
    return jsonify({
        "student": dict(student),
        "evidence": [dict(e) for e in evidence],
        "goals": [dict(g) for g in goals],
    })


@app.route("/api/students/<admission_no>/evidence", methods=["GET"])
@login_required
def api_student_evidence_get(admission_no):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM student_evidence WHERE admission_no = ? ORDER BY created_at DESC",
        (admission_no,),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/students/<admission_no>/evidence", methods=["POST"])
@login_required
def api_student_evidence_post(admission_no):
    data = request.get_json(force=True) or {}
    term = data.get("term", "").strip()
    rule_id = data.get("rule_id", "").strip()
    claim = data.get("claim", "").strip()
    evidence_text = data.get("evidence_text", "").strip()
    name = data.get("name", "").strip()
    if not (term and rule_id and claim):
        return jsonify({"error": "term, rule_id and claim are required"}), 400
    conn = get_db()
    if name:
        conn.execute(
            "INSERT INTO students (admission_no, name) VALUES (?, ?)"
            " ON CONFLICT(admission_no) DO UPDATE SET updated_at = CURRENT_TIMESTAMP",
            (admission_no, name),
        )
    status = "evidenced" if evidence_text else "pending"
    conn.execute(
        "INSERT INTO student_evidence"
        " (admission_no, term, rule_id, claim, evidence_text, status, added_by)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (admission_no, term, rule_id, claim, evidence_text, status, current_user.name),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "status": status})


@app.route("/api/students/<admission_no>/goals", methods=["GET"])
@login_required
def api_student_goals_get(admission_no):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM student_goals WHERE admission_no = ? ORDER BY created_at DESC",
        (admission_no,),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/students/<admission_no>/goals", methods=["POST"])
@login_required
def api_student_goals_post(admission_no):
    data = request.get_json(force=True) or {}
    goals = data.get("goals", [])
    term = data.get("term", "").strip()
    name = data.get("name", "").strip()
    source_remark = data.get("source_remark", "").strip()
    if not (goals and term):
        return jsonify({"error": "goals and term are required"}), 400
    conn = get_db()
    if name:
        conn.execute(
            "INSERT INTO students (admission_no, name) VALUES (?, ?)"
            " ON CONFLICT(admission_no) DO UPDATE SET updated_at = CURRENT_TIMESTAMP",
            (admission_no, name),
        )
    for goal in goals:
        if goal.strip():
            conn.execute(
                "INSERT INTO student_goals"
                " (admission_no, name, term, goal_text, source_remark)"
                " VALUES (?, ?, ?, ?, ?)",
                (admission_no, name, term, goal.strip(), source_remark),
            )
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "count": len([g for g in goals if g.strip()])})


# ── Helpers ────────────────────────────────────────────────────────────────────

def _status_from_issues(issues):
    if not issues:
        return "no_major_issues"
    return _STATUS_MAP.get(
        max(_SEVERITY_RANK.get(i["severity"], 0) for i in issues), "no_major_issues"
    )


def _normalize_rows(raw_rows):
    normalized = []
    for row in raw_rows:
        norm = {k.strip().lower().replace(" ", "_"): str(v).strip() for k, v in row.items()}
        for alias in ("teacher's_comments", "teacher_remark", "remarks"):
            if alias in norm and "remark" not in norm:
                norm["remark"] = norm[alias]
        if "name" in norm and "student_name" not in norm:
            norm["student_name"] = norm["name"]
        if "pronouns" in norm and "pronoun" not in norm:
            norm["pronoun"] = norm["pronouns"]
        normalized.append(norm)
    return normalized


_HE_FAMILY = re.compile(r'\b(he|him|his)\b', re.IGNORECASE)
_SHE_FAMILY = re.compile(r'\b(she|her|hers)\b', re.IGNORECASE)


def _b2_is_genuine(remark, pronoun):
    """
    Independently verify a B2 pronoun issue rather than trusting the LLM's
    own judgement — keeps only issues backed by an actual internal
    he/she contradiction in the remark, or a contradiction with an
    explicit roster pronoun. Drops any "can't verify without roster" noise.
    """
    remark = remark or ""
    pronoun = (pronoun or "").lower()
    he_family = _HE_FAMILY.search(remark)
    she_family = _SHE_FAMILY.search(remark)
    if pronoun:
        if re.search(r'\bhe\b', pronoun) and she_family:
            return True
        if re.search(r'\bshe\b', pronoun) and he_family:
            return True
        return False
    return bool(he_family and she_family)


def _filter_b2_issues(llm_results, norm_rows):
    row_by_name = {r.get("student_name"): r for r in norm_rows}
    for result in llm_results:
        row = row_by_name.get(result.get("student_name"), {})
        remark = row.get("remark", "")
        pronoun = row.get("pronoun", "")
        result["issues"] = [
            issue for issue in result.get("issues", [])
            if issue.get("rule_id") != "B2" or _b2_is_genuine(remark, pronoun)
        ]
    return llm_results


def _merge_results(det_results, llm_results):
    det_by_name = {r["student_name"]: r for r in det_results}
    llm_by_name = {r["student_name"]: r for r in llm_results}
    seen = set()
    ordered_names = []
    for r in det_results + llm_results:
        n = r["student_name"]
        if n not in seen:
            ordered_names.append(n)
            seen.add(n)
    merged = []
    for name in ordered_names:
        det = det_by_name.get(name, {})
        llm = llm_by_name.get(name, {})
        seen_rules = {}
        for issue in det.get("issues", []):
            seen_rules[issue["rule_id"]] = issue
        for issue in llm.get("issues", []):
            if issue["rule_id"] not in seen_rules:
                seen_rules[issue["rule_id"]] = issue
        all_issues = list(seen_rules.values())
        char_count = det.get("character_count") or llm.get("character_count", 0)
        merged.append({
            "student_name": name,
            "status": _status_from_issues(all_issues),
            "issues": all_issues,
            "character_count": char_count,
            "teacher_revision_required": bool(all_issues),
        })
    return merged


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
