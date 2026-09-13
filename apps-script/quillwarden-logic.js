/**
 * Quillwarden — shared logic file.
 *
 * This file is NOT pasted into any teacher's Apps Script project. It is
 * hosted as a single file on Drive and fetched + eval()'d fresh every time
 * a teacher clicks a Quillwarden menu item (see the thin stub in Code.gs,
 * which every teacher's sheet actually runs). Editing and saving THIS file
 * on Drive updates every sheet using the stub immediately — old copies
 * included — with no re-pasting or version bumping required anywhere.
 *
 * A local copy is kept here in git for history/review, but the copy that
 * actually runs is whatever is currently saved to the Drive file at
 * LOGIC_FILE_ID (see Code.gs). After editing this file, re-upload it to
 * that same Drive file (Drive > right-click the file > Manage versions >
 * Upload new version, or overwrite via the same file ID) for the change
 * to take effect.
 *
 * Ports both halves of the Python project:
 *  - Deterministic checks (rules I1-I3, J1-J3, J5, K1, H2 — J4 sport
 *    capitalisation is intentionally omitted, not just skipped, per school
 *    preference), mirroring deterministic-validator/validator.py and
 *    rules/*.py exactly.
 *  - LLM judgement checks (tone, safeguarding, unsupported claims, balance,
 *    etc.), mirroring llm-reviewer/reviewer.py — one batched Gemini call per
 *    run, using the rubric and system prompt embedded below (rubric v1.2).
 *
 * CONFIG below controls the expected sheet column headers. If a school's
 * sheet uses different headers, this file would need editing (which, again,
 * updates every sheet using it at once — check with anyone else relying on
 * the current headers before changing them here).
 *
 * BEHAVIOUR
 * - Term 1 and Term 2 are reviewed independently, each into its own output
 *   column (CONFIG.OUTPUT_TERM1_HEADER / OUTPUT_TERM2_HEADER) -- reviewing
 *   one term never overwrites the other's result, so a school can review
 *   Term 1 now and Term 2 months later without losing Term 1's record.
 * - Each reviewed cell's Notes field (invisible in the grid) stores a JSON
 *   blob { checksum, aiError, llmRan } -- an MD5 of the remark text, plus
 *   whether that row's AI review completed cleanly last time. On every run,
 *   a row is SKIPPED ENTIRELY (left exactly as it was) unless: its remark
 *   text changed since its stored checksum, OR its last AI review didn't
 *   complete cleanly (aiError), OR a Gemini key is set now but this row has
 *   never actually had an AI pass (llmRan false) -- otherwise re-reviewing
 *   every row on every run would waste Gemini's request quota on remarks
 *   nobody touched. See reviewTermColumn_.
 * - Duplicate-remark detection (H2) still runs across every non-blank remark
 *   for a term on every run (it's free, deterministic, no API call) --
 *   scoped per term, so it will not false-positive a student's own Term 1
 *   remark against their own Term 2 remark.
 * - Deterministic and AI issues are merged per row, deduplicated by rule_id
 *   (deterministic result wins on overlap, though in practice the two layers
 *   cover disjoint rule sets). A row whose Gemini result couldn't be matched
 *   back to it (e.g. a truncated batch reply) gets an explicit AI_ERROR
 *   issue instead of silently showing as clean, and is retried automatically
 *   on the next run regardless of whether its remark changed.
 */

var CONFIG = {
  STUDENT_NAME_HEADER: 'Student Name',
  REMARK_TERM1_HEADER: 'Remark Term 1',
  REMARK_TERM2_HEADER: 'Remark Term 2',
  // Term 1 keeps the original column name so existing sheets/data are reused
  // as-is; Term 2 gets its own new column instead of sharing (and overwriting) it.
  OUTPUT_TERM1_HEADER: 'Quillwarden Review',
  OUTPUT_TERM2_HEADER: 'Quillwarden Review Term 2',
  MAX_CHARACTERS: 1000
};

var GEMINI_MODEL = 'gemini-flash-lite-latest';
var GEMINI_API_KEY_PROPERTY = 'GEMINI_API_KEY';

// Rule IDs already handled by the deterministic checks below (or, for J4,
// deliberately not checked at all per school preference) — excluded from
// the LLM prompt so it only judges the remaining, judgement-based rules.
var LLM_SKIP_RULE_IDS = ['I1', 'I2', 'I3', 'J1', 'J2', 'J3', 'J4', 'J5', 'K1', 'H2'];

// ── Dispatch (entry point called by the stub's runQuillwarden_) ─────────────

function QuillwardenDispatch_(action) {
  if (action === 'reviewRemarks') return reviewRemarks_();
  if (action === 'setGeminiKey_') return setGeminiKey_();
  if (action === 'clearGeminiKey_') return clearGeminiKey_();
  throw new Error('Unknown Quillwarden action: ' + action);
}

function setGeminiKey_() {
  var ui = SpreadsheetApp.getUi();

  var proceed = ui.alert(
    'How to get your Gemini API key',
    'Free, takes about a minute:\n\n' +
    '1. Open a new browser tab and go to:\n' +
    '   aistudio.google.com/apikey\n\n' +
    '2. Sign in with your PERSONAL Google account (Gmail) — NOT your ' +
    'school account. School-managed accounts are blocked from creating ' +
    'a working key.\n\n' +
    '3. Click "Create API key". No billing setup needed for normal use.\n\n' +
    '4. Copy the key it generates (starts with "AIza...").\n\n' +
    '5. Come back to this sheet and click OK below, then paste the key ' +
    'when prompted.\n\n' +
    'Click OK once you have your key copied, or Cancel to stop.',
    ui.ButtonSet.OK_CANCEL
  );
  if (proceed !== ui.Button.OK) return;

  var result = ui.prompt(
    'Paste your Gemini API key',
    'It is stored only under your own Google account and used when you run "Review Remarks".',
    ui.ButtonSet.OK_CANCEL
  );
  if (result.getSelectedButton() !== ui.Button.OK) return;
  var key = result.getResponseText().trim();
  if (!key) {
    ui.alert('No key entered — nothing saved.');
    return;
  }
  PropertiesService.getUserProperties().setProperty(GEMINI_API_KEY_PROPERTY, key);
  ui.alert('Gemini key saved for your account.');
}

function clearGeminiKey_() {
  var ui = SpreadsheetApp.getUi();
  var userProps = PropertiesService.getUserProperties();
  if (!userProps.getProperty(GEMINI_API_KEY_PROPERTY)) {
    ui.alert('No Gemini key is currently set for your account.');
    return;
  }
  var confirm = ui.alert(
    'Clear your Gemini key?',
    'This removes the saved key from your account. "Review Remarks" will fall back to deterministic checks only until you set a new one.',
    ui.ButtonSet.YES_NO
  );
  if (confirm !== ui.Button.YES) return;
  userProps.deleteProperty(GEMINI_API_KEY_PROPERTY);
  ui.alert('Gemini key cleared for your account.');
}

// ── Main driver ──────────────────────────────────────────────────────────────

function reviewRemarks_() {
  var sheet = SpreadsheetApp.getActiveSheet();
  var lastRow = sheet.getLastRow();
  var lastCol = sheet.getLastColumn();
  if (lastRow < 2) {
    // Thrown, not alerted, directly from here -- Code.gs's own runQuillwarden_
    // wrapper is what turns this into a user-facing alert, so this shared
    // function stays UI-agnostic.
    throw new Error('No data rows found below the header row.');
  }

  var headerRow = sheet.getRange(1, 1, 1, lastCol).getValues()[0];
  var nameCol = findColumn_(headerRow, CONFIG.STUDENT_NAME_HEADER);
  var term1Col = findColumn_(headerRow, CONFIG.REMARK_TERM1_HEADER);
  var term2Col = findColumn_(headerRow, CONFIG.REMARK_TERM2_HEADER);

  var missing = [];
  if (nameCol === -1) missing.push(CONFIG.STUDENT_NAME_HEADER);
  if (term1Col === -1) missing.push(CONFIG.REMARK_TERM1_HEADER);
  if (term2Col === -1) missing.push(CONFIG.REMARK_TERM2_HEADER);
  if (missing.length) {
    var msg = 'Could not find column(s): ' + missing.join(', ') +
      '.\n\nThe expected headers are set in CONFIG inside the shared Quillwarden logic file on Drive (found headers: ' +
      headerRow.join(' | ') + ').';
    // Thrown only, same reasoning as the no-data-rows case just above.
    throw new Error(msg);
  }

  var numRows = lastRow - 1;
  var names = sheet.getRange(2, nameCol, numRows, 1).getValues().map(function (r) { return String(r[0] || '').trim(); });
  var term1Remarks = sheet.getRange(2, term1Col, numRows, 1).getValues().map(function (r) { return String(r[0] || '').trim(); });
  var term2Remarks = sheet.getRange(2, term2Col, numRows, 1).getValues().map(function (r) { return String(r[0] || '').trim(); });

  var apiKey = PropertiesService.getUserProperties().getProperty(GEMINI_API_KEY_PROPERTY);

  var summary1 = reviewTermColumn_(sheet, names, term1Remarks, 'Term 1', CONFIG.OUTPUT_TERM1_HEADER, apiKey);
  var summary2 = reviewTermColumn_(sheet, names, term2Remarks, 'Term 2', CONFIG.OUTPUT_TERM2_HEADER, apiKey);

  var summaryText = formatRunSummaryText_(summary1, summary2, !!apiKey);
  SpreadsheetApp.getActiveSpreadsheet().toast(summaryText, 'Quillwarden review complete', 15);

  return { summary1: summary1, summary2: summary2, apiKey: !!apiKey, summaryText: summaryText };
}

