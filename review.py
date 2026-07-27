#!/usr/bin/env python3
"""
Usage:
  python review.py remarks.csv
  python review.py remarks.csv --output results.json

CSV must have at minimum: student_name (or name), remark (or remarks).
Optional column: pronoun — if present and non-empty, passed to the LLM as verified roster data.
"""
import csv
import importlib.util
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

def _load_module(name, path):
    """Load a module by file path (used for directories with hyphens in their names)."""
    parent = str(path.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

_ROOT = Path(__file__).parent
_validator = _load_module("validator", _ROOT / "deterministic-validator" / "validator.py")
_reviewer  = _load_module("reviewer",  _ROOT / "llm-reviewer" / "reviewer.py")

load_dotenv()

RUBRIC_PATH       = _ROOT / "rubric" / "quillwarden_rubric.json"
SYSTEM_PROMPT_PATH = _ROOT / "prompts" / "system_prompt.md"
GEMINI_MODEL      = "gemini-flash-latest"

_SEVERITY_RANK = {"critical": 3, "required": 2, "warning": 1}
_STATUS_MAP = {3: "critical_issue", 2: "needs_revision", 1: "minor_edits"}


def load_remarks(csv_path):
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            rows.append({k.strip().lower().replace(" ", "_"): v.strip() for k, v in row.items()})
    return rows


def _status_from_issues(issues):
    if not issues:
        return "no_major_issues"
    return _STATUS_MAP.get(max(_SEVERITY_RANK.get(i["severity"], 0) for i in issues), "no_major_issues")


def merge_results(det_results, llm_results):
    """Union issues from both sources per student, deduplicating by rule_id."""
    det_by_name = {r["student_name"]: r for r in det_results}
    llm_by_name = {r["student_name"]: r for r in llm_results}

    # Preserve order from det_results, then append any LLM-only students
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


def print_summary(results):
    counts = {"critical_issue": 0, "needs_revision": 0, "minor_edits": 0, "no_major_issues": 0}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print(f"\n{'─' * 40}")
    print(f"Reviewed: {len(results)} remark(s)")
    print(f"  Critical issues : {counts['critical_issue']}")
    print(f"  Needs revision  : {counts['needs_revision']}")
    print(f"  Minor edits     : {counts['minor_edits']}")
    print(f"  No major issues : {counts['no_major_issues']}")
    print(f"{'─' * 40}")


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    csv_path = args[0]
    output_path = None
    if "--output" in args:
        idx = args.index("--output")
        output_path = args[idx + 1]

    rubric = json.loads(RUBRIC_PATH.read_text(encoding="utf-8"))
    system_prompt = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")

    print(f"Loading {csv_path}...")
    rows = load_remarks(csv_path)
    print(f"  {len(rows)} remark(s) found.")

    print("Running deterministic checks...")
    det_results = _validator.run_batch(rows, rubric)

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY not set. Copy .env.example to .env and add your key.", file=sys.stderr)
        sys.exit(1)

    print(f"Running LLM review ({GEMINI_MODEL})...")
    llm_results = _reviewer.run_review(rows, rubric, system_prompt, api_key, model=GEMINI_MODEL)

    results = merge_results(det_results, llm_results)

    output_json = json.dumps(results, indent=2, ensure_ascii=False)
    if output_path:
        Path(output_path).write_text(output_json, encoding="utf-8")
        print(f"Results saved to {output_path}")
    else:
        print(output_json)

    print_summary(results)


if __name__ == "__main__":
    main()
