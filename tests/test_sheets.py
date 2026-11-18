import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import db, sheets


class FakeWS:
    def __init__(self, title):
        self.title = title
        self.rows: list = []
        self.cleared = 0

    def clear(self):
        self.cleared += 1
        self.rows = []

    def update(self, values, value_input_option=None):
        self.rows = values

    def format(self, rng, fmt):
        pass

    def freeze(self, rows=0):
        pass


class FakeSheet:
    def __init__(self):
        self._ws = {}

    def worksheets(self):
        return list(self._ws.values())

    def worksheet(self, title):
        if title not in self._ws:
            raise Exception("not found")
        return self._ws[title]

    def add_worksheet(self, title, rows, cols):
        ws = FakeWS(title)
        self._ws[title] = ws
        return ws


def mk(con, rid, jid, co, title, ts):
    row_id = db.log_application(con, rid, {"platform": "naukri", "job_id": jid, "title": title, "company": co, "location": "x",
                                           "remote": 0, "url": "u", "exp_text": ""}, "queued", "")
    con.execute("UPDATE applications SET status='applied', ts=? WHERE id=?", (ts, row_id))
    con.commit()


def test_not_configured_refuses():
    assert not sheets.configured({"google_sheet": {"enabled": False}})
    assert not sheets.configured({"google_sheet": {"enabled": True, "sheet_id": "x"}})       # no key file path
    try:
        sheets.open_sheet({"google_sheet": {"enabled": False}})
        assert False
    except sheets.NotConfigured:
        pass


def test_sync_writes_today_and_master_rebuilds_old_day_once(monkeypatch, tmp_path):
    con = db.connect(tmp_path / "t.db")
    rid = db.start_run(con, False)
    today = time.time()
    yesterday = today - 86400
    mk(con, rid, "1", "Acme", "React Dev", yesterday)
    mk(con, rid, "2", "Beta", "Node Dev", today)

    fake = FakeSheet()
    monkeypatch.setattr(sheets, "open_sheet", lambda cfg=None: fake)
    cfg = {"google_sheet": {"enabled": True, "sheet_id": "x", "service_account_file": "k.json", "master_tab": "All Applied"}}

    res = sheets.sync(con, cfg, log=lambda *a: None)
    from datetime import datetime
    today_s, yday_s = datetime.fromtimestamp(today).date().isoformat(), datetime.fromtimestamp(yesterday).date().isoformat()
    assert set(res["days_written"]) == {today_s, yday_s}                      # first sync: both days are new
    assert res["master_rows"] == 2
    assert {w.title for w in fake.worksheets()} == {today_s, yday_s, "All Applied"}
    assert len(fake.worksheet(yday_s).rows) == 2 and len(fake.worksheet(today_s).rows) == 2   # header + 1 data row

    mk(con, rid, "3", "Gamma", "Full Stack Dev", today)                       # a new application today
    res2 = sheets.sync(con, cfg, log=lambda *a: None)
    assert res2["days_written"] == [today_s]                                 # yesterday's tab is frozen, not rewritten
    assert len(fake.worksheet(today_s).rows) == 3                            # header + 2 rows now
    assert len(fake.worksheet(yday_s).rows) == 2                             # unchanged
    assert fake.worksheet("All Applied").rows[0] == sheets.COLUMNS
    assert res2["master_rows"] == 3


def test_row_values_match_excel_columns(tmp_path):
    con = db.connect(tmp_path / "t.db")
    rid = db.start_run(con, False)
    mk(con, rid, "1", "Acme", "React Dev", time.time())
    from core import excel
    r = excel._applied_rows(con)[0]
    vals = excel.row_values(r)
    assert len(vals) == len(sheets.COLUMNS)
    assert vals[5] == "Acme" and vals[6] == "React Dev"