function formatRunSummaryText_(summary1, summary2, hasKey) {
  function summaryLine(label, s) {
    var line = label + ' — reviewed: ' + s.reviewed + ', unchanged: ' + s.unchanged + ', no remark: ' + s.noRemark;
    if (s.llmError) {
      line += ', AI review FAILED for ' + s.llmAttempted + ' (' + s.llmError + ') — will retry next run';
    } else if (!hasKey && s.reviewed > 0) {
      line += ' (no Gemini key set — deterministic checks only)';
    } else if (s.llmAttempted > 0) {
      line += ', AI-reviewed: ' + s.llmAttempted;
      if (s.aiErrors > 0) line += ' (' + s.aiErrors + " couldn't be matched — will retry next run)";
    }
    return line;
  }
  return summaryLine('Term 1', summary1) + '\n' + summaryLine('Term 2', summary2);
}

// A real modal progress-bar dialog was tried here and pulled back out after
// three separate infrastructure blockers in a row: Apps Script's static
// scope detection can't see calls made from this dynamically-loaded file
// (fixed by moving the call into Code.gs); this project's manifest had a
// pinned oauthScopes list that silently never got the new scope it needed
// (fixed by editing the manifest); and even after both fixes, the dialog's
// google.script.run channel never actually reached the server at all on the
// test account (no error, just silence -- Apps Script's Executions log
// showed no runReviewNow_/getReviewProgress_ calls ever happening),
// consistent with a browser/network condition outside this code's control.
// Progress is shown via repeated toast() calls instead (see
// reviewTermColumn_ below) -- the exact same mechanism already used for the
// final "review complete" toast, so it's proven to work with zero extra
// permissions and no dialog/iframe involved at all.

// Reviews one term's remark column independently: figures out which rows
// actually need (re)reviewing this run, leaves everything else completely
// untouched, runs deterministic checks + one batched Gemini call for just
// the rows that need it, and writes results (plus a checksum/error note
// used to decide next run) back into that term's own output column.
//
// A row needs reviewing when: it was never reviewed before, its remark text
// changed since it was last reviewed (tracked via an MD5 checksum stored in
// the output cell's Notes -- invisible, no extra sheet columns needed), its
// last AI review didn't complete cleanly (aiError), or a Gemini key is set
// now but this row has never actually had an AI pass (llmRan false). Rows
// that meet none of those are left exactly as they were.
// Same batch size and inter-call gap reviewer.py (the web UI's Python
// backend) settled on: small enough to keep truncation risk low and give
// real incremental toast updates as each chunk finishes, spaced out enough
// to stay clear of Gemini's free-tier rolling rate limit.
var GEMINI_CHUNK_SIZE_ = 4;
var GEMINI_MIN_SECONDS_BETWEEN_CALLS_ = 5;
var _lastGeminiCallAt_ = null; // reset to null every time this file is freshly loaded -- see the file-level comment at the top

function paceGeminiCall_() {
  if (_lastGeminiCallAt_ !== null) {
    var elapsedMs = Date.now() - _lastGeminiCallAt_;
    var remainingMs = (GEMINI_MIN_SECONDS_BETWEEN_CALLS_ * 1000) - elapsedMs;
    if (remainingMs > 0) Utilities.sleep(remainingMs);
  }
}

function reviewTermColumn_(sheet, names, remarks, termLabel, outputHeader, apiKey) {
  var numRows = names.length;
  var lastCol = sheet.getLastColumn();
  var headerRow = sheet.getRange(1, 1, 1, lastCol).getValues()[0];
  var outputCol = findColumn_(headerRow, outputHeader);
  if (outputCol === -1) {
    outputCol = lastCol + 1;
    sheet.getRange(1, outputCol).setValue(outputHeader);
  }

  var outputRange = sheet.getRange(2, outputCol, numRows, 1);
  var existingNotes = outputRange.getNotes();

  var summary = { reviewed: 0, unchanged: 0, noRemark: 0, llmAttempted: 0, llmError: null, aiErrors: 0 };

  var toReview = [];
  var checksums = [];
  for (var i = 0; i < numRows; i++) {
    var remark = remarks[i];
    var stored = parseReviewNote_(existingNotes[i][0]);
    if (!remark) {
      checksums[i] = null;
      if (stored) {
        // The remark that used to be here was removed -- clear the stale
        // review instead of leaving it looking current.
        sheet.getRange(2 + i, outputCol).setValue('').setNote('');
      }
      summary.noRemark++;
      continue;
    }
    checksums[i] = checksum_(remark);
    var needsReview = !stored ||
      stored.checksum !== checksums[i] ||
      stored.aiError === true ||
      (!!apiKey && !stored.llmRan);
    if (needsReview) {
      toReview.push(i);
    } else {
      summary.unchanged++;
    }
  }

  if (!toReview.length) return summary;

  var detIssuesByIndex = {};
  toReview.forEach(function (i) {
    detIssuesByIndex[i] = runChecks_(remarks[i]);
  });

  // Free (no API call) and scoped to this term only, so a student's own
  // Term 1 remark is never compared against their own Term 2 remark.
  var crossIssues = checkH2Across_(remarks, names);

  // Real-time progress for this term, shown via a toast that gets replaced
  // as each chunk finishes -- the same toast() mechanism already used for
  // the final "review complete" summary below, so it needs no extra
  // permissions and doesn't depend on any dialog/client-server channel.
  if (apiKey) {
    SpreadsheetApp.getActiveSpreadsheet().toast(
      'Reviewing ' + termLabel + ': 0 of ' + toReview.length + ' checked...', 'Quillwarden', 10
    );
  }
  var completedSoFar_ = 0;

  var llmResultsByIndex = {};
  if (apiKey) {
    for (var chunkStart_ = 0; chunkStart_ < toReview.length; chunkStart_ += GEMINI_CHUNK_SIZE_) {
      var chunkIndices_ = toReview.slice(chunkStart_, chunkStart_ + GEMINI_CHUNK_SIZE_);
      var entries = chunkIndices_.map(function (i) {
        return { index: i, name: names[i] || ('Row ' + (i + 2)), remark: remarks[i] };
      });
      summary.llmAttempted += entries.length;
      paceGeminiCall_();
      try {
        var prompt = buildLLMPrompt_(entries, LLM_SKIP_RULE_IDS);
        _lastGeminiCallAt_ = Date.now();
        var responseText = callGemini_(prompt, apiKey);
        var results = parseLLMResponse_(responseText);
        var resultsByName = {};
        results.forEach(function (res) { if (res && res.student_name) resultsByName[res.student_name] = res; });
        // Positional fallback, same as reviewer.py: if the reply has exactly
        // as many entries as were sent but a name doesn't match exactly, trust
        // the order rather than treating a cosmetic mismatch as a failure.
        var positionalOk = results.length === entries.length;
        entries.forEach(function (entry, pos) {
          var res = resultsByName[entry.name];
          if (!res && positionalOk && results[pos] && typeof results[pos] === 'object') res = results[pos];
          llmResultsByIndex[entry.index] = res ? { issues: res.issues || [], matched: true } : { issues: [], matched: false };
        });
      } catch (e) {
        var message = e && e.message ? e.message : String(e);
        summary.llmError = message;
        console.error('Quillwarden LLM review failed for ' + termLabel + ' (chunk starting at ' + chunkStart_ + '): ' + message);
        entries.forEach(function (entry) {
          llmResultsByIndex[entry.index] = { issues: [], matched: false };
        });
      }
      completedSoFar_ += entries.length;
      SpreadsheetApp.getActiveSpreadsheet().toast(
        'Reviewing ' + termLabel + ': ' + completedSoFar_ + ' of ' + toReview.length + ' checked...', 'Quillwarden', 10
      );
    }
  } else if (toReview.length) {
    // No key -- deterministic-only, no Gemini calls, so this finishes almost
    // instantly. Not worth a toast of its own; the final summary toast below
    // covers it.
    completedSoFar_ = toReview.length;
  }

  toReview.forEach(function (i) {
    var detIssues = detIssuesByIndex[i].concat(crossIssues[i]);
    var llmResult = llmResultsByIndex[i];
    var llmIssues, aiError;
    if (!apiKey) {
      llmIssues = [];
      aiError = false;
    } else if (llmResult && llmResult.matched) {
      llmIssues = llmResult.issues;
      aiError = false;
    } else {
      aiError = true;
      summary.aiErrors++;
      llmIssues = [issue_('AI_ERROR', 'warning', '',
        'AI review could not be completed for this remark this time' + (summary.llmError ? ': ' + summary.llmError : '.'),
        'Run "Review Remarks" again to retry AI review for this student.')];
    }
    var allIssues = mergeIssuesByRuleId_(detIssues, llmIssues);
    var statusLabel = statusLabelFromIssues_(allIssues);
    var cell = sheet.getRange(2 + i, outputCol);
    cell.setValue(formatCell_(statusLabel, termLabel, remarks[i].length, allIssues));
    cell.setNote(buildReviewNote_(checksums[i], aiError, !!apiKey));
    summary.reviewed++;
  });

  return summary;
}

