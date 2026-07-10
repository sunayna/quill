"""
Standalone deterministic validator — no LLM calls.

Public API:
  run_checks(remark, rubric)              -> list of issue dicts
  run_batch(rows, rubric)                 -> list of review_output_schema objects
"""
from rules.i_mechanics import check_i1, check_i2, check_i3
from rules.j_naming import check_j1, check_j2, check_j3, check_j4, check_j5
from rules.k_length import check_k1
from rules.h_consistency import check_h2_within, check_h2_across

_SEVERITY_RANK = {"critical": 3, "required": 2, "warning": 1}
_STATUS_MAP = {3: "critical_issue", 2: "needs_revision", 1: "minor_edits"}


def _get_category(rubric: dict, rule_id: str) -> dict:
    for cat in rubric.get("categories", []):
        if cat.get("id") == rule_id:
            return cat
    return {}


def _status_from_issues(issues: list) -> str:
    if not issues:
        return "no_major_issues"
    rank = max(_SEVERITY_RANK.get(i["severity"], 0) for i in issues)
    return _STATUS_MAP.get(rank, "no_major_issues")


def run_checks(remark: str, rubric: dict) -> list:
    """
    Run all deterministic checks on a single remark.
    Returns a list of issue dicts matching review_output_schema.issues[].
    """
    settings = rubric.get("settings", {})
    issues: list = []

    # I: mechanics
    issues.extend(check_i1(remark))
    issues.extend(check_i2(remark))
    issues.extend(check_i3(remark))

    # J: naming and capitalisation
    j1 = _get_category(rubric, "J1").get("detection", {})
    j2 = _get_category(rubric, "J2").get("detection", {})
    j3 = _get_category(rubric, "J3").get("detection", {})
    j4 = _get_category(rubric, "J4").get("detection", {})
    j5 = _get_category(rubric, "J5").get("detection", {})

    issues.extend(check_j1(remark, j1.get("canonical_forms", [])))
    issues.extend(check_j2(remark,
                           canonical_form=j2.get("canonical_form", "Khoj"),
                           avoid_forms=j2.get("avoid_forms", [])))
    issues.extend(check_j3(remark, j3.get("reference_forms", [])))
    issues.extend(check_j4(remark, j4.get("reference_forms", [])))
    issues.extend(check_j5(remark, j5.get("canonical_forms", [])))

    # K: length
    max_chars = settings.get("max_characters_including_spaces_and_line_breaks", 1000)
    issues.extend(check_k1(remark, max_chars))

    # H2: within-remark duplicates
    issues.extend(check_h2_within(remark))

    return issues


def _norm_row(row: dict) -> tuple[str, str]:
    name = (row.get("student_name") or row.get("name") or "").strip()
    remark = (row.get("remark") or row.get("remarks") or "").strip()
    return name, remark


def run_batch(rows: list, rubric: dict) -> list:
    """
    Run all deterministic checks on a batch of rows, including cross-remark H2 detection.

    Each row must be a dict with at minimum a remark/remarks key and optionally
    student_name/name.

    Returns a list of review_output_schema objects, one per row.
    """
    names = []
    remarks = []
    for row in rows:
        name, remark = _norm_row(row)
        names.append(name)
        remarks.append(remark)

    # Per-remark checks
    per_remark_issues = [run_checks(remark, rubric) for remark in remarks]

    # Cross-remark H2 check
    cross_issues = check_h2_across(remarks, names)

    results = []
    for i, (name, remark) in enumerate(zip(names, remarks)):
        all_issues = per_remark_issues[i] + cross_issues[i]
        results.append({
            "student_name": name,
            "status": _status_from_issues(all_issues),
            "issues": all_issues,
            "character_count": len(remark),
            "teacher_revision_required": bool(all_issues),
        })

    return results
