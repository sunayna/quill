/**
 * Node-side tests for the per-term review + checksum-skip + AI-error-retry
 * logic in apps-script/quillwarden-logic.js. Runs the REAL production
 * loading mechanism (new Function(code)(), exactly like Code.gs's
 * runQuillwarden_) against a small in-memory Sheet mock, with real MD5s
 * computed via Node's crypto so the signed/unsigned byte conversion in
 * checksum_ is genuinely exercised, not just assumed correct.
 */
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const code = fs.readFileSync(path.join(__dirname, '..', 'quillwarden-logic.js'), 'utf8');

let passed = 0, failed = 0;
function check(label, cond) {
  if (cond) { passed++; console.log('PASS:', label); }
  else { failed++; console.log('FAIL:', label); }
}

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
        setValues: (values) => {
          for (let r = 0; r < numRows; r++)
            for (let c = 0; c < numCols; c++) grid[row - 1 + r][col - 1 + c] = values[r][c];
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
    _notes: notes,
  };
}

function statusOf(sheet, colHeader, rowIdx) {
  const header = sheet._grid[0];
  const col = header.indexOf(colHeader);
  return { value: sheet._grid[rowIdx + 1][col], note: sheet._notes[rowIdx + 1][col] };
}

let currentSheet = null;
let currentApiKey = 'fake-key';
let fetchCallLog = [];
let fetchResponder = null;

let toastLog = [];
global.console = console;
global.SpreadsheetApp = {
  getActiveSheet: () => currentSheet,
  getActiveSpreadsheet: () => ({ toast: (msg, title) => { toastLog.push({ msg: msg, title: title }); } }),
  getUi: () => ({
    alert: (m) => { throw new Error('UNEXPECTED UI ALERT: ' + m); },
  }),
};
global.PropertiesService = {
  getUserProperties: () => ({
    getProperty: () => currentApiKey,
    setProperty: () => {},
    deleteProperty: () => {},
  }),
};
global.Utilities = {
  // Real MD5, converted to Apps Script's documented SIGNED byte convention
  // -- this is exactly what checksum_'s "b < 0 ? b + 256 : b" is undoing.
  computeDigest: (algo, text) => {
    const buf = crypto.createHash('md5').update(text, 'utf8').digest();
    return Array.from(buf).map(b => (b > 127 ? b - 256 : b));
  },
  DigestAlgorithm: { MD5: 'MD5' },
  Charset: { UTF_8: 'UTF-8' },
  sleep: () => {},
};
global.UrlFetchApp = {
  fetch: (url, options) => {
    const payload = JSON.parse(options.payload);
    const promptText = payload.contents[0].parts[0].text;
    const names = [];
    const re = /Student:\s*(.+)/g;
    let m;
    while ((m = re.exec(promptText))) names.push(m[1].trim());
    fetchCallLog.push(names);
    const outcome = fetchResponder(names);
    if (outcome.throwCode) {
      return { getResponseCode: () => outcome.throwCode, getContentText: () => outcome.body };
    }
    return {
      getResponseCode: () => 200,
      getContentText: () => JSON.stringify({ candidates: [{ content: { parts: [{ text: JSON.stringify(outcome.results) }] } }] }),
    };
  },
};
function runQuillwarden(action) {
  // Exactly mirrors Code.gs's runQuillwarden_: fetch the shared logic
  // text (here, read from disk instead of Drive) and invoke it the same way.
  const factory = new Function(code + '\nreturn QuillwardenDispatch_;');
  const dispatch = factory();
  return dispatch(action || 'reviewRemarks');
}

function resultFor(name, status) {
  return { student_name: name, status: status || 'pass', issues: [], character_count: 10, teacher_revision_required: false };
}

// ---------------------------------------------------------------------------
// Scenario: 3 students, Term 1 remarks only (Term 2 blank for now).
// ---------------------------------------------------------------------------
const header = ['Student Name', 'Remark Term 1', 'Remark Term 2'];
const data = [
  ['Aarav', 'Aarav works hard in class.', ''],
  ['Bhavya', 'Bhavya is making steady progress.', ''],
  ['Chirag', 'Chirag participates actively.', ''],
];
currentSheet = makeMockSheet(header, data);

// Run 1: Chirag is dropped from Gemini's reply (simulating a truncated batch).
fetchResponder = (names) => ({ results: names.filter(n => n !== 'Chirag').map(n => resultFor(n)) });
runQuillwarden();

check('run 1: Term 1 output column reuses the original header', currentSheet._grid[0].indexOf('Quillwarden Review') !== -1);
check('run 1: Gemini called once with all 3 students', fetchCallLog.length === 1 && fetchCallLog[0].length === 3);