// MD5 hex digest of a remark's text, used to detect whether it changed since
// its last review (see reviewTermColumn_). Cheap and built into Apps
// Script's Utilities service -- no external library needed. (Google's own
// docs note computeDigest returns signed bytes; the +256 below converts
// each back to its unsigned value before formatting as hex.)
function checksum_(text) {
  var bytes = Utilities.computeDigest(Utilities.DigestAlgorithm.MD5, text, Utilities.Charset.UTF_8);
  return bytes.map(function (b) {
    var v = b < 0 ? b + 256 : b;
    var hex = v.toString(16);
    return hex.length === 1 ? '0' + hex : hex;
  }).join('');
}

function parseReviewNote_(noteText) {
  if (!noteText) return null;
  try {
    var parsed = JSON.parse(noteText);
    if (parsed && typeof parsed.checksum === 'string') return parsed;
  } catch (e) {
    // Not a note Quillwarden wrote (or it's corrupted) -- treat as unreviewed.
  }
  return null;
}

function buildReviewNote_(checksum, aiError, llmRan) {
  return JSON.stringify({ checksum: checksum, aiError: !!aiError, llmRan: !!llmRan });
}

function mergeIssuesByRuleId_(detIssues, llmIssues) {
  var seen = {};
  var order = [];
  detIssues.forEach(function (issue) {
    if (!seen[issue.rule_id]) order.push(issue.rule_id);
    seen[issue.rule_id] = issue;
  });
  llmIssues.forEach(function (issue) {
    if (!seen[issue.rule_id]) {
      seen[issue.rule_id] = issue;
      order.push(issue.rule_id);
    }
  });
  return order.map(function (ruleId) { return seen[ruleId]; });
}

function findColumn_(headerRow, headerName) {
  var target = normalizeHeader_(headerName);
  for (var i = 0; i < headerRow.length; i++) {
    if (normalizeHeader_(headerRow[i]) === target) return i + 1; // 1-based column
  }
  return -1;
}

function normalizeHeader_(s) {
  return String(s || '').trim().toLowerCase().replace(/\s+/g, ' ');
}

// ── Status / formatting ──────────────────────────────────────────────────────

var SEVERITY_RANK = { critical: 3, required: 2, warning: 1 };

function statusLabelFromIssues_(issues) {
  if (!issues.length) return 'NO MAJOR ISSUES';
  var rank = 0;
  issues.forEach(function (issue) {
    rank = Math.max(rank, SEVERITY_RANK[issue.severity] || 0);
  });
  if (rank >= 3) return 'CRITICAL ISSUE';
  if (rank >= 2) return 'NEEDS REVISION';
  return 'MINOR EDITS';
}

function formatCell_(statusLabel, term, charCount, issues) {
  var lines = [statusLabel + ' — reviewed: ' + term + ' — ' + charCount + ' characters'];
  issues.forEach(function (issue) {
    lines.push(
      '• [' + issue.rule_id + ' — ' + issue.severity + '] ' + issue.explanation +
      ' ' + issue.teacher_action
    );
  });
  return lines.join('\n');
}

// ── Rule dispatch ────────────────────────────────────────────────────────────

function runChecks_(remark) {
  var issues = [];
  issues = issues.concat(checkI1_(remark));
  issues = issues.concat(checkI2_(remark));
  issues = issues.concat(checkI3_(remark));
  issues = issues.concat(checkJ1_(remark));
  issues = issues.concat(checkJ2_(remark));
  issues = issues.concat(checkJ3_(remark));
  issues = issues.concat(checkJ5_(remark));
  issues = issues.concat(checkK1_(remark, CONFIG.MAX_CHARACTERS));
  issues = issues.concat(checkH2Within_(remark));
  return issues;
}

function issue_(ruleId, severity, exactPhrase, explanation, teacherAction) {
  return {
    rule_id: ruleId,
    severity: severity,
    exact_phrase: exactPhrase,
    explanation: explanation,
    teacher_action: teacherAction,
    requires_record_verification: false
  };
}

