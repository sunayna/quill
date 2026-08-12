# Quillwarden — Setup Instructions (Google Sheets version)

## What this is
Quillwarden checks report-card remarks against the school rubric and writes the results into the last column of this sheet — spelling, grammar, naming conventions, length, duplicates, plus (if you set up a Gemini key) tone, safeguarding, and unsupported-claim checks.

## One-time setup for this sheet
1. Open this Google Sheet.
2. Go to **Extensions > Apps Script**.
3. Delete any existing code in the editor.
4. Paste in the Code.gs content (provided separately).
5. Find this line near the top:
   `var LOGIC_FILE_ID = 'PUT_THE_DRIVE_FILE_ID_HERE';`
   Replace the placeholder with the shared logic file's ID — **just the ID, not the full link**.
   - Wrong: `'https://drive.google.com/open?id=1CTah6W0pFKYpeUiahYrv9X1EGxxO1U2F&usp=drive_copy'`
   - Right: `'1CTah6W0pFKYpeUiahYrv9X1EGxxO1U2F'`
   (Get the correct current ID from whoever shared this setup with you.)
6. Click **Save** (the disk icon).
7. Close the Apps Script tab and reload the Google Sheet.
8. A **Quillwarden** menu should now appear next to Help.
9. The first time you click any Quillwarden menu item, Google will ask you to authorize the script:
   - You may see "Google hasn't verified this app" — this is expected for a private script. Click **Advanced**, then **Go to [project name] (unsafe)**, then **Allow**.
   - If it seems to hang after this, check for a second browser tab or popup with the permission prompt — it's waiting on that, not actually stuck.

## Enabling AI review (optional but recommended)
The AI layer catches things spelling/grammar checks can't — generic praise, unsupported claims, tone issues, safeguarding concerns. It's free but needs a personal API key.

1. In the Sheet, click **Quillwarden > Set My Gemini Key**.
2. Follow the on-screen steps: go to **aistudio.google.com/apikey**.
3. **Important: sign in with your personal Gmail account, not your school account.** School-managed Google accounts are blocked from creating a working key.
4. Click **Create API key** (no billing needed for normal use).
5. Copy the key and paste it back into the prompt in the Sheet.
6. You only need to do this once — it's saved privately to your account and no one else using this sheet can see it.

To remove your key later: **Quillwarden > Clear My Gemini Key**.

## How to use it
1. Fill in remarks as usual (Term 1 and/or Term 2 columns).
2. Click **Quillwarden > Review Remarks**.
3. Results appear in the last column, one per student:
   - **NO MAJOR ISSUES** — nothing flagged.
   - **MINOR EDITS** — worth a look, not urgent.
   - **NEEDS REVISION** — should be fixed before submitting.
   - **CRITICAL ISSUE** — must be fixed before submitting (only shows if AI review is enabled).
4. A summary popup appears after each run showing counts by status, and whether AI review ran, was skipped, or failed.
5. If Term 2 is filled in, that's what gets reviewed; otherwise it falls back to Term 1.

## Troubleshooting
- **"Quillwarden failed to load" / DriveApp error**: usually means `LOGIC_FILE_ID` is wrong (check for a pasted URL instead of just the ID) or you don't have access to the shared logic file — ask whoever manages this sheet to confirm you have at least Viewer access to it.
- **Gemini key gives a 403 "denied access" error**: this happens on school-managed Google accounts. Recreate the key using a personal Gmail account instead (see steps above).
- **AI review says "skipped"**: you haven't set a key yet, or it wasn't saved — try Set My Gemini Key again and confirm you see the "Gemini key saved" message.
- **Quillwarden menu doesn't appear at all**: reload the page. If it still doesn't appear, the script may not have saved correctly — check Extensions > Apps Script for errors.
