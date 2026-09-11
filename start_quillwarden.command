#!/bin/bash
cd "$(dirname "$0")"

# One-time cleanup: an earlier setup attempt was interrupted and left a stale database.
if [ -f "quill.db-journal" ]; then
  echo "Cleaning up an incomplete previous setup attempt..."
  rm -f quill.db quill.db-journal
fi

if [ ! -f ".venv/bin/activate" ]; then
  echo "Setting up Quillwarden for the first time (this only happens once)..."
  rm -rf .venv
  python3 -m venv .venv
fi

source .venv/bin/activate
pip install -q -r requirements.txt

export PORT=5050

# AI review must never be skipped. Fail fast here with a clear message
# rather than letting every single remark review fail one at a time.
# LLM_PROVIDER in .env picks which key is required (defaults to gemini —
# see app.py's LLM_PROVIDER comment for why).
PROVIDER=$(grep -E '^LLM_PROVIDER=' .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d ' \t\r' | tr '[:upper:]' '[:lower:]')
if [ -z "$PROVIDER" ]; then
  PROVIDER="gemini"
fi

if [ "$PROVIDER" = "groq" ]; then
  KEY_NAME="GROQ_API_KEY"
  KEY_URL="https://console.groq.com/keys"
  KEY_NOTE="(Groq's free tier needs no billing/credit card to start)"
else
  KEY_NAME="GEMINI_API_KEY"
  KEY_URL="https://aistudio.google.com/apikey"
  KEY_NOTE="(Google AI Studio's free tier needs no billing/credit card to start)"
fi

if ! grep -qE "^${KEY_NAME}=.+" .env 2>/dev/null || grep -qE "^${KEY_NAME}=(your_key_here)?\s*$" .env 2>/dev/null; then
  echo ""
  echo "======================================================"
  echo " Quillwarden needs a ${KEY_NAME} before it can start."
  echo " AI review is required and cannot be skipped."
  echo " (Currently configured provider: LLM_PROVIDER=${PROVIDER})"
  echo ""
  echo " 1. Get a free key from ${KEY_URL}"
  echo "    ${KEY_NOTE}"
  echo " 2. Open the .env file in this folder"
  echo " 3. Set: ${KEY_NAME}=your_real_key_here"
  echo " 4. Save the file and run this script again."
  echo ""
  echo " (To use the other provider instead, set LLM_PROVIDER=groq or"
  echo " LLM_PROVIDER=gemini in .env and provide that provider's key.)"
  echo "======================================================"
  echo ""
  read -p "Press Return to close this window..."
  exit 1
fi

echo ""
echo "======================================================"
echo " Starting Quillwarden..."
echo " Your browser will open automatically once it's ready."
echo ""
echo " Keep this window open while you use Quillwarden."
echo " Close this window (or press Control+C) to stop it."
echo "======================================================"
echo ""

# Mirror the server's own output into server.log (in addition to this
# window) so a review error can be diagnosed later without having to have
# watched this terminal live when it happened.
echo "" >> server.log
echo "===== Quillwarden started $(date) =====" >> server.log
# -u: unbuffered stdout/stderr. Without this, print() output only gets
# flushed to server.log in large infrequent chunks once redirected to a
# file/pipe instead of a live terminal (Python fully-buffers non-tty
# stdout) — every diagnostic print added while debugging the Groq
# integration was silently sitting in that buffer, never actually
# reaching server.log, which is why the log kept looking clean even on
# runs that were failing internally.
python -u app.py > >(tee -a server.log) 2>&1 &
SERVER_PID=$!

# Wait for the server to come up, then open it in the default browser.
(
  for i in $(seq 1 30); do
    if curl -s -o /dev/null "http://localhost:5050/login"; then
      open "http://localhost:5050"
      break
    fi
    sleep 1
  done
) &

wait $SERVER_PID