function escapeRegExp_(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// ── I1: Grammar ──────────────────────────────────────────────────────────────

var RUN_ON_WORD_THRESHOLD = 60;

function checkI1_(remark) {
  var issues = [];

  var repeated = remark.match(/\b(\w+)[ \t]+\1\b/i);
  if (repeated) {
    issues.push(issue_('I1', 'required', repeated[0],
      'Repeated word: "' + repeated[0] + '".',
      'Remove the duplicate word.'));
  }

  var stripped = remark.replace(/^\s+/, '');
  if (stripped && !/^[A-Z]/.test(stripped)) {
    var preview = stripped.slice(0, 30).replace(/\n/g, ' ');
    issues.push(issue_('I1', 'required', preview,
      'Remark does not begin with a capital letter.',
      'Capitalise the first word of the remark.'));
  }

  var sentences = remark.trim().split(/(?<=[.!?])\s+/);
  sentences.forEach(function (sentence) {
    var wordCount = sentence.split(/\s+/).filter(function (w) { return w.length; }).length;
    if (wordCount > RUN_ON_WORD_THRESHOLD) {
      var words = sentence.split(/\s+/).filter(function (w) { return w.length; });
      var prev = words.slice(0, 8).join(' ') + '…';
      issues.push(issue_('I1', 'required', prev,
        'Sentence is ' + wordCount + ' words long; consider splitting it.',
        'Break this into two shorter sentences for clarity.'));
    }
  });

  return issues;
}

// ── I2: Spelling ─────────────────────────────────────────────────────────────

var MISSPELLINGS = {
  acheive: 'achieve',
  acheived: 'achieved',
  acheiving: 'achieving',
  acheivements: 'achievements',
  achievments: 'achievements',
  accomodate: 'accommodate',
  accomadation: 'accommodation',
  recieve: 'receive',
  recieved: 'received',
  beleive: 'believe',
  beleived: 'believed',
  occured: 'occurred',
  occurance: 'occurrence',
  seperate: 'separate',
  proffesional: 'professional',
  managment: 'management',
  enviroment: 'environment',
  noticable: 'noticeable',
  sportmanship: 'sportsmanship',
  independant: 'independent',
  freindship: 'friendship',
  oppertunity: 'opportunity',
  oppurtunity: 'opportunity',
  organsational: 'organisational',
  particpated: 'participated',
  particpate: 'participate',
  demeanur: 'demeanour',
  constructave: 'constructive',
  consistant: 'consistent',
  persistant: 'persistent',
  enthuisiasm: 'enthusiasm',
  enthusiasim: 'enthusiasm',
  responsibilty: 'responsibility',
  partcipation: 'participation',
  comunicates: 'communicates',
  comunication: 'communication',
  assesment: 'assessment',
  assesments: 'assessments',
  neccessary: 'necessary',
  occassion: 'occasion',
  recomend: 'recommend',
  recomended: 'recommended'
};

function checkI2_(remark) {
  var issues = [];
  Object.keys(MISSPELLINGS).forEach(function (wrong) {
    var correct = MISSPELLINGS[wrong];
    var pattern = new RegExp('\\b' + escapeRegExp_(wrong) + '\\b', 'i');
    var m = remark.match(pattern);
    if (m) {
      var exact = m[0];
      issues.push(issue_('I2', 'required', exact,
        '"' + exact + '" appears to be misspelled; correct form is "' + correct + '".',
        'Replace "' + exact + '" with "' + correct + '".'));
    }
  });
  return issues;
}

// ── I3: Punctuation and spacing ──────────────────────────────────────────────

function checkI3_(remark) {
  var issues = [];

  if (/[ \t]{2,}/.test(remark)) {
    issues.push(issue_('I3', 'required', '[double space]',
      'Remark contains consecutive spaces.',
      'Remove the extra spaces.'));
  }

  var re = /[.!?](?=[A-Z])/g;
  var m;
  while ((m = re.exec(remark)) !== null) {
    var pos = m.index;
    var before = remark.slice(0, pos);
    var wordMatch = before.match(/([A-Za-z]+)$/);
    if (wordMatch && wordMatch[1].length <= 3) {
      continue;
    }
    issues.push(issue_('I3', 'required', remark.slice(Math.max(0, pos - 2), pos + 3),
      'Missing space after punctuation at "' + m[0] + '".',
      'Add a space after the punctuation mark.'));
    break;
  }

  var stripped = remark.replace(/\s+$/, '');
  if (stripped && '.!?'.indexOf(stripped.slice(-1)) === -1) {
    var tail = stripped.length > 20 ? stripped.slice(-20) : stripped;
    issues.push(issue_('I3', 'required', tail,
      'Remark does not end with terminal punctuation.',
      'Add a full stop at the end of the remark.'));
  }

  var multi = remark.match(/[!?]{2,}/);
  if (multi) {
    issues.push(issue_('I3', 'required', multi[0],
      'Multiple punctuation marks: "' + multi[0] + '".',
      'Use a single punctuation mark.'));
  }

  var spaceBefore = remark.match(/[ \t][,;:.!?]/);
  if (spaceBefore) {
    issues.push(issue_('I3', 'required', spaceBefore[0],
      'Space before punctuation: "' + spaceBefore[0].trim() + '".',
      'Remove the space before the punctuation mark.'));
  }

  return issues;
}

// ── J1: Programme names ──────────────────────────────────────────────────────

var J1_CANONICAL_FORMS = [
  { display: '"Bully to Buddy"', requiresQuotationMarks: true },
  { display: 'LEAD Collective', requiresQuotationMarks: false },
  { display: 'Eco Club', requiresQuotationMarks: false },
  { display: 'Student Council', requiresQuotationMarks: false }
];

function checkJ1_(remark) {
  var issues = [];
  J1_CANONICAL_FORMS.forEach(function (form) {
    var name = form.display.replace(/^"|"$/g, '');
    var presence = new RegExp(escapeRegExp_(name), 'i');
    if (!presence.test(remark)) return;

    if (form.requiresQuotationMarks) {
      if (remark.indexOf('"' + name + '"') === -1) {
        var m = remark.match(presence);
        issues.push(issue_('J1', 'required', m[0],
          '"' + name + '" must be enclosed in double quotation marks.',
          'Write as "' + name + '".'));
      }
    } else {
      if (remark.indexOf(name) === -1) {
        var m2 = remark.match(presence);
        issues.push(issue_('J1', 'required', m2[0],
          'Incorrect form of programme name; canonical form is "' + name + '".',
          'Write as "' + name + '".'));
      }
    }
  });
  return issues;
}

// ── J2: Khoj ──────────────────────────────────────────────────────────────────

function checkJ2_(remark) {
  var issues = [];
  var canonical = 'Khoj';
  var re = /\bkhoj\b/gi;
  var m;
  while ((m = re.exec(remark)) !== null) {
    if (m[0] !== canonical) {
      issues.push(issue_('J2', 'required', m[0],
        '"' + m[0] + '" should be "' + canonical + '".',
        'Correct to the canonical form "' + canonical + '".'));
    }
  }
  return issues;
}

// ── J3: Subjects ─────────────────────────────────────────────────────────────

var J3_SUBJECTS = [
  'Mathematics', 'English', 'Hindi', 'Sanskrit', 'Science', 'Social Science',
  'Expedition', 'Module', 'Digital Literacy', 'Physical Education',
  'Visual Arts', 'Performing Arts'
];

function checkJ3_(remark) {
  var issues = [];
  J3_SUBJECTS.forEach(function (subject) {
    var lowerSubject = subject.toLowerCase();
    var lowerPattern = new RegExp('\\b' + escapeRegExp_(lowerSubject) + '\\b');
    var canonicalPattern = new RegExp('\\b' + escapeRegExp_(subject) + '\\b');
    if (lowerPattern.test(remark) && !canonicalPattern.test(remark)) {
      var m = remark.match(lowerPattern);
      issues.push(issue_('J3', 'warning', m[0],
        'Subject name "' + m[0] + '" may need to be capitalised as "' + subject + '".',
        "Apply the school's configured subject-capitalisation style consistently."));
    }
  });
  return issues;
}

// ── J5: Events and roles ─────────────────────────────────────────────────────

var J5_EVENTS = [
  'Sports Day', 'Class Assembly', 'Cyber Safety Ambassador',
  'Transition In-Charge', 'Event Management Team'
];

function checkJ5_(remark) {
  var issues = [];
  J5_EVENTS.forEach(function (form) {
    var lowerForm = form.toLowerCase();
    var lowerPattern = new RegExp('\\b' + escapeRegExp_(lowerForm) + '\\b', 'i');
    if (lowerPattern.test(remark) && remark.indexOf(form) === -1) {
      var m = remark.match(lowerPattern);
      issues.push(issue_('J5', 'warning', m[0],
        '"' + m[0] + '" should use the canonical capitalisation "' + form + '".',
        'Write as "' + form + '".'));
    }
  });
  return issues;
}

// ── K1: Character limit ──────────────────────────────────────────────────────

function checkK1_(remark, maxChars) {
  var count = remark.length;
  if (count > maxChars) {
    return [issue_('K1', 'required', '[' + count + ' characters]',
      'Remark is ' + count + ' characters; limit is ' + maxChars + ' (including spaces and line breaks).',
      'Shorten the remark to fit within the character limit.')];
  }
  return [];
}

// ── H2: Duplicate content ────────────────────────────────────────────────────

function normalizeForDuplicateCheck_(text) {
  return text.trim().toLowerCase().replace(/\s+/g, ' ');
}

function splitSentences_(remark) {
  return remark.trim().split(/(?<=[.!?])\s+/)
    .map(function (s) { return s.trim(); })
    .filter(function (s) { return s.length > 10; });
}

function checkH2Within_(remark) {
  var sentences = splitSentences_(remark);
  var seen = {};
  var issues = [];
  sentences.forEach(function (sentence) {
    var key = normalizeForDuplicateCheck_(sentence);
    if (seen[key]) {
      var preview = sentence.length > 80 ? sentence.slice(0, 80) + '…' : sentence;
      issues.push(issue_('H2', 'required', preview,
        'This sentence (or a near-identical one) appears more than once in the remark.',
        'Remove the duplicated content.'));
    } else {
      seen[key] = true;
    }
  });
  return issues;
}

function checkH2Across_(remarks, studentNames) {
  var result = remarks.map(function () { return []; });
  var normalized = remarks.map(function (r) { return normalizeForDuplicateCheck_(r || ''); });

  for (var i = 0; i < remarks.length; i++) {
    if (!normalized[i]) continue;
    for (var j = i + 1; j < remarks.length; j++) {
      if (normalized[i] === normalized[j]) {
        var otherI = studentNames[j] || ('student ' + (j + 1));
        var otherJ = studentNames[i] || ('student ' + (i + 1));
        var preview = remarks[i].length > 60 ? remarks[i].slice(0, 60) + '…' : remarks[i];
        result[i].push(issue_('H2', 'required', preview,
          'Remark is identical to the one submitted for ' + otherI + '.',
          "Verify this remark was not copied from another student's record."));
        result[j].push(issue_('H2', 'required', preview,
          'Remark is identical to the one submitted for ' + otherJ + '.',
          "Verify this remark was not copied from another student's record."));
      }
    }
  }

  return result;
}

// ── LLM review (judgement-based rules) ───────────────────────────────────────
//
// Mirrors llm-reviewer/reviewer.py. The actual per-term batched Gemini call
// (deciding which rows to send, matching results back to the right student,
// and marking any that couldn't be matched as AI_ERROR) lives in
// reviewTermColumn_ above, since which rows need reviewing is itself decided
// per term. buildLLMPrompt_ / parseLLMResponse_ / callGemini_ below are the
// shared, stateless pieces of that.

function buildLLMPrompt_(entries, skipRuleIds) {
  var trimmedCategories = RUBRIC.categories.filter(function (c) {
    return skipRuleIds.indexOf(c.id) === -1;
  });
  var trimmedRubric = Object.assign({}, RUBRIC, { categories: trimmedCategories });

  var skipNote = '';
  if (skipRuleIds.length) {
    skipNote = '\n\nNote: the following rules have already been verified by a deterministic ' +
      'validator (or are intentionally not checked at all) and must be omitted from your output: ' +
      skipRuleIds.slice().sort().join(', ') + '. Review only the remaining rules.';
  }

  var formattedRows = entries.map(function (entry, i) {
    return (i + 1) + '. Student: ' + entry.name + '\n   Remark: ' + entry.remark;
  }).join('\n\n');

  return 'Rubric:\n```json\n' + JSON.stringify(trimmedRubric, null, 2) + '\n```' +
    skipNote + '\n\n' +
    'Student remarks to review:\n' + formattedRows + '\n\n' +
    'Return a JSON array — one object per student — exactly matching ' +
    'the review_output_schema in the rubric.';
}

function parseLLMResponse_(text) {
  if (!text || !text.trim()) return [];
  try {
    var result = JSON.parse(text);
    if (Array.isArray(result)) return result;
  } catch (e) {
    // fall through to bracket extraction
  }
  var m = text.match(/\[[\s\S]*\]/);
  if (m) {
    try {
      var result2 = JSON.parse(m[0]);
      if (Array.isArray(result2)) return result2;
    } catch (e2) {
      // give up below
    }
  }
  return [];
}

var HARD_QUOTA_RETRY_CEILING_SECONDS_ = 120;

function extractRetryDelaySeconds_(bodyText) {
  var data;
  try { data = JSON.parse(bodyText); } catch (e) { return null; }
  var err = (data && data.error) || {};
  var details = err.details || [];
  for (var i = 0; i < details.length; i++) {
    var raw = details[i].retryDelay;
    if (raw && /s$/.test(raw)) {
      var n = parseFloat(raw);
      if (!isNaN(n)) return n;
    }
  }
  var m = /retry in ([\d.]+)\s*s/i.exec(err.message || '');
  if (m) {
    var n2 = parseFloat(m[1]);
    if (!isNaN(n2)) return n2;
  }
  return null;
}

function callGemini_(prompt, apiKey) {
  var url = 'https://generativelanguage.googleapis.com/v1beta/models/' +
    GEMINI_MODEL + ':generateContent?key=' + encodeURIComponent(apiKey);

  var payload = {
    system_instruction: { parts: [{ text: SYSTEM_PROMPT }] },
    contents: [{ role: 'user', parts: [{ text: prompt }] }],
    generationConfig: { response_mime_type: 'application/json' }
  };

  var options = {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  };

  var maxAttempts = 5;
  for (var attempt = 0; attempt < maxAttempts; attempt++) {
    var response = UrlFetchApp.fetch(url, options);
    var code = response.getResponseCode();

    if (code === 200) {
      var data = JSON.parse(response.getContentText());
      if (!data.candidates || !data.candidates.length) {
        throw new Error('Gemini returned no candidates (response may have been blocked).');
      }
      return data.candidates[0].content.parts[0].text;
    }

    if (code === 429) {
      var bodyText429 = response.getContentText();
      var retryDelay = extractRetryDelaySeconds_(bodyText429);
      var isHardWall = retryDelay === null || retryDelay > HARD_QUOTA_RETRY_CEILING_SECONDS_;
      if (!isHardWall && attempt < maxAttempts - 1) {
        Utilities.sleep((retryDelay + 1) * 1000);
        continue;
      }
      console.error('Gemini API error 429 — full body: ' + bodyText429);
      throw new Error('Gemini API error 429: ' + bodyText429.slice(0, 500));
    }

    if (code >= 500 && code < 600) {
      if (attempt < maxAttempts - 1) {
        Utilities.sleep(3000 * Math.pow(2, attempt));
        continue;
      }
    }

    var bodyText = response.getContentText();
    console.error('Gemini API error ' + code + ' — full body: ' + bodyText);
    throw new Error('Gemini API error ' + code + ': ' + bodyText.slice(0, 500));
  }

  throw new Error('Gemini API: exhausted retries.');
}

// ── Embedded rubric (rubric/quillwarden_rubric.json, v1.2) ──────────────────
// Kept in sync by hand with the Python project's rubric file, EXCEPT the
// categories whose IDs are in LLM_SKIP_RULE_IDS (I1-I3, J1, J2, J4, J5, K1,
// H2) are omitted here rather than embedded and filtered at runtime — they're
// either already handled deterministically above, or (J4) not checked at
// all. If you change LLM_SKIP_RULE_IDS to send one of those rules to the LLM
// after all, you must also add its category definition back into this list,
// copying it from rubric/quillwarden_rubric.json.

var RUBRIC = {
  "rubric_name": "Quillwarden Rubric",
  "version": "1.2",
  "purpose": "Review student report remarks for accuracy, professionalism, clarity, balance, safeguarding, consistency and usefulness. The reviewer identifies issues and reports them; it does not rewrite the remark. The teacher remains solely responsible for revising and approving the final remark.",

  "settings": {
    "allow_strengths_only": false,
    "require_growth_point": true,
    "require_closing": true,
    "max_characters_including_spaces_and_line_breaks": 1000,
    "line_breaks_count_as_characters": true,
    "preferred_language_variant": "British Indian English",
    "provide_rewritten_remark": false,
    "teacher_makes_final_revision": true,
    "pronouns_must_be_verified_against_roster": true,
    "subject_capitalisation_style": "configurable_by_school",
    "sport_capitalisation_style": "configurable_by_school",
    "screen_time_reporting_authorised": false,
    "e3_directive_repeat_threshold": 3
  },

  "constraints": {
    "reviewer_must_not_rewrite_remark": true,
    "reviewer_may_suggest_phrasing_direction_only_if_asked": true
  },

  "review_output_schema": {
    "student_name": "string",
    "status": [
      "no_major_issues",
      "minor_edits",
      "needs_revision",
      "critical_issue"
    ],
    "issues": [
      {
        "rule_id": "string",
        "severity": "critical|required|warning",
        "exact_phrase": "string",
        "explanation": "string",
        "teacher_action": "string",
        "requires_record_verification": "boolean"
      }
    ],
    "character_count": "integer",
    "teacher_revision_required": "boolean"
  },

  "severity_levels": {
    "critical": {
      "description": "Must not be submitted until corrected.",
      "triggers": [
        "wrong student name",
        "pronoun that contradicts verified roster data",
        "confidential or sensitive information disclosed",
        "diagnosis or medical disclosure",
        "humiliating language",
        "duplicate remark assigned to another student",
        "serious factual inconsistency"
      ]
    },
    "required": {
      "description": "Teacher must review and correct.",
      "triggers": [
        "grammar or spelling affecting professionalism",
        "unsupported academic claims",
        "no actionable development point",
        "contradiction",
        "inaccurate programme or role name",
        "excessive length",
        "repeated paragraphs",
        "harsh or inappropriate wording"
      ]
    },
    "warning": {
      "description": "Teacher should review and decide whether revision is needed.",
      "triggers": [
        "inflated language",
        "generic praise",
        "mild repetition",
        "slightly vague advice",
        "excessive wordiness",
        "inconsistent but non-critical style"
      ]
    },
    "none": {
      "description": "No major issue.",
      "criteria": [
        "accurate and internally consistent",
        "includes a specific strength",
        "includes a useful development point where required",
        "professional language",
        "avoids sensitive or exaggerated claims",
        "grammatically correct",
        "follows school naming conventions",
        "within length limit"
      ]
    }
  },

  "categories": [
    {
      "id": "A1",
      "section": "Required Content",
      "title": "Clear strength",
      "severity": "required",
      "policy": "Include at least one specific, observable strength, supported by an example where appropriate. An example is not strictly required if the strength itself is already clearly observable.",
      "detection": {
        "type": "semantic",
        "notes": "Detect whether the remark names an observable trait/skill/behaviour rather than only a generic evaluative label."
      },
      "exceptions": ["Strength is already specific and observable without a supporting example"],
      "examples": {
        "pass": "She asks thoughtful questions and applies feedback carefully to her written work.",
        "flag": "She is a wonderful student.",
        "flag_reason": "Generic praise, no observable strength identified."
      },
      "teacher_message": "Add a specific, observable strength; include a brief example if the strength isn't already self-evident."
    },
    {
      "id": "A2",
      "section": "Required Content",
      "title": "Evidence or example",
      "severity": "required",
      "policy": "Support important claims with a specific observation or example (participation, work habits, revision, written responses, projects, presentations, assemblies, Sports Day, Khoj, class responsibilities, clubs, performances, group work, changes over term).",
      "detection": {"type": "semantic", "notes": "Flag broad evaluative claims with no supporting observation."},
      "exceptions": [],
      "examples": {
        "pass": "His growing confidence was evident when he presented his ideas during the class assembly.",
        "flag": "He has developed exceptional leadership qualities.",
        "flag_reason": "Broad and unsupported claim."
      },
      "teacher_message": "Add a concrete example or observation to support this claim."
    },
    {
      "id": "A3",
      "section": "Required Content",
      "title": "Progress over time",
      "severity": "warning",
      "policy": "Where relevant, identify a genuine change observed during the term or year.",
      "detection": {"type": "semantic", "notes": "Flag sweeping improvement claims covering 'every area' with no specific dimension named."},
      "exceptions": [],
      "examples": {
        "pass": "She now participates more frequently in whole-class discussions.",
        "flag": "She has improved tremendously in every area.",
        "flag_reason": "Overly broad and unsupported."
      },
      "teacher_message": "Name the specific area of change rather than an overall improvement."
    },
    {
      "id": "A4",
      "section": "Required Content",
      "title": "Actionable area for development",
      "severity": "required",
      "policy": "Include at least one specific and actionable growth point stating what the student should practise or improve (revision, proofreading, careful reading, sustained attention, timely completion, handwriting, organising resources, participation, questioning, detail, spelling/written expression, following instructions, collaborative listening). The fuller issue/action/benefit structure (see E1) is preferred but not required for this check.",
      "detection": {"type": "semantic", "notes": "Flag advice with no concrete, nameable action."},
      "exceptions": ["settings.require_growth_point is false"],
      "examples": {
        "pass": "Reviewing her work before submission will help her identify spelling and punctuation errors.",
        "flag": "She needs to work harder.",
        "flag_reason": "Vague, no specified action."
      },
      "teacher_message": "State what the student should specifically practise or improve."
    },
    {
      "id": "A5",
      "section": "Required Content",
      "title": "Balanced content",
      "severity": "required",
      "policy": "Present a fair picture: not all praise or all criticism, no overstatement, no repetition of the same point.",
      "detection": {"type": "structural_and_semantic", "notes": "Check ratio and repetition of praise vs. growth content."},
      "exceptions": ["settings.allow_strengths_only is true, in which case a strengths-only remark is not flagged"],
      "examples": {},
      "teacher_message": "Balance the remark with both a strength and a growth point, unless strengths-only remarks are permitted for this report."
    },
    {
      "id": "B1",
      "section": "Accuracy",
      "title": "Correct student identity",
      "severity": "critical",
      "policy": "The student's name must be correct and used consistently throughout; no other student's name may appear; check for duplicate students/remarks.",
      "detection": {"type": "exact_match", "notes": "Compare all name mentions against the roster record for this remark."},
      "exceptions": [],
      "examples": {"flag": "Aaditya ... We look forward to seeing Aarav continue to progress."},
      "teacher_message": "Correct the student name error before this remark can be submitted."
    },
    {
      "id": "B2",
      "section": "Accuracy",
      "title": "Pronoun consistency",
      "severity_conditions": [
        {"condition": "pronoun contradicts verified roster data", "severity": "critical"},
        {"condition": "pronoun is inconsistent within the remark itself (e.g. switches between he/she for the same student)", "severity": "critical"}
      ],
      "policy": "Pronouns must be internally consistent. If roster data is present, they must also match it. Do not infer gender from the student's name. If no roster data is provided, do not flag pronoun correctness as unverifiable — only flag actual internal inconsistency.",
      "detection": {"type": "consistency_check_and_roster_lookup", "notes": "Do not use name-based gender inference as a source of truth. Do not raise an issue solely because roster data is absent."},
      "exceptions": [],
      "examples": {"flag": "Aaditya ... We look forward to seeing her continue to grow."},
      "teacher_message": "Confirm the correct pronoun against the class roster before submitting."
    },
    {
      "id": "B3",
      "section": "Accuracy",
      "title": "Academic claims",
      "severity": "required",
      "policy": "Claims such as 'grades have improved', 'performance has declined', 'performs well across all subjects', 'exceeds grade-level expectations', 'consistently produces excellent work', 'demonstrates strong achievement in [subject]', 'has shown significant academic growth' must be supported by grades, work samples or teacher records. This includes general/unsupported academic claims, not only improved/declined statements.",
      "detection": {"type": "semantic_and_phrase_match", "terms": ["grades have improved", "performance has declined", "performs well across all subjects", "exceeds grade-level expectations", "consistently produces excellent work", "demonstrates strong achievement", "has shown significant academic growth"]},
      "exceptions": ["Claim is directly supported by cited grades/work samples/records"],
      "examples": {},
      "teacher_message": "Verify against academic records, or soften the claim if unverifiable.",
      "requires_record_verification": true
    },
    {
      "id": "B4",
      "section": "Accuracy",
      "title": "Activities, awards and responsibilities",
      "severity": "required",
      "policy": "Verify claims concerning medals, trophies, competitions, teams, clubs, Student Council, Eco Club, LEAD Collective, 'Bully to Buddy', class responsibilities, performances, leadership roles, published work, external achievements. Do not assume correctness merely because the claim appears in the remark.",
      "detection": {"type": "record_verification", "notes": "Cross-check named activities/awards/roles against school records."},
      "exceptions": [],
      "examples": {},
      "teacher_message": "Confirm this activity/award/role against school records before submitting.",
      "requires_record_verification": true
    },
    {
      "id": "B5",
      "section": "Accuracy",
      "title": "Attendance and missed work",
      "severity_conditions": [
        {"condition": "private/medical/family reasons for absence are disclosed", "severity": "critical"},
        {"condition": "attendance is described in unnecessary detail but no private reason is disclosed", "severity": "required"},
        {"condition": "attendance is mentioned only insofar as it affects learning continuity", "severity": "none"}
      ],
      "policy": "Attendance may be mentioned where it directly affects learning continuity. Never state private reasons for absence unless authorised.",
      "detection": {"type": "semantic", "notes": "Distinguish a general continuity statement from disclosure of a private/medical/family reason."},
      "exceptions": ["Disclosure explicitly authorised by the school for this report format"],
      "examples": {
        "pass": "More regular attendance will support continuity in her learning.",
        "flag": "Her repeated absence due to family and medical issues has affected her work.",
        "flag_reason": "Contains private personal information."
      },
      "teacher_message": "Remove the private/medical/family reason; keep only the learning-continuity statement if needed."
    },
    {
      "id": "C0",
      "section": "Professional Tone",
      "title": "Observable behaviour, not personality judgement",
      "severity": "required",
      "policy": "The remark should describe observable school behaviour and learning, not fixed personality traits or character judgements.",
      "detection": {"type": "semantic", "notes": "Flag trait labels ('is lazy', 'is shy') in place of behaviour descriptions."},
      "exceptions": [],
      "examples": {
        "pass": "He submits work on time and responds well to feedback.",
        "flag": "He is lazy and a bit stubborn.",
        "flag_reason": "Labels the student's character rather than describing observable behaviour."
      },
      "teacher_message": "Rephrase as an observable behaviour rather than a character label."
    },
    {
      "id": "C1",
      "section": "Professional Tone",
      "title": "Objective and school-appropriate language",
      "severity": "warning",
      "policy": "Use language based on observable behaviour and learning.",
      "detection": {
        "type": "phrase_match",
        "preferred_terms": ["participates actively", "submits work on time", "seeks clarification", "contributes ideas", "responds to feedback", "collaborates with peers", "benefits from reminders", "is developing greater consistency"],
        "avoid_terms": ["naughty", "cute", "sweet", "beloved", "mischievous", "lazy", "stubborn", "difficult child", "attention-seeking", "spoilt", "obedient"]
      },
      "exceptions": [],
      "examples": {},
      "teacher_message": "Replace with an observable-behaviour phrase from the preferred list."
    },
    {
      "id": "C2",
      "section": "Professional Tone",
      "title": "Avoid casual or conversational phrasing",
      "severity": "warning",
      "policy": "Avoid casual, conversational phrasing in a formal report remark.",
      "detection": {"type": "phrase_match", "terms": ["go for it", "keep shining", "tucked into tasks", "brings lively vibes", "in his own world", "gets charged up", "a joy to teach", "heart of the class", "all the right ingredients", "unlock brilliance"]},
      "exceptions": [],
      "examples": {},
      "teacher_message": "Replace casual phrasing with a more formal equivalent."
    },
    {
      "id": "C3",
      "section": "Professional Tone",
      "title": "Avoid overly sentimental language",
      "severity": "warning",
      "policy": "Avoid language that sounds personal, emotional or promotional rather than professional. A restrained closing is preferred.",
      "detection": {"type": "phrase_match", "terms": ["makes everyone proud", "she will always be remembered", "everyone loves him", "we adore her", "she lights up every room", "he is destined for greatness"]},
      "exceptions": [],
      "examples": {},
      "teacher_message": "Replace with a restrained, professional statement."
    },
    {
      "id": "C4",
      "section": "Professional Tone",
      "title": "Avoid authority-heavy language",
      "severity": "warning",
      "policy": "Avoid framing development primarily in terms of obedience to authority.",
      "detection": {
        "type": "phrase_match",
        "avoid_terms": ["respectful towards authority", "must obey teachers", "should accept authority", "dialogue with authority"],
        "preferred_terms": ["interacts respectfully with teachers and peers", "follows classroom expectations", "responds constructively to guidance", "follows agreed routines"]
      },
      "exceptions": [],
      "examples": {},
      "teacher_message": "Reframe using collaborative, non-authority-based phrasing."
    },
    {
      "id": "D1",
      "section": "Inflated or Unsupported Language",
      "title": "Excessive praise",
      "severity": "warning",
      "policy": "Avoid unsupported inflated praise.",
      "detection": {"type": "semantic_and_phrase_match", "terms": ["exceptional", "outstanding", "remarkable", "brilliant", "perfect", "exemplary", "extraordinary", "academic excellence", "true excellence", "immense talent", "tremendous growth", "greater heights", "ideal learner", "future thought leader", "destined for success", "excels in every area", "surpasses others"]},
      "exceptions": ["Supported by specific, verified evidence"],
      "examples": {},
      "teacher_message": "Review whether the praise is supported by evidence; otherwise use a more measured term."
    },
    {
      "id": "D2",
      "section": "Inflated or Unsupported Language",
      "title": "Generic motivational endings",
      "severity": "warning",
      "policy": "Avoid generic, unearned motivational endings.",
      "detection": {
        "type": "phrase_match",
        "avoid_terms": ["Keep shining!", "Reach for the stars!", "She will achieve great success.", "He will unlock his full potential.", "She is destined for excellence.", "We know he will make everyone proud."],
        "preferred_terms": ["This will support her continued progress.", "With consistent effort, he can strengthen this area further.", "She is well placed to continue developing these skills.", "We wish him well for Grade 7."]
      },
      "exceptions": [],
      "examples": {},
      "teacher_message": "Replace with a restrained, specific closing statement."
    },
    {
      "id": "E1",
      "section": "Development Points",
      "title": "Specific and practical",
      "severity": "warning",
      "policy": "Where feasible, growth points should identify the observable issue, the action the student can take, and the expected learning benefit.",
      "detection": {"type": "structural", "notes": "This is the fuller structural test; A4 is the minimum pass/fail check."},
      "exceptions": [],
      "examples": {
        "pass": "Reading questions carefully and reviewing answers before submission will help reduce avoidable errors.",
        "flag": "She should become more serious."
      },
      "teacher_message": "Where possible, add the expected benefit of taking this action."
    },
    {
      "id": "E2",
      "section": "Development Points",
      "title": "Reasonable number of development points",
      "severity": "required",
      "policy": "A remark should generally contain no more than two or three priority areas.",
      "detection": {"type": "count", "notes": "Count distinct concern areas: attendance, handwriting, focus, emotional regulation, peer relationships, spelling, organisation, reading, submission, self-confidence, etc."},
      "exceptions": [],
      "examples": {},
      "teacher_message": "Reduce to two or three priority development areas."
    },
    {
      "id": "E3",
      "section": "Development Points",
      "title": "Avoid repeated directives",
      "severity": "warning",
      "policy": "Avoid overuse of directive language. A single instance of 'must', 'needs to', 'should' or 'has to' is not itself an issue; flag only when these are repeated, harsh in combination, or used in an overly directive tone throughout the remark.",
      "detection": {
        "type": "frequency_and_semantic",
        "terms": ["must", "needs to", "should", "has to"],
        "threshold": "flag when the count of these terms in one remark meets or exceeds settings.e3_directive_repeat_threshold, or when combined with harsh phrasing"
      },
      "exceptions": ["A single occurrence with otherwise constructive tone"],
      "examples": {
        "preferred_terms": ["would benefit from", "is encouraged to", "continuing to practise", "developing a routine for", "greater attention to", "regular revision will support"]
      },
      "teacher_message": "Vary the directive phrasing; this remark repeats 'must/needs to/should' language."
    },
    {
      "id": "E4",
      "section": "Development Points",
      "title": "Avoid vague counselling advice",
      "severity": "critical",
      "policy": "Growth points should stay within classroom/academic responsibility; avoid advice that strays into personal/emotional counselling.",
      "detection": {"type": "phrase_match", "terms": ["love yourself", "accept yourself as you are", "confide in your parents", "choose better friends", "build emotional strength", "practise inner healing", "manage racing thoughts"]},
      "exceptions": [],
      "examples": {},
      "teacher_message": "Convert this into an observable, school-based action rather than personal counselling advice."
    },
    {
      "id": "F1",
      "section": "Safeguarding, Privacy and Sensitivity",
      "title": "Medical and diagnostic information",
      "severity": "critical",
      "policy": "Do not include medical diagnoses, mental-health conditions, therapy, speech therapy, counselling details, medication, doctor's recommendations, special-needs details, or confidential intervention programmes.",
      "detection": {"type": "semantic", "notes": "Flag any disclosure of medical/therapeutic/diagnostic content."},
      "exceptions": ["School has explicitly authorised this content for the given report format"],
      "examples": {},
      "teacher_message": "Remove this medical/diagnostic content; it may only appear with explicit school authorisation."
    },
    {
      "id": "F2",
      "section": "Safeguarding, Privacy and Sensitivity",
      "title": "Clinical or diagnostic wording",
      "severity": "critical",
      "policy": "Avoid clinical/diagnostic terminology; use observable behaviour instead.",
      "detection": {"type": "phrase_match", "terms": ["executive dysfunction", "emotionally dependent", "dysregulated", "clinically anxious", "attention deficit", "sensory issues", "behavioural disorder"]},
      "exceptions": [],
      "examples": {"pass": "He benefits from reminders to return his attention to the task."},
      "teacher_message": "Replace clinical wording with an observable-behaviour description."
    },
    {
      "id": "F3",
      "section": "Safeguarding, Privacy and Sensitivity",
      "title": "Personal and family matters",
      "severity": "critical",
      "policy": "Do not mention family relationships, parental wishes, comparisons with parents, family travel reasons, home conflicts, financial circumstances, another sibling's needs, or private conversations with parents.",
      "detection": {"type": "semantic", "notes": "Flag any content describing home/family circumstances."},
      "exceptions": [],
      "examples": {},
      "teacher_message": "Remove this family/personal detail; keep the remark focused on school learning and behaviour."
    },
    {
      "id": "F4",
      "section": "Safeguarding, Privacy and Sensitivity",
      "title": "Personal habits outside school",
      "severity": "required",
      "policy": "Avoid comments on personal hygiene, eating habits, sleep, clothing, body image, screen time, gaming or video games, or household routines. A report remark should normally remain focused on school learning and behaviour.",
      "detection": {"type": "semantic", "notes": "Screen time, gaming and video game references are treated as an outside-school personal habit unless authorised."},
      "exceptions": ["settings.screen_time_reporting_authorised is true and digital habits are formally part of this school's reporting format"],
      "examples": {},
      "teacher_message": "Remove this personal-habit reference unless the school has authorised it for this report."
    },
    {
      "id": "F5",
      "section": "Safeguarding, Privacy and Sensitivity",
      "title": "Humiliating or embarrassing language",
      "severity": "critical",
      "policy": "Avoid phrases likely to shame the student.",
      "detection": {"type": "phrase_match", "terms": ["negative attention-seeking", "causes disruption for everyone", "behaves badly", "is immature", "has no self-control", "is careless and lazy", "acts foolishly", "is socially awkward"]},
      "exceptions": [],
      "examples": {},
      "teacher_message": "Remove or rephrase this potentially shaming language."
    },
    {
      "id": "G1",
      "section": "Equity and Fairness",
      "title": "No comparison with others",
      "severity": "critical",
      "policy": "Avoid comparisons with classmates, siblings, parents, age peers, or 'average students'.",
      "detection": {"type": "semantic", "notes": "Flag any relative-comparison construction against another named/unnamed person or group."},
      "exceptions": [],
      "examples": {
        "flag": ["She performs better than most students in the class.", "Much like her mother, she is highly creative."]
      },
      "teacher_message": "Remove the comparison; describe the student's performance on its own terms."
    },
    {
      "id": "G2",
      "section": "Equity and Fairness",
      "title": "Avoid superiority claims",
      "severity": "warning",
      "policy": "Avoid framing the student as superior to peers.",
      "detection": {"type": "phrase_match", "terms": ["sets him apart", "rare for her age", "surpasses others", "better than peers", "the best student", "a benchmark for others"]},
      "exceptions": [],
      "examples": {},
      "teacher_message": "Reframe without an implicit ranking against peers."
    },
    {
      "id": "G3",
      "section": "Equity and Fairness",
      "title": "Avoid stereotypes",
      "severity": "critical",
      "policy": "Do not make assumptions based on gender, culture, language background, disability, personality type, or family background.",
      "detection": {"type": "semantic", "notes": "Flag generalisations tied to a protected or identity characteristic."},
      "exceptions": [],
      "examples": {},
      "teacher_message": "Remove the assumption; describe only what was directly observed."
    },
    {
      "id": "H1",
      "section": "Internal Consistency",
      "title": "No contradiction",
      "severity": "required",
      "policy": "Flag remarks that describe the same quality as both a strength and a weakness without context.",
      "detection": {"type": "consistency_check", "notes": "Acceptable only if the remark explains a change over time or a specific context."},
      "exceptions": ["Remark explicitly explains a change over time or a specific context"],
      "examples": {"flag": "She consistently remains focused throughout lessons. / She needs frequent reminders to focus during lessons."},
      "teacher_message": "Resolve the contradiction, or add context explaining the change."
    },
    {
      "id": "H3",
      "section": "Internal Consistency",
      "title": "Correct logical connectors",
      "severity": "warning",
      "policy": "Check that connective words (additionally, however, although, while, moving forward, at the same time) are used logically. Do not use 'Additionally' to introduce the first and only development point.",
      "detection": {"type": "structural", "notes": "Check connector usage against the surrounding sentence structure."},
      "exceptions": [],
      "examples": {},
      "teacher_message": "Correct the logical connector; it does not match the surrounding sentence structure."
    },
    {
      "id": "K3",
      "section": "Length and Readability",
      "title": "Readability",
      "severity": "warning",
      "policy": "Flag remarks that are hard to read.",
      "detection": {"type": "semantic_and_structural", "flags": ["very long sentences", "reads like a list of praise", "excessive repetition of student's name", "several unrelated ideas in one sentence", "overly formal or complicated language", "too many adjectives", "substantially longer than other remarks"]},
      "exceptions": [],
      "examples": {},
      "teacher_message": "Simplify sentence structure or reduce repetition/adjective density."
    }
  ],

  "final_checklist_reference": [
    "Student's name is correct",
    "Pronouns are correct and consistent",
    "Remark contains at least one specific, observable strength, supported by an example where appropriate",
    "Important claims are supported by evidence",
    "Academic claims (improved, declined, or general such as 'performs well across all subjects') match records",
    "Remark includes at least one specific and actionable growth point stating what the student should practise or improve",
    "Growth point is not vague or overly personal",
    "Remark is balanced — not all praise or all criticism, unless allow_strengths_only is enabled",
    "Remark describes observable school behaviour, not personality judgements",
    "No sensitive or confidential information disclosed without authorisation",
    "Language is professional and non-judgemental",
    "No exaggerated or promotional language",
    "No contradictions or repeated sentences",
    "School programmes, subjects, sports and roles are written per the school's configured style",
    "Spelling, grammar, punctuation and spacing have been checked",
    "Remark is within the character limit, including line breaks",
    "Closing is present (if required) and is brief and professional",
    "Reviewer has not rewritten the remark; final correction is made by the teacher"
  ]
};

// ── Embedded system prompt (prompts/system_prompt.md) ────────────────────────

var SYSTEM_PROMPT = [
  '# System Prompt — Quillwarden Reviewer',
  '',
  'You are Quillwarden, an automated report remark reviewer. You are not a writer or editor of remarks — you are a checker. Your only job is to compare each remark against a fixed rubric and report issues.',
  '',
  '## Inputs you will be given',
  '1. **`quillwarden_rubric.json`** — the complete, authoritative rule set. It contains `settings`, `constraints`, `severity_levels`, `categories` (the individual rules, each with `id`, `policy`, `detection`, `exceptions`, `severity` or `severity_conditions`, and `teacher_message`), and `review_output_schema`. Treat every field in this file as binding. Do not apply rules from memory, general "good writing" instincts, or any source outside this file.',
  '2. **A spreadsheet** of student remarks (typically columns like Student Name, Class/Section, Remark, and sometimes Pronoun/Gender if the school has provided verified roster data). Open and read it in full before reviewing anything — do not sample or summarize it first.',
  '',
  '## What to do',
  'For every row in the spreadsheet:',
  '',
  '1. Extract the student name and the remark text.',
  '2. Check the remark against **every** rule in `categories`, not just an obvious subset. Work through the rules in order rather than pattern-matching for the first issue you notice.',
  '3. For rules with a single `severity`, apply it directly.',
  '4. For rules with `severity_conditions` (currently B2 pronouns and B5 attendance), determine which condition actually applies to this remark\'s content and use that condition\'s severity — do not default to the most severe option.',
  '5. Before flagging anything, check the rule\'s `exceptions` list. If an exception applies, do not raise the issue.',
  '6. Apply `settings` exactly as given (e.g. `allow_strengths_only`, `require_closing`, `max_characters_including_spaces_and_line_breaks`, `e3_directive_repeat_threshold`). If a setting isn\'t present in the file you\'re given, assume the rubric\'s stated default.',
  '7. For any rule where `detection.type` includes `record_verification` (B3 academic claims, B4 activities/awards/roles) — you have no access to the school\'s actual records. Always flag these with `requires_record_verification: true` and the rule\'s `teacher_action`, never assert the claim is true or false yourself.',
  '8. For B2 pronoun checks — only use pronoun/gender data if it is explicitly present as verified roster data in the spreadsheet. Never infer gender or pronoun from the student\'s name. If no roster data is provided, do not raise an issue for that reason alone — only flag a pronoun as `critical` if it actually contradicts roster data (when present) or is inconsistent within the remark itself (e.g. switches between he/she for the same student).',
  '9. When you cite an issue, quote the exact offending phrase from the remark (`exact_phrase`) rather than paraphrasing it.',
  '',
  '## Output format',
  'Return results as a JSON array, one object per student, each conforming exactly to `review_output_schema`:',
  '',
  '```json',
  '[',
  '  {',
  '    "student_name": "string",',
  '    "status": "no_major_issues | minor_edits | needs_revision | critical_issue",',
  '    "issues": [',
  '      {',
  '        "rule_id": "string",',
  '        "severity": "critical|required|warning",',
  '        "exact_phrase": "string",',
  '        "explanation": "string",',
  '        "teacher_action": "string",',
  '        "requires_record_verification": true',
  '      }',
  '    ],',
  '    "character_count": 0,',
  '    "teacher_revision_required": true',
  '  }',
  ']',
  '```',
  '',
  'Derive `status` from the highest severity present in `issues` (critical_issue > needs_revision > minor_edits > no_major_issues). Set `teacher_revision_required` to `true` whenever `issues` is non-empty.',
  '',
  'After the JSON array, add a short plain-text summary: total remarks reviewed, and a count of remarks by status, so a teacher can triage the batch quickly without reading every object.',
  '',
  '## Hard constraints',
  '- **Never rewrite, reword, or suggest replacement text for a remark**, per `constraints.reviewer_must_not_rewrite_remark`. Only describe the issue and the teacher action. If asked to "fix" a remark, decline and point back to the flagged issues instead.',
  '- Never fabricate facts about a student\'s grades, records, awards, or attendance reasons.',
  '- Never guess a pronoun from a name.',
  '- Do not skip students, merge rows, or summarize instead of individually reviewing each remark.',
  '- If the spreadsheet is missing a column the rubric needs (e.g. no character count, no roster pronoun column), note that once at the top of your reply rather than silently guessing.',
  '- The teacher, not you, makes the final decision on every remark. Your output is advisory input to their review, not a final verdict.'
].join('\n');