let aarav1 = statusOf(currentSheet, 'Quillwarden Review', 0);
let chirag1 = statusOf(currentSheet, 'Quillwarden Review', 2);
check('run 1: Aarav has a clean (non-AI_ERROR) result', !/AI_ERROR/.test(aarav1.value));
check('run 1: Chirag is flagged AI_ERROR (unmatched / truncated)', /AI_ERROR/.test(chirag1.value));
check('run 1: Chirag note recorded aiError true', JSON.parse(chirag1.note).aiError === true);
check('run 1: Aarav note recorded aiError false', JSON.parse(aarav1.note).aiError === false);
check('run 1: both notes recorded llmRan true', JSON.parse(aarav1.note).llmRan === true && JSON.parse(chirag1.note).llmRan === true);

// ---------------------------------------------------------------------------
// Run 2: nothing changed. Only Chirag (carried-over AI_ERROR) should be
// resent -- Aarav and Bhavya must be completely untouched (same note).
// ---------------------------------------------------------------------------
fetchCallLog = [];
const aaravNoteBefore = statusOf(currentSheet, 'Quillwarden Review', 0).note;
const bhavyaNoteBefore = statusOf(currentSheet, 'Quillwarden Review', 1).note;
fetchResponder = (names) => ({ results: names.map(n => resultFor(n)) }); // Chirag succeeds this time
runQuillwarden();

check('run 2: Gemini called once with only Chirag (the carried-over AI_ERROR)',
  fetchCallLog.length === 1 && fetchCallLog[0].length === 1 && fetchCallLog[0][0] === 'Chirag');
check('run 2: Aarav row completely untouched', statusOf(currentSheet, 'Quillwarden Review', 0).note === aaravNoteBefore);
check('run 2: Bhavya row completely untouched', statusOf(currentSheet, 'Quillwarden Review', 1).note === bhavyaNoteBefore);
let chirag2 = statusOf(currentSheet, 'Quillwarden Review', 2);
check('run 2: Chirag now clean (retried successfully)', !/AI_ERROR/.test(chirag2.value) && JSON.parse(chirag2.note).aiError === false);

// ---------------------------------------------------------------------------
// Run 3: change only Bhavya's Term 1 remark. Only Bhavya should be resent;
// Aarav and Chirag remain untouched.
// ---------------------------------------------------------------------------
currentSheet._grid[2][1] = 'Bhavya has shown great improvement this term.'; // grid row 2 = Bhavya
fetchCallLog = [];
const chiragNoteBefore = statusOf(currentSheet, 'Quillwarden Review', 2).note;
const aaravNoteBefore2 = statusOf(currentSheet, 'Quillwarden Review', 0).note;
fetchResponder = (names) => ({ results: names.map(n => resultFor(n)) });
runQuillwarden();

check('run 3: Gemini called once with only Bhavya (the changed remark)',
  fetchCallLog.length === 1 && fetchCallLog[0].length === 1 && fetchCallLog[0][0] === 'Bhavya');
check('run 3: Chirag row completely untouched', statusOf(currentSheet, 'Quillwarden Review', 2).note === chiragNoteBefore);
check('run 3: Aarav row completely untouched', statusOf(currentSheet, 'Quillwarden Review', 0).note === aaravNoteBefore2);

// ---------------------------------------------------------------------------
// Run 4: fill in Term 2 for Aarav only. Term 1 must stay untouched; Term 2
// gets its own column and only reviews Aarav (the only non-blank Term 2 row).
// ---------------------------------------------------------------------------
currentSheet._grid[1][2] = 'Aarav has grown in confidence this term.'; // grid row 1 = Aarav, col 2 = Remark Term 2
fetchCallLog = [];
const aaravTerm1NoteBefore = statusOf(currentSheet, 'Quillwarden Review', 0).note;
fetchResponder = (names) => ({ results: names.map(n => resultFor(n)) });
runQuillwarden();

check('run 4: Term 2 output column exists', currentSheet._grid[0].indexOf('Quillwarden Review Term 2') !== -1);
check('run 4: Gemini called once with only Aarav (Term 2)',
  fetchCallLog.length === 1 && fetchCallLog[0].length === 1 && fetchCallLog[0][0] === 'Aarav');
check('run 4: Term 1 output for Aarav untouched by the Term 2 review',
  statusOf(currentSheet, 'Quillwarden Review', 0).note === aaravTerm1NoteBefore);
let aaravTerm2 = statusOf(currentSheet, 'Quillwarden Review Term 2', 0);
check('run 4: Aarav Term 2 cell has a clean result', !/AI_ERROR/.test(aaravTerm2.value));
check('run 4: Bhavya/Chirag have no Term 2 cell value (blank remark)',
  statusOf(currentSheet, 'Quillwarden Review Term 2', 1).value === '' &&
  statusOf(currentSheet, 'Quillwarden Review Term 2', 2).value === '');

// ---------------------------------------------------------------------------
// Run 5: a whole-batch Gemini failure (e.g. genuine quota exhaustion) must
// mark every row in that batch as aiError (so all get retried next time),
// while still surfacing the deterministic-only result for now.
// ---------------------------------------------------------------------------
currentSheet = makeMockSheet(header, [
  ['Dev', 'Dev completes his work on time.', ''],
  ['Esha', 'Esha collaborates well with peers.', ''],
]);
fetchCallLog = [];
fetchResponder = () => ({ throwCode: 429, body: JSON.stringify({ error: { message: 'quota exceeded, no retry info' } }) });
runQuillwarden();

