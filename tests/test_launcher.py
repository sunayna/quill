"""
Unit tests for the pure-logic pieces of start_quillwarden.py -- the parts
that can be verified without actually spinning up a venv, installing
dependencies, or starting the Flask server (which needs a real OS-level
double-click launch to fully exercise, on both Windows and macOS).
"""
import importlib.util
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

spec = importlib.util.spec_from_file_location("start_quillwarden", "start_quillwarden.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

passed = 0
failed = 0


def check(label, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS: {label}")
    else:
        failed += 1
        print(f"FAIL: {label}")


# ---------------------------------------------------------------------------
# venv_python_path: must differ correctly by OS, without needing to actually
# be running on that OS (os.name is patched to simulate both).
# ---------------------------------------------------------------------------
venv_dir = Path("/fake/.venv")

with mock.patch.object(mod.os, "name", "nt"):
    p = mod.venv_python_path(venv_dir)
    check("Windows venv python path uses Scripts/python.exe", str(p) == str(venv_dir / "Scripts" / "python.exe"))

with mock.patch.object(mod.os, "name", "posix"):
    p = mod.venv_python_path(venv_dir)
    check("POSIX venv python path uses bin/python", str(p) == str(venv_dir / "bin" / "python"))

# ---------------------------------------------------------------------------
# read_env_file: basic KEY=VALUE parsing, comments/blank lines ignored,
# missing file returns {} instead of raising.
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    env_path = Path(td) / ".env"
    env_path.write_text(
        "# a comment\n"
        "\n"
        "LLM_PROVIDER=gemini\n"
        "GEMINI_API_KEY=abc123\n"
        "  GROQ_API_KEY = should_still_trim  \n"
    )
    parsed = mod.read_env_file(env_path)
    check("read_env_file parses LLM_PROVIDER", parsed.get("LLM_PROVIDER") == "gemini")
    check("read_env_file parses GEMINI_API_KEY", parsed.get("GEMINI_API_KEY") == "abc123")
    check("read_env_file trims whitespace around key/value", parsed.get("GROQ_API_KEY") == "should_still_trim")
    check("read_env_file skips comments/blank lines (only 3 keys)", len(parsed) == 3)

    missing = mod.read_env_file(Path(td) / "does_not_exist.env")
    check("read_env_file returns {} for a missing file (no exception)", missing == {})

# ---------------------------------------------------------------------------
# check_api_key: the four real scenarios a teacher can hit.
# ---------------------------------------------------------------------------
def make_env_dir(content: str) -> Path:
    d = Path(tempfile.mkdtemp())
    (d / ".env").write_text(content)
    return d

d1 = make_env_dir("LLM_PROVIDER=gemini\nGEMINI_API_KEY=your_key_here\n")
check("gemini + placeholder key -> error mentioning GEMINI_API_KEY",
      mod.check_api_key(d1) is not None and "GEMINI_API_KEY" in mod.check_api_key(d1))

d2 = make_env_dir("LLM_PROVIDER=gemini\nGEMINI_API_KEY=AIzaRealLookingKey123\n")
check("gemini + real-looking key -> no error", mod.check_api_key(d2) is None)

d3 = make_env_dir("LLM_PROVIDER=groq\nGROQ_API_KEY=your_key_here\nGEMINI_API_KEY=AIzaSomething\n")
err3 = mod.check_api_key(d3)
check("groq provider + placeholder GROQ key -> error mentions GROQ_API_KEY (not Gemini's, even though that one is set)",
      err3 is not None and "GROQ_API_KEY" in err3 and "console.groq.com" in err3)

d4 = make_env_dir("")  # no .env at all / empty
err4 = mod.check_api_key(d4)
check("no .env at all -> defaults to gemini and reports missing GEMINI_API_KEY",
      err4 is not None and "GEMINI_API_KEY" in err4)

print("---")
print(f"Total: {passed} passed, {failed} failed")
if failed:
    sys.exit(1)
