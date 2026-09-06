"""Read student-remark rows out of Google Sheets living in a shared Drive folder,
and write edited remarks back into the source sheet.

Requires a service account JSON key at credentials/service_account.json,
with the target Drive folder shared to that service account's email
address. Because this module now writes edited remarks back, the folder
must be shared with Editor access (Viewer is no longer enough).
"""
import re
import time
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]

_ROOT = Path(__file__).parent
CREDENTIALS_PATH = _ROOT / "credentials" / "service_account.json"

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _execute_with_retry(request, attempts=5, base_delay=2):
    """Run a googleapiclient request, retrying on transient errors.

    Google's Sheets/Drive APIs occasionally return 503 ("service currently
    unavailable") or other 5xx/429 responses for a moment even when nothing
    is wrong on our end. Retrying with exponential backoff clears almost
    all of these automatically instead of surfacing a scary error to the
    teacher for what is usually a one-off blip.
    """
    last_error = None
    for attempt in range(attempts):
        try:
            return request.execute()
        except HttpError as e:
            status = getattr(e.resp, "status", None)
            last_error = e
            if status not in _RETRYABLE_STATUS or attempt == attempts - 1:
                raise
            time.sleep(base_delay * (2 ** attempt))
    raise last_error


def _get_creds():
    if not CREDENTIALS_PATH.exists():
        raise RuntimeError(
            "Drive credentials not configured. Expected a service account key at "
            "credentials/service_account.json."
        )
    return service_account.Credentials.from_service_account_file(
        str(CREDENTIALS_PATH), scopes=SCOPES
    )


def extract_folder_id(folder_url_or_id):
    folder_url_or_id = (folder_url_or_id or "").strip()
    m = re.search(r"/folders/([a-zA-Z0-9_-]+)", folder_url_or_id)
    if m:
        return m.group(1)
    m = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", folder_url_or_id)
    if m:
        return m.group(1)
    if re.fullmatch(r"[a-zA-Z0-9_-]{10,}", folder_url_or_id):
        return folder_url_or_id
    raise ValueError("Could not find a Drive folder ID in that link.")


def list_spreadsheets(folder_url_or_id):
    """List Google Sheets files directly inside the given Drive folder."""
    folder_id = extract_folder_id(folder_url_or_id)
    creds = _get_creds()
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    files = []
    page_token = None
    while True:
        resp = _execute_with_retry(
            drive.files().list(
                q=(
                    f"'{folder_id}' in parents and "
                    "mimeType='application/vnd.google-apps.spreadsheet' and "
                    "trashed = false"
                ),
                fields="nextPageToken, files(id, name)",
                pageSize=100,
                pageToken=page_token,
            )
        )
        files.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return files


def _norm_header(h):
    return re.sub(r"\s+", " ", str(h)).strip().lower()


def _pick_tab(sheets_api, spreadsheet_id, sheet_name, required_header="student name"):
    """Pick which tab to read.

    Tries an exact (case-insensitive, trimmed) match on `sheet_name` first.
    If that tab's own header row doesn't contain `required_header`, or no
    match is found by name, falls back to scanning every tab's header row
    for one containing `required_header`. Raises with full diagnostics
    (all tab names + their header rows) if nothing qualifies.
    """
    meta = _execute_with_retry(
        sheets_api.spreadsheets().get(
            spreadsheetId=spreadsheet_id, fields="sheets.properties.title"
        )
    )
    titles = [s["properties"]["title"] for s in meta.get("sheets", [])]
    if not titles:
        raise RuntimeError("No tabs found in this spreadsheet.")

    header_by_title = {}

    def header_row(title):
        if title not in header_by_title:
            vals = _execute_with_retry(
                sheets_api.spreadsheets()
                .values()
                .get(spreadsheetId=spreadsheet_id, range=f"'{title}'!1:1")
            ).get("values", [])
            header_by_title[title] = vals[0] if vals else []
        return header_by_title[title]

    ordered_titles = list(titles)
    if sheet_name:
        wanted = _norm_header(sheet_name)
        name_match = next((t for t in titles if _norm_header(t) == wanted), None)
        if name_match:
            ordered_titles = [name_match] + [t for t in titles if t != name_match]

    for title in ordered_titles:
        headers = [_norm_header(h) for h in header_row(title)]
        if required_header in headers:
            return title, header_row(title)

    diag = "; ".join(f"{t}: {header_row(t)}" for t in titles)
    raise RuntimeError(
        f"Could not find a tab with a '{required_header.title()}' column. "
        f"Tabs and header rows found — {diag}"
    )


def get_sheet_rows(spreadsheet_id, sheet_name=None):
    """Read one sheet's rows as a list of {header: value} dicts.

    Picks the tab named `sheet_name` if its header row actually contains a
    "Student Name" column; otherwise scans every tab for one that does
    (so an "Instructions" tab listed before "Review" doesn't get picked by
    mistake), and raises a diagnostic error if none qualify.

    Returns (tab_title, rows). Each row dict also carries "_row_number",
    the actual 1-based row number of that record in the sheet, so an
    edited remark can later be written back to the exact cell it came
    from.
    """
    creds = _get_creds()
    sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
    target, _ = _pick_tab(sheets, spreadsheet_id, sheet_name)

    result = _execute_with_retry(
        sheets.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=f"'{target}'")
    )
    values = result.get("values", [])
    if not values:
        return target, []
    headers = [h.strip() for h in values[0]]
    rows = []
    for offset, raw in enumerate(values[1:], start=2):  # sheet row 1 is the header
        row = {headers[i]: (raw[i].strip() if i < len(raw) else "") for i in range(len(headers))}
        if any(row.values()):
            row["_row_number"] = offset
            rows.append(row)
    return target, rows


def _col_letter(index):
    """0-based column index -> spreadsheet column letter (0->A, 25->Z, 26->AA, ...)."""
    index += 1
    letters = ""
    while index > 0:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def update_remark_cells_batch(spreadsheet_id, tab_title, column_name, updates, create_if_missing=True):
    """Write values (e.g. review comments) into a single column on tab_title.

    updates: list of {"row_number": int, "value": str}. All writes are sent
    in one batchUpdate call. If column_name isn't already a header on that
    tab, it is appended as a new column (with that header written into row
    1) when create_if_missing is True; otherwise this raises with the
    headers actually found.
    """
    creds = _get_creds()
    sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
    header_vals = _execute_with_retry(
        sheets.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=f"'{tab_title}'!1:1")
    ).get("values", [])
    headers = [h.strip() for h in (header_vals[0] if header_vals else [])]
    if column_name not in headers:
        if not create_if_missing:
            raise RuntimeError(
                f"Column '{column_name}' not found on tab '{tab_title}'. Columns: {headers}"
            )
        new_col_letter = _col_letter(len(headers))
        _execute_with_retry(
            sheets.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=f"'{tab_title}'!{new_col_letter}1",
                valueInputOption="RAW",
                body={"values": [[column_name]]},
            )
        )
        headers = headers + [column_name]
    col_letter = _col_letter(headers.index(column_name))
    data = [
        {"range": f"'{tab_title}'!{col_letter}{u['row_number']}", "values": [[u["value"]]]}
        for u in updates
        if u.get("row_number")
    ]
    if not data:
        return
    body = {"valueInputOption": "RAW", "data": data}
    _execute_with_retry(
        sheets.spreadsheets().values().batchUpdate(spreadsheetId=spreadsheet_id, body=body)
    )
