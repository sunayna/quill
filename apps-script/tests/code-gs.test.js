/**
 * Node-side tests for Code.gs -- the thin stub that is the ONLY file
 * physically pasted into a teacher's own Apps Script project. Runs the
 * REAL Code.gs source (loaded the same way Apps Script would run it: as a
 * flat script whose top-level function declarations become globals) against
 * mocks of DriveApp/SpreadsheetApp, with DriveApp serving the REAL
 * quillwarden-logic.js from disk -- so this exercises the actual
 * Drive-fetch -> eval -> dispatch path Code.gs uses in production, not a
 * reimplementation of it.
 *
 * (This file used to also cover a modal progress-dialog feature -- Code.gs
 * grew runReviewNow_/getReviewProgress_ and reviewRemarks() opened a dialog.
 * That was pulled back out after hitting a browser/network condition that
 * silently broke google.script.run; see quillwarden-logic.js's own comment
 * above reviewTermColumn_ for the full story. Progress is shown via toast()
 * now, entirely inside quillwarden-logic.js -- see its own test file -- so
 * Code.gs is back to the simple fetch/eval/dispatch stub tested below.)
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const codeGsSrc = fs.readFileSync(path.join(__dirname, '..', 'Code.gs'), 'utf8');
const logicJsSrc = fs.readFileSync(path.join(__dirname, '..', 'quillwarden-logic.js'), 'utf8');

let passed = 0, failed = 0;
function check(label, cond) {
  if (cond) { passed++; console.log('PASS:', label); }
  else { failed++; console.log('FAIL:', label); }
}

let uiAlerts;
let toastLog;
let currentSheet;
let currentApiKey;

function makeMockSheet(headerRow, dataRows) {
  const grid = [headerRow.slice()].concat(dataRows.map(r => r.slice()));
  const notes = grid.map(row => row.map(() => ''));
  function ensureCols(n) {
    grid.forEach(row => { while (row.length < n) row.push(''); });
    notes.forEach(row => { while (row.length < n) row.push(''); });
  }
  return {
    getLastRow: () => grid.length,
    getLastColumn: () => grid[0].length,
    getRange: (row, col, numRows, numCols) => {
      numRows = numRows || 1; numCols = numCols || 1;
      ensureCols(col + numCols - 1);
      return {
        getValues: () => {
          const out = [];
          for (let r = 0; r < numRows; r++) {
            const rowOut = [];
            for (let c = 0; c < numCols; c++) rowOut.push(grid[row - 1 + r][col - 1 + c]);
            out.push(rowOut);
          }
          return out;
        },
        getNotes: () => {
          const out = [];
          for (let r = 0; r < numRows; r++) {
            const rowOut = [];
            for (let c = 0; c < numCols; c++) rowOut.push(notes[row - 1 + r][col - 1 + c] || '');
            out.push(rowOut);
          }
          return out;
        },
        setValue: function (v) { grid[row - 1][col - 1] = v; return this; },
        setNote: function (n) { notes[row - 1][col - 1] = n; return this; },
      };
    },
    _grid: grid,
  };
}

// Builds a fresh sandbox each time, exactly the way a real Apps Script
// project's global scope works: every top-level function in Code.gs becomes
// a callable global. DriveApp.getFileById(...).getBlob().getDataAsString()
// is wired to return the REAL quillwarden-logic.js source read above.
function makeSandbox() {
  uiAlerts = [];
  toastLog = [];
  const sandbox = {
    console,
    DriveApp: {
      getFileById: (id) => ({
        getBlob: () => ({ getDataAsString: () => logicJsSrc }),
      }),
    },
    SpreadsheetApp: {
      getActiveSheet: () => currentSheet,
      getActiveSpreadsheet: () => ({ toast: (msg, title) => { toastLog.push({ msg: msg, title: title }); } }),
      getUi: () => ({
        alert: (m) => { uiAlerts.push(m); },
        createMenu: () => ({ addItem: function () { return this; }, addToUi: () => {} }),
      }),
    },
    PropertiesService: {
      getUserProperties: () => ({
        getProperty: () => currentApiKey,
        setProperty: () => {},
        deleteProperty: () => {},
      }),
    },
    Utilities: {
      computeDigest: (algo, text) => {
        const crypto = require('crypto');
        const buf = crypto.createHash('md5').update(text, 'utf8').digest();
        return Array.from(buf).map(b => (b > 127 ? b - 256 : b));
      },
      DigestAlgorithm: { MD5: 'MD5' },
      Charset: { UTF_8: 'UTF-8' },
      sleep: () => {},
    },
  };
  vm.createContext(sandbox);
  vm.runInContext(codeGsSrc, sandbox);
  return sandbox;
}

// ---------------------------------------------------------------------------
// reviewRemarks() (the menu item target): fetches the real logic file,
// evaluates it, and dispatches 'reviewRemarks' -- exercised end-to-end
// against a real (mocked) sheet, no shortcuts.
// ---------------------------------------------------------------------------
{
  const sandbox = makeSandbox();
  currentApiKey = null; // no Gemini key -- deterministic-only, keeps this test network-free
  currentSheet = makeMockSheet(
    ['Student Name', 'Remark Term 1', 'Remark Term 2'],
    [['Nadia', 'Nadia is doing well this term.', '']]
  );
  sandbox.reviewRemarks();
  check('reviewRemarks() runs the real review end-to-end and toasts a completion summary',
    toastLog.length === 1 && toastLog[0].title === 'Quillwarden review complete' && /Term 1/.test(toastLog[0].msg));
  check('reviewRemarks() did not alert on the happy path', uiAlerts.length === 0);
}

// ---------------------------------------------------------------------------
// runQuillwarden_ (shared by all three menu items): swallows any error into
// an alert instead of letting it propagate -- the only error-handling
// behavior Code.gs has, now that there's no separate dialog path with its
// own failure handler to worry about.
// ---------------------------------------------------------------------------
{
  const brokenSandbox = vm.createContext({
    console,
    DriveApp: { getFileById: () => { throw new Error('simulated: no access to shared Drive file'); } },
    SpreadsheetApp: {
      getUi: () => ({ alert: (m) => { uiAlerts.push(m); }, createMenu: () => ({ addItem: function () { return this; }, addToUi: () => {} }) }),
    },
  });
  vm.runInContext(codeGsSrc, brokenSandbox);
  uiAlerts = [];
  const returned = brokenSandbox.runQuillwarden_('reviewRemarks');
  check('runQuillwarden_ swallows the error and alerts instead of throwing', uiAlerts.length === 1 && /simulated: no access/.test(uiAlerts[0]));
  check('runQuillwarden_ returns undefined on failure (nothing for a menu click to do with a value)', returned === undefined);
}

// reviewRemarks() itself must alert (not throw) when a required column is
// missing -- this exercises the SAME failure path a teacher would actually
// hit (a typo'd or renamed column), end to end through the real Drive-fetch
// -> eval -> dispatch chain, not just runQuillwarden_ in isolation.
{
  const sandbox = makeSandbox();
  currentApiKey = null;
  currentSheet = makeMockSheet(['Student Name', 'Remark Term 1'], [['Omar', 'Omar is doing fine.']]); // Term 2 column missing
  sandbox.reviewRemarks();
  check('reviewRemarks() alerts (does not throw) when a required column is missing',
    uiAlerts.length === 1 && /Remark Term 2/.test(uiAlerts[0]));
  check('reviewRemarks() did not toast anything when the run failed before reviewing', toastLog.length === 0);
}

// ---------------------------------------------------------------------------
// setGeminiKey_/clearGeminiKey_: just confirm they route through the same
// runQuillwarden_ wrapper (dispatching into the real logic file) rather than
// duplicating any logic of their own in Code.gs.
// ---------------------------------------------------------------------------
{
  const sandbox = makeSandbox();
  currentApiKey = 'existing-key';
  let deletedProperty = null;
  sandbox.PropertiesService = {
    getUserProperties: () => ({
      getProperty: () => currentApiKey,
      setProperty: () => {},
      deleteProperty: (name) => { deletedProperty = name; },
    }),
  };
  // clearGeminiKey_ inside quillwarden-logic.js confirms via
  // ui.alert(title, msg, ui.ButtonSet.YES_NO), comparing the return value
  // against ui.Button.YES -- the mock needs both objects present, and the
  // alert mock returns YES to walk the real confirm-then-delete path.
  sandbox.SpreadsheetApp.getUi = () => ({
    alert: (title) => { uiAlerts.push(title); return 'YES'; },
    ButtonSet: { YES_NO: 'YES_NO' },
    Button: { YES: 'YES' },
  });
  sandbox.clearGeminiKey_();
  check('clearGeminiKey_ dispatches into the real logic file and clears the stored key',
    deletedProperty === 'GEMINI_API_KEY');
}

console.log('---');
console.log(`Total: ${passed} passed, ${failed} failed`);
if (failed) process.exit(1);
