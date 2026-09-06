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

# AI (Gemini) review must never be skipped. Fail fast here with a clear
# message rather than letting every single remark review fail one at a time.
if ! grep -qE '^GEMINI_API_KEY=.+' .env 2>/dev/null || grep -qE '^GEMINI_API_KEY=(your_key_here)?\s*$' .env 2>/dev/null; then
  echo ""
  echo "======================================================"
  echo " Quillwarden needs a Gemini API key before it can start."
  echo " AI review is required and cannot be skipped."
  echo ""
  echo " 1. Get a key from https://aistudio.google.com/apikey"
  echo " 2. Open the .env file in this folder"
  echo " 3. Set: GEMINI_API_KEY=your_real_key_here"
  echo " 4. Save the file and run this script again."
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

python app.py &
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
