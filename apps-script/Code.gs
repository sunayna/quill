/**
 * Quillwarden — thin loader stub.
 *
 * This is the ONLY file pasted into a teacher's Apps Script project (bound
 * to their remarks Google Sheet). All the actual logic — every check, the
 * rubric, the system prompt, the Gemini call — lives in one shared file on
 * Drive (see LOGIC_FILE_ID below), fetched fresh and evaluated every time a
 * menu action runs. Editing and saving that Drive file updates every sheet
 * using this stub immediately — including sheets copied long ago — with no
 * re-pasting or version bumping required anywhere.
 *
 * The menu itself (onOpen) is intentionally 100% static and never touches
 * the network, so it always renders even if the Drive fetch fails. Only the
 * individual action functions depend on the live fetch; a failure there
 * shows an alert but leaves the menu untouched for the next click.
 *
 * A real modal progress-bar dialog for "Review Remarks" was tried and
 * pulled back out (see quillwarden-logic.js's own comment above
 * reviewTermColumn_ for the full story) after hitting a browser/network
 * condition that broke google.script.run silently, on top of two other
 * infrastructure issues already fixed along the way. Progress during a run
 * is now shown via repeated toast() notifications instead — same idea,
 * much simpler, and it's the same mechanism already used for the
 * "review complete" summary, so it needs no extra permissions at all.
 *
 * SETUP
 * 1. Open the remarks Google Sheet.
 * 2. Extensions > Apps Script.
 * 3. Delete any starter code, paste this whole file in, save.
 * 4. Set LOGIC_FILE_ID below to the Drive file ID of quillwarden-logic.js
 *    (see apps-script/quillwarden-logic.js in the repo for that file's
 *    content and full setup notes).
 * 5. Reload the sheet — a "Quillwarden" menu appears.
 * 6. Every user who runs this needs at least Viewer access to the Drive
 *    file at LOGIC_FILE_ID — same sharing model as the sheet itself.
 * 7. If this project's manifest (Project Settings, gear icon, tick "Show
 *    'appsscript.json' manifest file in editor") has an explicit
 *    "oauthScopes" array, keep it in sync with apps-script/appsscript.json
 *    in the repo -- an explicit list is enforced exactly as written and is
 *    NOT auto-extended when future code needs a new permission, so a new
 *    restricted API call could otherwise fail forever with "Specified
 *    permissions are not sufficient..." until the scope is added by hand.
 *    Projects with no explicit oauthScopes array don't need this step;
 *    auto-detection just prompts for new permissions as needed.
 */

var LOGIC_FILE_ID = '1CTah6W0pFKYpeUiahYrv9X1EGxxO1U2F';

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Quillwarden')
    .addItem('Review Remarks', 'reviewRemarks')
    .addItem('Set My Gemini Key', 'setGeminiKey_')
    .addItem('Clear My Gemini Key', 'clearGeminiKey_')
    .addToUi();
}

function reviewRemarks() { runQuillwarden_('reviewRemarks'); }
function setGeminiKey_() { runQuillwarden_('setGeminiKey_'); }
function clearGeminiKey_() { runQuillwarden_('clearGeminiKey_'); }

function runQuillwarden_(action) {
  try {
    var code = DriveApp.getFileById(LOGIC_FILE_ID).getBlob().getDataAsString();
    // Apps Script's V8 runtime executes in strict mode, where a direct eval()
    // does NOT leak its function/var declarations into the caller's scope
    // (unlike sloppy-mode JS elsewhere). The Function constructor sidesteps
    // this: it always runs in its own scope, so we explicitly return the one
    // symbol we need (QuillwardenDispatch_) instead of relying on leakage.
    var factory = new Function(code + '\nreturn QuillwardenDispatch_;');
    var dispatch = factory();
    dispatch(action);
  } catch (e) {
    SpreadsheetApp.getUi().alert(
      'Quillwarden failed to load: ' + (e && e.message ? e.message : e) +
      '\n\nCheck that you have access to the shared Quillwarden logic file on Drive, then try again.'
    );
  }
}
