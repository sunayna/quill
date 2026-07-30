/**
 * Quillwarden — Google Apps Script remark checker.
 *
 * Ports both halves of the Python project to run directly inside a Google
 * Sheet:
 *  - Deterministic checks (rules I1-I3, J1-J3, J5, K1, H2 — J4 sport
 *    capitalisation is intentionally omitted, not just skipped, per school
 *    preference), mirroring deterministic-validator/validator.py and
 *    rules/*.py exactly.
 *  - LLM judgement checks (tone, safeguarding, unsupported claims, balance,
 *    etc.), mirroring llm-reviewer/reviewer.py — one batched Gemini call per
 *    run, using the rubric and system prompt embedded below (rubric v1.2).
 *
 * SETUP
 * 1. Open the remarks Google Sheet.
 * 2. Extensions > Apps Script.
 * 3. Delete any starter code, paste this whole file in, save.
 * 4. Edit the CONFIG block below so the header names match your sheet exactly.
 * 5. Reload the sheet. A "Quillwarden" menu appears.
 * 6. (Optional, for the AI layer) Each teacher: Quillwarden > Set My Gemini
 *    Key, and paste a personal key from aistudio.google.com/apikey. Stored
 *    only under that teacher's own account (PropertiesService
 *    UserProperties) — never shared with other users of the sheet.
 * 7. Use Quillwarden > Review Remarks. Deterministic checks always run; the
 *    AI layer runs too if the current user has saved a key, otherwise it's
 *    skipped with a note in the completion toast.
 *
 * BEHAVIOUR
 * - For each row, reviews the Term 2 remark if it is non-empty, otherwise falls
 *   back to the Term 1 remark for that row.
 * - Writes one output column (header from CONFIG.OUTPUT_HEADER) as the last
 *   column of the sheet — reused on repeat runs rather than duplicated.
 * - Duplicate-remark detection (H2) runs only over the set of remarks actually
 *   reviewed (i.e. each row's chosen Term 1/Term 2 remark), so it will not
 *   false-positive a student's own Term 1 remark against their own Term 2 remark.
 * - Deterministic and AI issues are merged per row, deduplicated by rule_id
 *   (deterministic result wins on overlap, though in practice the two layers
 *   cover disjoint rule sets).
 */

var CONFIG = {
  STUDENT_NAME_HEADER: 'Student Name',
  REMARK_TERM1_HEADER: 'Remark Term 1',
  REMARK_TERM2_HEADER: 'Remark Term 2',
  OUTPUT_HEADER: 'Quillwarden Review',
  MAX_CHARACTERS: 1000
};

var GEMINI_MODEL = 'gemini-flash-latest';
var GEMINI_API_KEY_PROPERTY = 'GEMINI_API_KEY';

// Rule IDs already handled by the deterministic checks below (or, for J4,
// deliberately not checked at all per school preference) — excluded from
// the LLM prompt so it only judges the remaining, judgement-based rules.
var LLM_SKIP_RULE_IDS = ['I1', 'I2', 'I3', 'J1', 'J2', 'J3', 'J4', 'J5', 'K1', 'H2'];

// ── Menu ─────────────────────────────────────────────────────────────────────

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Quillwarden')
    .addItem('Review Remarks', 'reviewRemarks')
    .addItem('Set My Gemini Key', 'setGeminiKey_')
    .addItem('Clear My Gemini Key', 'clearGeminiKey_')
    .addToUi();
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

