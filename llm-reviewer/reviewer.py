"""
LLM Rubric Reviewer — judgement-based rules only.

Public API:
  build_prompt(rows, rubric, skip_rule_ids)   -> str        (pure, testable)
  parse_response(text)                         -> list[dict] (pure, testable)
  run_review(rows, rubric, system_prompt, ...) -> list[dict] (calls Gemini)

The deterministic validator already handles I1–I3, J1–J5, K1, H2.
Those rule IDs are excluded from the LLM prompt so the model focuses
on the remaining judgement-based rules (content, tone, safeguarding,
equity, consistency).
"""
import json
import re

# Rules already handled by deterministic-validator — excluded from the LLM prompt.
DET_RULE_IDS: frozenset[str] = frozenset(
    {"I1", "I2", "I3", "J1", "J2", "J3", "J4", "J5", "K1", "H2"}
)

DEFAULT_MODEL = "gemini-flash-latest"


# ── Pure functions ─────────────────────────────────────────────────────────────

def _format_rows(rows: list[dict]) -> str:
    lines = []
    for i, row in enumerate(rows):
        name = row.get("student_name") or row.get("name") or f"Row {i + 1}"
        remark = row.get("remark") or row.get("remarks") or ""
        pronoun = row.get("pronoun", "").strip()
        entry = f"{i + 1}. Student: {name}"
        if pronoun:
            entry += f" | Pronoun (verified from roster): {pronoun}"
        entry += f"\n   Remark: {remark}"
        lines.append(entry)
    return "\n\n".join(lines)


def build_prompt(
    rows: list[dict],
    rubric: dict,
    skip_rule_ids: frozenset[str] | set[str] | None = None,
) -> str:
    """
    Build the user-turn prompt for the LLM.

    Args:
        rows: normalised CSV rows — each dict must have student_name/name and remark/remarks.
        rubric: parsed rubric JSON (passed to the model as data, not baked into the prompt).
        skip_rule_ids: rule IDs already handled by the deterministic validator.
            Defaults to DET_RULE_IDS. Pass an empty set to ask the LLM to check everything.
    """
    skip = DET_RULE_IDS if skip_rule_ids is None else frozenset(skip_rule_ids)

    # Remove skipped rule definitions from the rubric to reduce prompt size.
    # The model is told to ignore those rules anyway; no need to send their definitions.
    trimmed_rubric = {
        **rubric,
        "categories": [
            c for c in rubric.get("categories", []) if c.get("id") not in skip
        ],
    }

    skip_note = ""
    if skip:
        ids = ", ".join(sorted(skip))
        skip_note = (
            f"\n\nNote: the following rules have already been verified by a deterministic "
            f"validator and must be omitted from your output: {ids}. "
            f"Review only the remaining rules."
        )

    return (
        f"Rubric:\n```json\n{json.dumps(trimmed_rubric, indent=2)}\n```"
        f"{skip_note}\n\n"
        f"Student remarks to review:\n{_format_rows(rows)}\n\n"
        "Return a JSON array — one object per student — exactly matching "
        "the review_output_schema in the rubric."
    )


def parse_response(text: str) -> list[dict]:
    """
    Extract and parse the JSON array from the LLM response text.
    Returns an empty list if the response cannot be parsed.
    """
    if not text or not text.strip():
        return []
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except (json.JSONDecodeError, ValueError):
        pass
    # Fallback: extract the outermost [...] block
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if m:
        try:
            result = json.loads(m.group())
            if isinstance(result, list):
                return result
        except (json.JSONDecodeError, ValueError):
            pass
    return []


# ── Impure: calls the Gemini REST API ─────────────────────────────────────────

_GEMINI_REST_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)


def _call_gemini(prompt: str, system_prompt: str, api_key: str, model: str) -> str:
    """POST to the Gemini REST endpoint. Returns the raw text of the first candidate."""
    import time
    import requests  # stdlib-adjacent; already a transitive dep of google-generativeai

    model_id = model.removeprefix("models/")
    url = _GEMINI_REST_URL.format(model=model_id)
    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"response_mime_type": "application/json"},
    }

    for attempt in range(4):
        resp = requests.post(
            url,
            params={"key": api_key},
            json=payload,
            verify=False,   # corporate SSL proxy uses a self-signed cert in the chain
            timeout=300,
        )
        if resp.status_code == 429:
            wait = 15 * (2 ** attempt)
            print(f"  Rate limited — waiting {wait}s before retry {attempt + 1}/3...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"]

    resp.raise_for_status()


def run_review(
    rows: list[dict],
    rubric: dict,
    system_prompt: str,
    api_key: str,
    model: str = DEFAULT_MODEL,
    skip_rule_ids: frozenset[str] | set[str] | None = None,
) -> list[dict]:
    """
    Call the Gemini API and return a list of review_output_schema objects.

    Args:
        rows: normalised CSV rows.
        rubric: parsed rubric JSON.
        system_prompt: contents of prompts/system_prompt.md.
        api_key: Gemini API key.
        model: Gemini model name.
        skip_rule_ids: rule IDs to exclude from the LLM review (already deterministically checked).
    """
    prompt = build_prompt(rows, rubric, skip_rule_ids)
    text = _call_gemini(prompt, system_prompt, api_key, model)
    return parse_response(text)
