"""Push applied jobs to a Google Sheet: one tab per CALENDAR DAY (named YYYY-MM-DD, written once and then frozen - a
day's tab is only rebuilt while it is still today) plus one 'All Applied' master tab that always holds every row.
Off by default; needs config.yaml `google_sheet.enabled: true` + a service account. See ARCHITECTURE.md."""
import time
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from . import excel
from .config import ROOT, load_config

COLUMNS = excel.COLUMNS
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class NotConfigured(Exception):
    """google_sheet.enabled is false, or the setup is incomplete. Caller decides whether that's an error."""


def _cfg(cfg: dict | None = None) -> dict:
    return (cfg or load_config()).get("google_sheet") or {}


def configured(cfg: dict | None = None) -> bool:
    gs = _cfg(cfg)
    return bool(gs.get("enabled") and gs.get("sheet_id") and gs.get("service_account_file"))


def _client(cfg: dict):
    import gspread
    from google.oauth2.service_account import Credentials
    gs = _cfg(cfg)
    key = ROOT / gs["service_account_file"]
    if not key.exists():
        raise NotConfigured(f"service account file not found: {key}")
    creds = Credentials.from_service_account_file(str(key), scopes=SCOPES)
    return gspread.authorize(creds)


def open_sheet(cfg: dict | None = None):
    cfg = cfg or load_config()
    if not configured(cfg):
        raise NotConfigured("google_sheet.enabled is false, or sheet_id / service_account_file is not set in config.yaml")
    return _client(cfg).open_by_key(_cfg(cfg)["sheet_id"])


def _rows_by_day(con) -> dict[str, list]:
    out = defaultdict(list)
    for r in excel._applied_rows(con):
        out[datetime.fromtimestamp(r["ts"]).date().isoformat()].append(r)
    return out


def _write_tab(sheet, title: str, rows: list[dict]):
    """Create the tab if missing, then replace its content entirely (cheap at this row count, and avoids any
    dedup bookkeeping: the day's rows in the database are always the full truth for that day)."""
    try:
        ws = sheet.worksheet(title)
        ws.clear()
    except Exception:
        ws = sheet.add_worksheet(title=title, rows=max(100, len(rows) + 10), cols=len(COLUMNS))
    body = [excel.row_values(r) for r in rows]
    for i, v in enumerate(body, 1):
        v[0] = i
    ws.update([COLUMNS] + body, value_input_option="USER_ENTERED")
    ws.format("1:1", {"textFormat": {"bold": True}})
    ws.freeze(rows=1)


def sync(con, cfg: dict | None = None, log=print) -> dict:
    """Write today's tab (always), any missing earlier day's tab (first run after being off, or a day with no run),
    and the master tab. Returns {days_written: [...], master_rows: n}."""
    cfg = cfg or load_config()
    sheet = open_sheet(cfg)
    by_day = _rows_by_day(con)
    existing = {w.title for w in sheet.worksheets()}
    today = date.today().isoformat()
    written = []
    for day, rows in sorted(by_day.items()):
        if day == today or day not in existing:            # today is always refreshed; past days are written once
            _write_tab(sheet, day, rows)
            written.append(day)
    all_rows = excel._applied_rows(con)
    _write_tab(sheet, _cfg(cfg).get("master_tab", "All Applied"), all_rows)
    log(f"  [sheets] wrote {', '.join(written) if written else 'no day tabs (nothing new)'}; "
        f"master tab '{_cfg(cfg).get('master_tab', 'All Applied')}' has {len(all_rows)} rows")
    return {"days_written": written, "master_rows": len(all_rows)}