function reviewRemarks() {
  var sheet = SpreadsheetApp.getActiveSheet();
  var lastRow = sheet.getLastRow();
  var lastCol = sheet.getLastColumn();
  if (lastRow < 2) {
    SpreadsheetApp.getUi().alert('No data rows found below the header row.');
    return;
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
    SpreadsheetApp.getUi().alert(
      'Could not find column(s): ' + missing.join(', ') +
      '.\n\nEdit the CONFIG block at the top of the script so the header names match your sheet exactly (found headers: ' +
      headerRow.join(' | ') + ').'
    );
    return;
  }

  var outputCol = findColumn_(headerRow, CONFIG.OUTPUT_HEADER);
  if (outputCol === -1) {
    outputCol = lastCol + 1;
    sheet.getRange(1, outputCol).setValue(CONFIG.OUTPUT_HEADER);
  }

  var numRows = lastRow - 1;
  var names = sheet.getRange(2, nameCol, numRows, 1).getValues();
  var term1s = sheet.getRange(2, term1Col, numRows, 1).getValues();
  var term2s = sheet.getRange(2, term2Col, numRows, 1).getValues();

  var rows = [];
  for (var i = 0; i < numRows; i++) {
    var name = String(names[i][0] || '').trim();
    var t1 = String(term1s[i][0] || '').trim();
    var t2 = String(term2s[i][0] || '').trim();
    var activeRemark = t2 !== '' ? t2 : t1;
    var activeTerm = t2 !== '' ? 'Term 2' : (t1 !== '' ? 'Term 1' : null);
    rows.push({ name: name, remark: activeRemark, term: activeTerm });
  }

  // Per-remark deterministic checks.
  var perRowIssues = rows.map(function (r) {
    if (!r.remark) return [];
    return runChecks_(r.remark);
  });

  // Cross-row duplicate check (H2 across), scoped to the remarks actually reviewed.
  var crossIssues = checkH2Across_(
    rows.map(function (r) { return r.remark; }),
    rows.map(function (r) { return r.name; })
  );

  // LLM judgement-based checks (tone, safeguarding, unsupported claims, etc.),
  // run only if the current user has saved their own Gemini key.
  var llmStatus = runLLMReviewForRows_(rows);
  // llmStatus = { ran: bool, error: string|null, issuesByRowIndex: {index: [issue,...]} }

  var counts = { NO_MAJOR_ISSUES: 0, MINOR_EDITS: 0, NEEDS_REVISION: 0, CRITICAL_ISSUE: 0, SKIPPED: 0 };
  var outputValues = rows.map(function (r, i) {
    if (!r.remark) {
      counts.SKIPPED++;
      return ['No remark to review (Term 1 and Term 2 both blank).'];
    }
    var detIssues = perRowIssues[i].concat(crossIssues[i]);
    var llmIssues = llmStatus.issuesByRowIndex[i] || [];
    var allIssues = mergeIssuesByRuleId_(detIssues, llmIssues);
    var statusLabel = statusLabelFromIssues_(allIssues);
    counts[statusLabel.replace(/ /g, '_')] = (counts[statusLabel.replace(/ /g, '_')] || 0) + 1;
    return [formatCell_(statusLabel, r.term, r.remark.length, allIssues)];
  });

  sheet.getRange(2, outputCol, numRows, 1).setValues(outputValues);

  var aiNote;
  if (llmStatus.ran) {
    aiNote = 'AI review: included.';
  } else if (llmStatus.error) {
    aiNote = 'AI review: FAILED (' + llmStatus.error + ') — showing deterministic checks only.';
  } else {
    aiNote = 'AI review: skipped — no Gemini key set (Quillwarden > Set My Gemini Key).';
  }

  SpreadsheetApp.getActiveSpreadsheet().toast(
    'No major issues: ' + counts.NO_MAJOR_ISSUES +
    ' | Minor edits: ' + counts.MINOR_EDITS +
    ' | Needs revision: ' + counts.NEEDS_REVISION +
    ' | Critical: ' + counts.CRITICAL_ISSUE +
    ' | Skipped: ' + counts.SKIPPED +
    '  —  ' + aiNote,
    'Quillwarden review complete', 12
  );
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
// Mirrors llm-reviewer/reviewer.py: one batched Gemini call per run covering
// every remark, using the full rubric and system prompt as authoritative
// input. Uses the current user's own saved key (PropertiesService
// UserProperties), so each teacher's usage is separate and private.

function runLLMReviewForRows_(rows) {
  var apiKey = PropertiesService.getUserProperties().getProperty(GEMINI_API_KEY_PROPERTY);
  if (!apiKey) {
    return { ran: false, error: null, issuesByRowIndex: {} };
  }

  var entries = []; // { index: original row index, name: label used in the prompt }
  rows.forEach(function (r, i) {
    if (!r.remark) return;
    var label = r.name || ('Row ' + (entries.length + 1));
    entries.push({ index: i, name: label, remark: r.remark });
  });

  if (!entries.length) {
    return { ran: false, error: null, issuesByRowIndex: {} };
  }

  try {
    var prompt = buildLLMPrompt_(entries, LLM_SKIP_RULE_IDS);
    var responseText = callGemini_(prompt, apiKey);
    var results = parseLLMResponse_(responseText);

    var resultsByName = {};
    results.forEach(function (res) {
      if (res && res.student_name) resultsByName[res.student_name] = res;
    });

    var issuesByRowIndex = {};
    entries.forEach(function (entry) {
      var res = resultsByName[entry.name];
      issuesByRowIndex[entry.index] = (res && res.issues) || [];
    });

    return { ran: true, error: null, issuesByRowIndex: issuesByRowIndex };
  } catch (e) {
    console.error('Quillwarden LLM review failed: ' + (e.message || String(e)));
    return { ran: false, error: e.message || String(e), issuesByRowIndex: {} };
  }
}

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

  var maxAttempts = 3;
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

    if (code === 429 || (code >= 500 && code < 600)) {
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
      "id": "D3",
      "section": "Inflated or Unsupported Language",
      "title": "Claims about potential",
      "severity": "warning",
      "policy": "Avoid relying heavily on unobservable 'potential'.",
      "detection": {"type": "semantic", "notes": "Flag references to unrealised or true potential without an observable basis."},
      "exceptions": [],
      "examples": {
        "flag": "He is not performing according to his true potential.",
        "pass": "Greater consistency in completing tasks will help his work reflect his understanding more accurately."
      },
      "teacher_message": "Reframe potential claims in terms of an observable action and outcome."
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
      "policy": "Avoid comments on personal hygiene, eating habits, sleep, clothing, body image, screen time, or household routines. A report remark should normally remain focused on school learning and behaviour.",
      "detection": {"type": "semantic", "notes": "Screen time is treated as an outside-school personal habit unless authorised."},
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
      "id": "K2",
      "section": "Length and Readability",
      "title": "Recommended length",
      "severity": "warning",
      "policy": "Aim for approximately 100-150 words; exact length may vary provided the remark stays clear and within the character limit.",
      "detection": {"type": "count", "recommended_word_count": "100-150"},
      "exceptions": [],
      "examples": {},
      "teacher_message": "Consider trimming or expanding to the recommended word range."
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
    },
    {
      "id": "L1",
      "section": "Closing Statements",
      "title": "Preferred closing",
      "severity": "warning",
      "policy": "The closing should be brief, professional and restrained. A closing is required only if settings.require_closing is true.",
      "detection": {"type": "phrase_match", "preferred_examples": [
        "With continued effort, she is well placed to make steady progress.",
        "Regular practice will support his continued development.",
        "We wish her well for Grade 7.",
        "We wish him continued progress in the next academic year."
      ]},
      "exceptions": ["settings.require_closing is false"],
      "examples": {},
      "teacher_message": "Add a brief, restrained closing statement."
    },
    {
      "id": "L2",
      "section": "Closing Statements",
      "title": "Avoid",
      "severity": "warning",
      "policy": "Avoid effusive or promotional closings.",
      "detection": {"type": "phrase_match", "terms": [
        "Keep shining!",
        "Make everyone proud!",
        "Reach greater heights!",
        "He will certainly excel!",
        "She is destined for success!",
        "Unlock your brilliance!",
        "We look forward to seeing you become a star!"
      ]},
      "exceptions": [],
      "examples": {},
      "teacher_message": "Replace with a restrained closing from the preferred list."
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