let dev5 = statusOf(currentSheet, 'Quillwarden Review', 0);
let esha5 = statusOf(currentSheet, 'Quillwarden Review', 1);
check('run 5: both rows flagged AI_ERROR after a whole-batch Gemini failure',
  /AI_ERROR/.test(dev5.value) && /AI_ERROR/.test(esha5.value));
check('run 5: both notes recorded aiError true (will retry next run)',
  JSON.parse(dev5.note).aiError === true && JSON.parse(esha5.note).aiError === true);

// Run 6: same content, Gemini now succeeds -- both should be retried
// (aiError carried over) even though their remark text never changed.
fetchCallLog = [];
fetchResponder = (names) => ({ results: names.map(n => resultFor(n)) });
runQuillwarden();
check('run 6: both previously-failed rows resent despite unchanged remarks',
  fetchCallLog.length === 1 && fetchCallLog[0].length === 2);
let dev6 = statusOf(currentSheet, 'Quillwarden Review', 0);
check('run 6: both rows now clean', !/AI_ERROR/.test(dev6.value) && JSON.parse(dev6.note).aiError === false);

// ---------------------------------------------------------------------------
// Run 7: chunking -- 6 students all needing review in one term (chunk size
// is 4), so Gemini must be called twice (4 then 2), not once with all 6.
// ---------------------------------------------------------------------------
const sixNames = ['Farah', 'Gopal', 'Hina', 'Ishaan', 'Jaya', 'Kiran'];
currentSheet = makeMockSheet(header, sixNames.map(n => [n, n + ' is doing well this term.', '']));
fetchCallLog = [];
toastLog = [];
fetchResponder = (names) => ({ results: names.map(n => resultFor(n)) });
runQuillwarden();
check('run 7: 6 students needing review -> 2 Gemini calls (chunk size 4)', fetchCallLog.length === 2);
check('run 7: first chunk has 4 students', fetchCallLog[0] && fetchCallLog[0].length === 4);
check('run 7: second chunk has the remaining 2 students', fetchCallLog[1] && fetchCallLog[1].length === 2);
check('run 7: all 6 rows came back clean', sixNames.every((n, i) => !/AI_ERROR/.test(statusOf(currentSheet, 'Quillwarden Review', i).value)));

// ---------------------------------------------------------------------------
// Run 8: real-time progress via toast() -- the replacement for the modal
// progress dialog (see the comment above reviewTermColumn_ for why the
// dialog was dropped). One toast before the chunk loop starts, one after
// each chunk finishes, and the existing "review complete" summary toast at
// the very end -- all via the same toast() call already relied on
// elsewhere, so no dialog/cache/polling machinery is involved at all.
// ---------------------------------------------------------------------------
check('run 8: toast fired before the chunk loop starts (0 of 6)',
  toastLog.some(t => t.msg === 'Reviewing Term 1: 0 of 6 checked...'));
check('run 8: toast fired after the first chunk (4 of 6)',
  toastLog.some(t => t.msg === 'Reviewing Term 1: 4 of 6 checked...'));
check('run 8: toast fired after the second chunk (6 of 6)',
  toastLog.some(t => t.msg === 'Reviewing Term 1: 6 of 6 checked...'));
check('run 8: final summary toast still fires with the completion title',
  toastLog.some(t => t.title === 'Quillwarden review complete' && /Term 1/.test(t.msg)));

// No Gemini key set -> deterministic-only, finishes near-instantly -> no
// per-chunk progress toasts expected, just the final summary.
currentApiKey = null;
currentSheet = makeMockSheet(header, [
  ['Laila', 'Laila is thriving this term.', ''],
]);
toastLog = [];
runQuillwarden();
check('run 8: no Gemini key -> no per-chunk progress toast, only the final summary',
  toastLog.length === 1 && toastLog[0].title === 'Quillwarden review complete');
currentApiKey = 'fake-key';

// ---------------------------------------------------------------------------
// Run 9: the missing-required-column case still throws (not alerts) from
// this shared file -- Code.gs's own runQuillwarden_ wrapper is what turns
// that into a user-facing alert now that there's no separate dialog path.
// ---------------------------------------------------------------------------
currentSheet = makeMockSheet(['Student Name', 'Remark Term 1'], [['Mira', 'Mira is doing fine.']]); // Term 2 column missing
let threw = false;
let thrownMessage = '';
try { runQuillwarden('reviewRemarks'); } catch (e) { threw = true; thrownMessage = e.message; }
check('run 9: missing a required column throws (not alerts) from this file', threw && /Remark Term 2/.test(thrownMessage));

console.log('---');
console.log(`Total: ${passed} passed, ${failed} failed`);
if (failed) process.exit(1);
