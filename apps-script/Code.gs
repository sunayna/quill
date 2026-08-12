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
 */

var LOGIC_FILE_ID = 'PUT_THE_DRIVE_FILE_ID_HERE';

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
    eval(code);
    QuillwardenDispatch_(action);
  } catch (e) {
    SpreadsheetApp.getUi().alert(
      'Quillwarden failed to load: ' + (e && e.message ? e.message : e) +
      '\n\nCheck that you have access to the shared Quillwarden logic file on Drive, then try again.'
    );
  }
}
