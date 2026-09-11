"""
LLM Rubric Reviewer — judgement-based rules only.

Public API:
  build_prompt(rows, rubric, skip_rule_ids)   -> str        (pure, testable)
  parse_response(text)                         -> list[dict] (pure, testable)
  run_review(rows, rubric, system_prompt, ...) -> list[dict] (calls Groq)

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

DEFAULT_MODEL = "openai/gpt-oss-20b"


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
    # The model is told to ignore those rules anyway; no need to send their
    # definitions. Also drop each category's "examples" — useful for a human
    # reading the rubric, but not called out anywhere as a field the model
    # needs (system_prompt.md's own description of the rubric's "binding"
    # fields lists id/policy/detection/exceptions/severity/teacher_message,
    # not examples) — and it was consistently the single biggest field per
    # category. This isn't cosmetic: Groq's free tier caps this app's model
    # at 8,000 tokens PER MINUTE, and the untrimmed rubric alone (sent in
    # full on every single request, regardless of chunk size) already ran
    # to ~9-10K tokens — meaning literally every request was rejected before
    # a single student's remark was even added. Compact (no indent) JSON
    # plus dropping "examples" cuts the rubric portion by roughly 40%.
    trimmed_rubric = {
        **rubric,
        "categories": [
            {k: v for k, v in c.items() if k != "examples"}
            for c in rubric.get("categories", [])
            if c.get("id") not in skip
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
        f"Rubric:\n```json\n{json.dumps(trimmed_rubric, separators=(',', ':'))}\n```"
        f"{skip_note}\n\n"
        f"Student remarks to review:\n{_format_rows(rows)}\n\n"
        "Return a JSON array — one object per student — exactly matching "
        "the review_output_schema in the rubric."
    )


def parse_response(text: str) -> list[dict]:
    """
    Extract and parse the JSON array from the LLM response text.
    Returns an empty list if the response cannot be parsed.

    The model is asked for "a JSON array — one object per student", and with
    several students it reliably wraps them in an array. But for a chunk of
    exactly one student — a single "Recheck this remark" — it can reply
    with just the one object, unwrapped, or wrap the array in a sentence
    or a markdown code fence. That used to be silently discarded here (this
    function only accepted a list), producing an "AI review could not be
    completed" result every single time for that remark, regardless of
    retries, since the request itself had actually succeeded — the parsing
    just threw the answer away. A lone object is now treated the same as a
    one-item array instead of being dropped.
    """
    if not text or not text.strip():
        return []
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return [result]
    except (json.JSONDecodeError, ValueError):
        pass
    # Fallback: extract the outermost [...] block, or else a bare {...} object.
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if m:
        try:
            result = json.loads(m.group())
            if isinstance(result, list):
                return result
        except (json.JSONDecodeError, ValueError):
            pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            result = json.loads(m.group())
            if isinstance(result, dict):
                return [result]
        except (json.JSONDecodeError, ValueError):
            pass
    return []


# ── Impure: calls the Groq API (OpenAI-compatible chat completions) ────────────

_GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"


def _call_groq(
    prompt: str,
    system_prompt: str,
    api_key: str,
    model: str,
    max_completion_tokens: int = 1024,
) -> str:
    """POST to Groq's chat completions endpoint. Returns the assistant's reply text."""
    import time
    import requests

    payload = {
        "model": model,
        # Groq's free-tier tokens-per-minute limit is checked against
        # (prompt tokens + this reserved output budget) on EVERY request,
        # whether or not the model ends up using that many output tokens.
        # A flat 8192 here (sized for a whole-class response, from before
        # chunking existed) was reserving far more than a small chunk could
        # ever need — and for this rubric, the fixed prompt overhead alone
        # (system prompt + trimmed rubric JSON) already runs to ~6,800-7,000
        # real tokens, out of an 8,000 TPM budget. Reserving another 8,192
        # on top of that meant literally every request — regardless of
        # chunk size — asked for ~15,000 tokens against a hard 8,000 cap,
        # so it could never succeed no matter how long a retry waited.
        # See run_review's _estimate_max_completion_tokens for how this is
        # sized per chunk instead.
        "max_completion_tokens": max_completion_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
    }
    if model.startswith("groq/compound"):
        # The compound systems auto-invoke web search / code execution when
        # they judge a query calls for it. That's unwanted here — a rubric
        # review must return plain JSON only, never a tool-call detour, and
        # student remarks should never be sent out to a live web search.
        # An empty enabled_tools list turns compound into a plain
        # text-in/text-out model while keeping its much higher free-tier
        # rate limit.
        payload["compound_custom"] = {"tools": {"enabled_tools": []}}
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    max_attempts = 4
    for attempt in range(max_attempts):
        resp = requests.post(
            _GROQ_CHAT_URL,
            headers=headers,
            json=payload,
            verify=False,   # corporate/school SSL proxy uses a self-signed cert in the chain
            # Requests go out per-chunk (≤8 remarks, or 1 for a single
            # recheck) rather than a whole class in one call, so a slow/stuck
            # connection doesn't need 5 minutes to give up on — 60s is ample
            # for a chunk this size and keeps "Recheck this remark" from
            # looking frozen for many minutes if a request silently hangs.
            timeout=60,
        )
        is_last_attempt = attempt == max_attempts - 1
        if resp.status_code == 429:
            try:
                message = resp.json().get("error", {}).get("message", "")
            except ValueError:
                message = ""
            # Same distinction as Gemini's QuotaExhaustedError check: a
            # tokens-PER-DAY exhaustion (seen in practice as "... on tokens
            # per day (TPD): Limit 200000, Used 197652 ... try again in
            # 31m42s") reports itself as a 429 exactly like a short-lived
            # per-minute rate limit, but won't clear within this function's
            # 15/30/60s retry waits — it needed the ~30 MINUTE wait the
            # message itself named. Retrying it here just repeats the same
            # failure for every remaining chunk; raising immediately lets
            # run_review stop the whole review instead of grinding through
            # hours of identical failures (exactly what happened before
            # this check existed).
            if "tokens per day" in message.lower() or " tpd" in message.lower() or "requests per day" in message.lower():
                raise QuotaExhaustedError(message or f"429 with no error detail: {resp.text[:500]}")
            if is_last_attempt:
                break  # about to give up anyway — no point sleeping first
            wait = 15 * (2 ** attempt)
            print(f"  Rate limited — waiting {wait}s before retry {attempt + 2}/{max_attempts}...")
            time.sleep(wait)
            continue
        if resp.status_code == 413:
            # Groq's free tier caps this model at a fixed tokens-PER-MINUTE
            # budget, and reports exceeding it as a 413 with
            # error.code == "rate_limit_exceeded" (not 429, despite it being
            # the same kind of "too much, too fast" condition) — that
            # recovers on its own once the per-minute window rolls over, so
            # it's worth waiting out and retrying, unlike a genuinely
            # oversized request (error.code == "request_too_large", e.g.
            # from a model with a hard per-request size ceiling), which will
            # just fail again identically no matter how long we wait.
            try:
                code = resp.json().get("error", {}).get("code")
            except ValueError:
                code = None
            if code == "rate_limit_exceeded" and not is_last_attempt:
                wait = 65  # the TPM window is a fixed one-minute budget
                print(f"  Hit the per-minute token budget — waiting {wait}s before retry {attempt + 2}/{max_attempts}...")
                time.sleep(wait)
                continue
            break  # oversized request, or out of attempts — retrying won't help
        if resp.status_code in (500, 502, 503, 504):
            if is_last_attempt:
                break  # about to give up anyway — no point sleeping first
            wait = 5 * (2 ** attempt)
            print(f"  Groq API server error ({resp.status_code}) — waiting {wait}s before retry {attempt + 2}/{max_attempts}...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        choices = resp.json().get("choices", [])
        return choices[0]["message"]["content"] if choices else ""

    resp.raise_for_status()


# ── Impure: calls the Gemini REST API ──────────────────────────────────────────

_GEMINI_REST_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)


def _is_gemini_model(model: str) -> bool:
    return model.startswith("gemini")


class QuotaExhaustedError(Exception):
    """Raised when a provider reports a hard, non-transient quota wall —
    e.g. Gemini's free-tier "generate_content_free_tier_requests" daily
    request cap — as opposed to an ordinary rate limit that clears on its
    own within seconds to minutes. Retrying this the way a normal 429/413
    gets retried is actively harmful: a request-count quota (as opposed to
    a tokens-per-minute one) doesn't refill mid-request the way a rate
    limit does, so every retry just fails identically, and doing that for
    every remaining chunk in a class turns one exhausted quota into hours
    of wasted waiting instead of an immediate, clear stop. run_review
    catches this specifically and aborts the whole review right away
    instead of continuing to the next chunk.
    """


def _call_gemini(prompt: str, system_prompt: str, api_key: str, model: str) -> str:
    """POST to the Gemini REST endpoint. Returns the raw text of the first candidate.

    The original version of this function caught every exception in
    run_review's calling code with a bare `except Exception: parsed = []` —
    meaning whatever actually went wrong (a bad key, a renamed/retired
    model, a quota error, a malformed request) was thrown away with zero
    trace of it anywhere. That is almost certainly why "Gemini wasn't
    working" was never actually diagnosed the first time around — every
    remark just silently came back as AI_ERROR with no way for anyone to
    tell why. This version prints the real error and (when Gemini sends
    one) the response body, the same way _call_groq does, so a real cause
    shows up in server.log instead of a dead end.
    """
    import time
    import requests

    model_id = model.removeprefix("models/")
    url = _GEMINI_REST_URL.format(model=model_id)
    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"response_mime_type": "application/json", "maxOutputTokens": 8192},
    }

    # 503 "the model is currently experiencing high demand" (Google's own
    # overload message, not anything about this app's request) showed up
    # in practice lasting well past the ~35s the old 4-attempt/5-10-20s
    # backoff gave it to clear — every attempt failed the same way. More
    # attempts with a longer tail (up to a couple of minutes) gives a
    # demand spike more realistic room to pass before this chunk gives up.
    max_attempts = 6
    for attempt in range(max_attempts):
        resp = requests.post(
            url,
            params={"key": api_key},
            json=payload,
            verify=False,   # corporate/school SSL proxy uses a self-signed cert in the chain
            timeout=60,
        )
        is_last_attempt = attempt == max_attempts - 1
        if resp.status_code == 429:
            try:
                err = resp.json().get("error", {})
            except ValueError:
                err = {}
            message = err.get("message", "")
            # A per-minute/short-burst rate limit and a hard daily quota
            # both come back as the same 429/RESOURCE_EXHAUSTED — the only
            # thing that tells them apart is the message text. Google's
            # free-tier daily request cap names itself explicitly
            # ("generate_content_free_tier_requests" / "requests per day" /
            # "PerDay") — when we see that, no amount of waiting within
            # this run will help, so stop immediately instead of retrying.
            if "free_tier_requests" in message or "requests per day" in message.lower() or "perday" in message.lower().replace(" ", ""):
                raise QuotaExhaustedError(message or f"429 with no error detail: {resp.text[:500]}")
            if is_last_attempt:
                break
            wait = 15 * (2 ** attempt)
            print(f"  Gemini rate limited — waiting {wait}s before retry {attempt + 2}/{max_attempts}...")
            time.sleep(wait)
            continue
        if resp.status_code in (500, 502, 503, 504):
            if is_last_attempt:
                break
            wait = min(90, 10 * (2 ** attempt))
            print(f"  Gemini API server error ({resp.status_code}) — waiting {wait}s before retry {attempt + 2}/{max_attempts}...")
            time.sleep(wait)
            continue
        if resp.status_code >= 400:
            # Surface the exact error instead of raise_for_status()'s terse
            # summary — this is precisely the detail that was missing
            # before and made the original Gemini failure undiagnosable.
            print(f"  Gemini API error {resp.status_code}: {resp.text[:800]}")
            resp.raise_for_status()
        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            # A response with no candidates usually means the prompt was
            # blocked (promptFeedback.blockReason) rather than a network
            # or auth failure — worth its own message so it doesn't look
            # identical to a parse failure.
            feedback = data.get("promptFeedback")
            print(f"  Gemini returned no candidates. promptFeedback: {feedback}")
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        return parts[0].get("text", "") if parts else ""

    resp.raise_for_status()


# Down to 1 (was 4, before that 8). Measuring the real prompt this app
# sends (system_prompt.md + the trimmed rubric JSON, with zero students
# added) comes to ~6,800-7,000 tokens on its own — already ~85-90% of this
# model's 8,000-tokens-PER-MINUTE free-tier budget before a single
# student's remark is added. Each extra student only adds on the order of
# ~50-100 tokens, so chunking students together barely reduces total
# request count (the fixed cost dominates either way) while eating into
# the very little headroom left over for the model's actual output. One
# student per request maximizes that headroom; see
# _estimate_max_completion_tokens and _MIN_SECONDS_BETWEEN_CALLS below for
# how the remaining budget is spent and paced.
DEFAULT_CHUNK_SIZE = 1

# However large or small a chunk's output budget is, a single request's
# (prompt + reserved output) is already close to this model's entire
# per-minute budget (see DEFAULT_CHUNK_SIZE above) — meaning at most one
# such request can succeed per rolling 60-second window, full stop. Firing
# requests back-to-back just means every request after the first gets
# rejected with a 413 and has to wait out a retry anyway. Proactively
# spacing calls by slightly more than a minute avoids that wasted
# fail-then-retry cycle entirely — it trades "fire immediately, maybe fail
# and wait 65s" for "wait up to ~62s, then reliably succeed".
_MIN_SECONDS_BETWEEN_CALLS = 62


def _estimate_max_completion_tokens(chunk_size: int) -> int:
    """
    How many output tokens to reserve for a Groq request covering this many
    students. Sized to comfortably fit one detailed review_output_schema
    object per student (student_name, status, a handful of issues each
    with a short exact_phrase/explanation/teacher_action, character_count,
    teacher_revision_required) without reserving so much that it pushes
    the request over this model's tight per-minute token budget — see
    DEFAULT_CHUNK_SIZE above for why that budget is so scarce here.
    """
    return min(4096, 500 + 550 * max(1, chunk_size))


def run_review(
    rows: list[dict],
    rubric: dict,
    system_prompt: str,
    api_key: str,
    model: str = DEFAULT_MODEL,
    skip_rule_ids: frozenset[str] | set[str] | None = None,
    chunk_size: int | None = None,
    on_chunk_done=None,
) -> list[dict]:
    """
    Call the configured LLM (Gemini or Groq, chosen by `model`'s name — see
    _is_gemini_model) and return a list of review_output_schema objects.

    Rows are sent in chunks of `chunk_size` students per request rather
    than one request for the whole class. A single huge request risks the
    model's response being truncated once it hits the output token limit —
    for a big class that silently drops the AI review for every student
    after the cut, which looked like the review "stopping in the middle".
    Chunking bounds each response and makes failures local to one chunk.

    If a chunk's call fails even after the retries in _call_groq /
    _call_gemini, that chunk's students still get a result — with a
    visible AI_ERROR issue explaining the AI review didn't come back for
    them this time — instead of silently showing as clean. AI review is
    never silently skipped; a failure to complete it is always surfaced
    to the teacher.

    Args:
        rows: normalised CSV rows.
        rubric: parsed rubric JSON.
        system_prompt: contents of prompts/system_prompt.md.
        api_key: API key for whichever provider `model` selects.
        model: model name — a name starting with "gemini" routes to the
            Gemini REST API; anything else routes to Groq.
        skip_rule_ids: rule IDs to exclude from the LLM review (already deterministically checked).
        chunk_size: how many students to send per request. Defaults to 8
            for Gemini (which has much more per-minute headroom — see
            DEFAULT_CHUNK_SIZE) and 1 for Groq.
        on_chunk_done: optional callback(completed_count, total_count) invoked
            after each chunk finishes, so a caller can report live progress
            ("8 of 24 reviewed") for what would otherwise be one long,
            unmeasured wait.
    """
    import time

    is_gemini = _is_gemini_model(model)
    provider = "Gemini" if is_gemini else "Groq"
    if chunk_size is None:
        # Gemini's own log evidence (server.log) showed 8-student chunks
        # coming back truncated mid-JSON-array before the closing bracket —
        # a detailed review_output_schema object per student for 8 students
        # routinely exceeds the 8,192-output-token budget in _call_gemini's
        # payload. One student per request keeps each response comfortably
        # inside that budget, at the cost of more requests overall — a
        # trade worth making since a truncated chunk currently loses every
        # student in it, not just the one that pushed it over.
        chunk_size = 1 if is_gemini else DEFAULT_CHUNK_SIZE

    results = []
    last_call_started = None
    for start in range(0, len(rows), max(1, chunk_size)):
        chunk = rows[start:start + chunk_size]

        # Proactively pace calls (see _MIN_SECONDS_BETWEEN_CALLS) instead of
        # firing immediately and relying on the 413 retry in _call_groq to
        # wait out a rate limit that a request this size will hit on
        # virtually every attempt except the first in a fresh minute. Only
        # Groq needs this — its free-tier per-minute budget is so tight
        # that one request nearly exhausts it; Gemini's is comfortably
        # larger, so pacing there would just slow things down for nothing
        # and Gemini's own 429 retry in _call_gemini is enough.
        if not is_gemini and last_call_started is not None:
            elapsed = time.monotonic() - last_call_started
            remaining = _MIN_SECONDS_BETWEEN_CALLS - elapsed
            if remaining > 0:
                print(f"  Pacing requests to stay under the per-minute token budget — waiting {remaining:.0f}s before the next chunk...")
                time.sleep(remaining)

        try:
            prompt = build_prompt(chunk, rubric, skip_rule_ids)
            last_call_started = time.monotonic()
            if is_gemini:
                text = _call_gemini(prompt, system_prompt, api_key, model)
            else:
                text = _call_groq(
                    prompt,
                    system_prompt,
                    api_key,
                    model,
                    max_completion_tokens=_estimate_max_completion_tokens(len(chunk)),
                )
            parsed = parse_response(text)
            if not parsed and text and text.strip():
                # The call succeeded (no exception) but nothing usable came
                # out of parse_response — e.g. the model wrapped the JSON in
                # commentary parse_response couldn't strip, or got cut off
                # by the token limit. Print the raw reply so this is
                # diagnosable instead of just showing up as AI_ERROR with
                # zero explanation.
                print(f"  {provider} responded but no JSON could be parsed for this chunk ({len(chunk)} student(s)). Raw reply (truncated): {text[:500]!r}")
        except QuotaExhaustedError as e:
            # A hard quota wall (see QuotaExhaustedError) won't clear by
            # waiting and retrying, and it won't clear for the NEXT chunk
            # either — continuing to grind through every remaining student
            # with the same futile multi-minute retry dance is exactly
            # what turned one exhausted quota into a review that ran for
            # hours without finishing. Stop immediately: mark every
            # not-yet-reviewed student (this chunk and everything after
            # it) with a clear, honest reason, and return what was
            # actually completed instead of hanging.
            remaining_rows = rows[start:]
            print(
                f"  {provider}'s quota is exhausted ({e}) — stopping this review now "
                f"instead of retrying every remaining student. "
                f"{len(results)} of {len(rows)} students were reviewed before this happened; "
                f"the remaining {len(remaining_rows)} are marked as needing a retry later."
            )
            for row in remaining_rows:
                name = row.get("student_name") or row.get("name") or ""
                remark = row.get("remark") or row.get("remarks") or ""
                results.append({
                    "student_name": name,
                    "status": "needs_revision",
                    "issues": [{
                        "rule_id": "AI_ERROR",
                        "severity": "warning",
                        "exact_phrase": "",
                        "explanation": (
                            f"AI review could not be completed: {provider}'s free-tier "
                            "quota for this model was used up during this review."
                        ),
                        "teacher_action": (
                            "Try this student again later (once the quota resets — "
                            "usually the next day for a daily quota), or switch to a "
                            "different model/provider in the meantime."
                        ),
                        "requires_record_verification": False,
                    }],
                    "character_count": len(remark),
                    "teacher_revision_required": True,
                })
            if on_chunk_done:
                try:
                    on_chunk_done(len(results), len(rows))
                except Exception:
                    pass
            return results
        except Exception as e:
            # Never let a chunk fail silently. Print what actually went
            # wrong (a bad API key, an invalid model name, a malformed
            # request, ...) to server.log — otherwise all a teacher ever
            # sees is "AI review could not be completed", with no way for
            # anyone to tell why.
            detail = str(e)
            resp = getattr(e, "response", None)
            if resp is not None:
                detail = f"{detail} — response body: {resp.text[:500]}"
            print(f"  {provider} call failed for this chunk ({len(chunk)} student(s)): {detail}")
            parsed = []

        def _norm_name(s):
            return " ".join(str(s or "").split()).strip().lower()

        parsed_by_name = {
            _norm_name(p.get("student_name")): p for p in parsed if isinstance(p, dict)
        }
        # Positional fallback: if the model's response has exactly as many
        # entries as the chunk we sent, but a name doesn't match exactly
        # (extra whitespace, punctuation, a name it echoed slightly
        # differently), assume it kept the same order rather than treating
        # a cosmetic mismatch as a failed review for that student.
        positional_ok = len(parsed) == len(chunk)

        for idx, row in enumerate(chunk):
            name = row.get("student_name") or row.get("name") or ""
            hit = parsed_by_name.get(_norm_name(name))
            if hit is None and positional_ok and isinstance(parsed[idx], dict):
                hit = parsed[idx]
            if hit is not None:
                results.append(hit)
            else:
                remark = row.get("remark") or row.get("remarks") or ""
                sent_names = [r.get("student_name") for r in chunk]
                got_names = [p.get("student_name") for p in parsed if isinstance(p, dict)]
                print(
                    f"  Could not match a {provider} result to student {name!r}. "
                    f"Sent {len(chunk)} student(s) in this chunk: {sent_names!r}. "
                    f"{provider} returned {len(parsed)} item(s) with names: {got_names!r}."
                )
                results.append({
                    "student_name": name,
                    "status": "needs_revision",
                    "issues": [{
                        "rule_id": "AI_ERROR",
                        "severity": "warning",
                        "exact_phrase": "",
                        "explanation": "AI review could not be completed for this remark this time.",
                        "teacher_action": (
                            "Go back to the Drive folder list and review this file again — "
                            "AI review will be retried for this student."
                        ),
                        "requires_record_verification": False,
                    }],
                    "character_count": len(remark),
                    "teacher_revision_required": True,
                })
        if on_chunk_done:
            try:
                on_chunk_done(len(results), len(rows))
            except Exception:
                pass  # progress reporting must never break the actual review
    return results
