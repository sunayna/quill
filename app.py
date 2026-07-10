#!/usr/bin/env python3
import importlib.util
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory

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
SYSTEM_PROMPT_PATH = _ROOT / "prompts" / "system_prompt.md"
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")

rubric = json.loads(RUBRIC_PATH.read_text(encoding="utf-8"))
system_prompt = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")

_SEVERITY_RANK = {"critical": 3, "required": 2, "warning": 1}
_STATUS_MAP = {3: "critical_issue", 2: "needs_revision", 1: "minor_edits"}

app = Flask(__name__, static_folder=str(_ROOT), static_url_path="")


def _status_from_issues(issues):
    if not issues:
        return "no_major_issues"
    return _STATUS_MAP.get(
        max(_SEVERITY_RANK.get(i["severity"], 0) for i in issues), "no_major_issues"
    )


def _normalize_rows(raw_rows):
    """Map frontend column names (Student Name, Teacher Remark, Pronouns) to backend keys."""
    normalized = []
    for row in raw_rows:
        norm = {k.strip().lower().replace(" ", "_"): str(v).strip() for k, v in row.items()}
        if "teacher_remark" in norm and "remark" not in norm:
            norm["remark"] = norm["teacher_remark"]
        if "pronouns" in norm and "pronoun" not in norm:
            norm["pronoun"] = norm["pronouns"]
        normalized.append(norm)
    return normalized


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


@app.route("/")
def index():
    return send_from_directory(str(_ROOT), "index.html")


@app.route("/api/review", methods=["POST"])
def api_review():
    data = request.get_json(force=True)
    rows = data.get("rows", [])
    if not rows:
        return jsonify({"error": "No rows provided"}), 400

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return jsonify({"error": "GEMINI_API_KEY not configured on the server"}), 500

    try:
        norm_rows = _normalize_rows(rows)
        det_results = _validator.run_batch(norm_rows, rubric)
        llm_results = _reviewer.run_review(
            norm_rows, rubric, system_prompt, api_key, model=GEMINI_MODEL
        )
        results = _merge_results(det_results, llm_results)
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
