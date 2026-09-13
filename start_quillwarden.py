#!/usr/bin/env python3
"""
Quillwarden — cross-platform launcher.

This is the ONE place that knows how to set up and start Quillwarden. It
runs identically on macOS, Windows, and Linux (pure Python: venv creation,
pip install, .env parsing, subprocess management, and opening the browser
via the stdlib `webbrowser` module all behave the same on every OS).

The only OS-specific pieces left are the tiny double-click entry points that
get a Python interpreter running this file in the first place:
  - start_quillwarden.command  (macOS: double-click in Finder)
  - start_quillwarden.bat      (Windows: double-click in Explorer)
Both just find a `python`/`python3` on PATH and run `python start_quillwarden.py`
from this same folder — everything that actually matters lives here.
"""
import os
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PORT = "5050"
BASE_URL = f"http://localhost:{PORT}"


def venv_python_path(venv_dir: Path) -> Path:
    """Path to the venv's own Python interpreter, which differs by OS."""
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def read_env_file(path: Path) -> dict:
    """Minimal KEY=VALUE .env parser -- no python-dotenv dependency, since
    this runs before requirements.txt has necessarily been installed yet."""
    env = {}
    if not path.exists():
        return env
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


def cleanup_stale_setup(script_dir: Path):
    # An earlier setup attempt that got interrupted can leave a stale
    # SQLite journal file behind; a leftover journal without its main file
    # having ever been finalised means the db is in an inconsistent state.
    if (script_dir / "quill.db-journal").exists():
        print("Cleaning up an incomplete previous setup attempt...")
        for name in ("quill.db", "quill.db-journal"):
            p = script_dir / name
            if p.exists():
                p.unlink()


def ensure_venv_and_deps(script_dir: Path) -> Path:
    venv_dir = script_dir / ".venv"
    venv_py = venv_python_path(venv_dir)
    if not venv_py.exists():
        print("Setting up Quillwarden for the first time (this only happens once)...")
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True, cwd=str(script_dir))
        venv_py = venv_python_path(venv_dir)

    subprocess.run(
        [str(venv_py), "-m", "pip", "install", "-q", "-r", "requirements.txt"],
        check=True, cwd=str(script_dir),
    )
    return venv_py


def check_api_key(script_dir: Path):
    """Returns None if a usable key is configured, otherwise the instructions
    string to print before exiting."""
    env = read_env_file(script_dir / ".env")
    provider = (env.get("LLM_PROVIDER") or "gemini").strip().lower()

    if provider == "groq":
        key_name = "GROQ_API_KEY"
        key_url = "https://console.groq.com/keys"
        key_note = "(Groq's free tier needs no billing/credit card to start)"
    else:
        key_name = "GEMINI_API_KEY"
        key_url = "https://aistudio.google.com/apikey"
        key_note = "(Google AI Studio's free tier needs no billing/credit card to start)"

    value = env.get(key_name, "").strip()
    if value and value != "your_key_here":
        return None

    return (
        "\n======================================================\n"
        f" Quillwarden needs a {key_name} before it can start.\n"
        " AI review is required and cannot be skipped.\n"
        f" (Currently configured provider: LLM_PROVIDER={provider})\n"
        "\n"
        f" 1. Get a free key from {key_url}\n"
        f"    {key_note}\n"
        " 2. Open the .env file in this folder\n"
        f" 3. Set: {key_name}=your_real_key_here\n"
        " 4. Save the file and run this script again.\n"
        "\n"
        " (To use the other provider instead, set LLM_PROVIDER=groq or\n"
        " LLM_PROVIDER=gemini in .env and provide that provider's key.)\n"
        "======================================================\n"
    )


def start_server(script_dir: Path, venv_py: Path) -> subprocess.Popen:
    log_path = script_dir / "server.log"
    with log_path.open("a", encoding="utf-8") as logf:
        logf.write(f"\n===== Quillwarden started {datetime.now()} =====\n")

    env = os.environ.copy()
    env["PORT"] = PORT
    env["PYTHONUNBUFFERED"] = "1"

    # stdout+stderr merged and piped so this one process can both show them
    # live in the console window AND mirror them into server.log -- the
    # cross-platform equivalent of the old bash script's
    # `python -u app.py > >(tee -a server.log) 2>&1`, which relied on a
    # bash-only process substitution that doesn't exist on Windows.
    proc = subprocess.Popen(
        [str(venv_py), "-u", "app.py"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env=env, cwd=str(script_dir), text=True, bufsize=1,
    )

    def pump_output():
        with log_path.open("a", encoding="utf-8") as logf:
            for line in proc.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
                logf.write(line)
                logf.flush()

    threading.Thread(target=pump_output, daemon=True).start()
    return proc


def wait_for_server_then_open_browser():
    login_url = f"{BASE_URL}/login"
    for _ in range(30):
        try:
            with urllib.request.urlopen(login_url, timeout=1) as resp:
                if resp.status < 500:
                    webbrowser.open(BASE_URL)
                    return
        except Exception:
            pass
        time.sleep(1)


def main():
    os.chdir(SCRIPT_DIR)
    cleanup_stale_setup(SCRIPT_DIR)
    venv_py = ensure_venv_and_deps(SCRIPT_DIR)

    key_error = check_api_key(SCRIPT_DIR)
    if key_error:
        print(key_error)
        input("Press Return to close this window...")
        sys.exit(1)

    print(
        "\n======================================================\n"
        " Starting Quillwarden...\n"
        " Your browser will open automatically once it's ready.\n"
        "\n"
        " Keep this window open while you use Quillwarden.\n"
        " Close this window (or press Control+C) to stop it.\n"
        "======================================================\n"
    )

    proc = start_server(SCRIPT_DIR, venv_py)
    threading.Thread(target=wait_for_server_then_open_browser, daemon=True).start()

    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
