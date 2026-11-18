"""Applied_Jobs.xlsx - the shared record of every job applied to (by this tool AND by a browser agent such as Antigravity).

  ingest(): rows added to the file by hand / by the agent are loaded into the database (so company de-duplication sees them)
  export(): rewrites the file from the database (ingest first, so nothing the agent added is ever lost)
Only APPLIED jobs are listed. A backup of the previous file is kept in out/.
"""
import hashlib
import shutil
import time
from datetime import datetime
from pathlib import Path
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from .companies import norm_company
from .config import ROOT, OUT, load_config
from .report import bucket

SHEET = "Applied"
COLUMNS = ["#", "Date", "Time", "Platform", "Applied via", "Company", "Job title", "Job location", "Area", "Work type",
           "Experience asked", "Fit score", "Matched skills", "Resume used", "Job link", "Notes", "Source", "Key"]
WIDTHS = [5, 12, 9, 14, 22, 28, 38, 24, 12, 11, 14, 9, 40, 24, 50, 50, 14, 22]


def excel_path() -> Path:
    return ROOT / load_config().get("excel_file", "Applied_Jobs.xlsx")


def _key(platform, job_id):
    return f"{platform}|{job_id}"


def _applied_rows(con):
    return [dict(r) for r in con.execute("SELECT * FROM applications WHERE status='applied' ORDER BY ts, id")]


# ------------------------------------------------------------------ Excel -> database
def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    ws = load_workbook(path, data_only=True)[SHEET] if SHEET in load_workbook(path, read_only=True).sheetnames else None
    if ws is None:
        return []
    it = ws.iter_rows(values_only=True)
    header = [str(h).strip() if h else "" for h in next(it, [])]
    rows = []
    for vals in it:
        r = dict(zip(header, vals))
        if r.get("Company") and r.get("Job title"):
            rows.append(r)
    return rows


def _ts(r) -> float:
    d, t = r.get("Date"), r.get("Time")
    try:
        if isinstance(d, datetime):
            dt = d
        else:
            dt = datetime.strptime(str(d)[:10], "%Y-%m-%d")
        if isinstance(t, datetime):
            t = t.time()
        if t and not isinstance(t, str):
            dt = dt.replace(hour=t.hour, minute=t.minute, second=t.second)
        elif t:
            hh, mm, *ss = [int(x) for x in str(t).split(":")]
            dt = dt.replace(hour=hh, minute=mm, second=(ss[0] if ss else 0))
        return dt.timestamp()
    except Exception:
        return time.time()


def ingest(con, path: Path | None = None) -> int:
    """Load rows that exist in the Excel but not in the database (added by a browser agent or by hand)."""
    path = path or excel_path()
    # a row the TOOL wrote carries its key: if the tracker knows that job under ANY status (it may have been corrected to
    # external / failed since), the tracker wins and the old Excel row must not come back as "added by hand"
    known_keys = {_key(r["platform"], r["job_id"]) for r in con.execute("SELECT platform, job_id FROM applications")}
    known_pairs = {(norm_company(r["company"]), (r["title"] or "").lower().strip()) for r in _applied_rows(con)}
    added = 0
    for r in read_rows(path):
        company, title = str(r["Company"]).strip(), str(r["Job title"]).strip()
        platform = str(r.get("Platform") or "company-site").strip().lower() or "company-site"
        key = str(r.get("Key") or "").strip()
        pair = (norm_company(company), title.lower())
        if (key and key in known_keys) or pair in known_pairs:
            continue
        job_id = key.split("|", 1)[1] if "|" in key else hashlib.md5(f"{company}{title}".encode()).hexdigest()[:12]
        loc = str(r.get("Job location") or "")
        con.execute(
            """INSERT INTO applications(run_id, ts, platform, job_id, title, company, location, remote, exp_text, fit_score,
               status, reason, matched_skills, resume_path, url) VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (_ts(r), platform, job_id, title, company, loc, int(str(r.get("Work type") or "").lower() == "remote"),
             str(r.get("Experience asked") or ""), r.get("Fit score") if isinstance(r.get("Fit score"), (int, float)) else None,
             "applied", ("added via Excel by " + str(r.get("Source") or "you") + (": " + str(r["Notes"]) if r.get("Notes") else ""))[:300],
             str(r.get("Matched skills") or "").replace(", ", ","), str(r.get("Resume used") or ""), str(r.get("Job link") or "")))
        known_keys.add(_key(platform, job_id))
        known_pairs.add(pair)
        added += 1
    if added:
        con.commit()
    return added


# ------------------------------------------------------------------ database -> Excel
def export(con, path: Path | None = None) -> Path:
    path = path or excel_path()
    ingest(con, path)                                       # never lose rows someone else added
    rows = _applied_rows(con)
    if path.exists():
        OUT.mkdir(exist_ok=True)
        shutil.copy(path, OUT / "Applied_Jobs.backup.xlsx")
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET
    ws.append(COLUMNS)
    head_fill = PatternFill("solid", fgColor="1F4E78")
    for c in ws[1]:
        c.font, c.fill, c.alignment = Font(bold=True, color="FFFFFF"), head_fill, Alignment(vertical="center", wrap_text=True)
    for i, r in enumerate(rows, 1):
        dt = datetime.fromtimestamp(r["ts"])
        agent = "added via Excel" in (r["reason"] or "")
        src = (r["reason"] or "").split("by ", 1)[-1].split(":")[0].strip().title() if agent else "Tool"
        via = "Company website (browser agent)" if (agent or (r["reason"] or "").startswith("applied on the company site")) else ("Naukri" if r["platform"] == "naukri" else
              "Instahyre" if r["platform"] == "instahyre" else r["platform"].title())
        ws.append([i, dt.date(), dt.time().replace(microsecond=0), r["platform"], via, r["company"], r["title"], r["location"],
                   bucket(r["location"], r["remote"]), "Remote" if r["remote"] else "Onsite/Hybrid", r["exp_text"] or "",
                   round(r["fit_score"]) if r["fit_score"] is not None else None,
                   (r["matched_skills"] or "").replace(",", ", "), Path(r["resume_path"]).name if r["resume_path"] else "",
                   r["url"], (r["reason"] or "") if not agent else (r["reason"] or "").split(":", 1)[-1].strip(), src,
                   _key(r["platform"], r["job_id"])])
        link = ws.cell(row=i + 1, column=15)
        if r["url"]:
            link.hyperlink, link.font = r["url"], Font(color="0563C1", underline="single")
        ws.cell(row=i + 1, column=2).number_format = "dd-mmm-yyyy"
        ws.cell(row=i + 1, column=3).number_format = "hh:mm"
    for n, w in enumerate(WIDTHS, 1):
        ws.column_dimensions[get_column_letter(n)].width = w
    ws.freeze_panes = "G2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}{max(2, len(rows) + 1)}"
    _summary(wb, con, rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.xlsx")
    wb.save(tmp)
    tmp.replace(path)
    return path


def _summary(wb, con, rows):
    ws = wb.create_sheet("Summary")
    bold = Font(bold=True)
    ws["A1"], ws["A1"].font = "Applied jobs - summary (live formulas over the 'Applied' sheet)", Font(bold=True, size=13)
    ws["A3"], ws["B3"] = "Total applied", "=COUNTA(Applied!F2:F5000)"
    ws["A4"], ws["B4"] = "Distinct companies", f"={len({norm_company(r['company']) for r in rows})}"
    ws["A5"], ws["B5"] = "Last updated", datetime.now().strftime("%d-%b-%Y %H:%M")
    r0 = 8
    for title, col, vals in (("By platform", "D", sorted({r["platform"] for r in rows})),
                             ("By area", "I", ["Remote", "Delhi", "Gurugram", "Noida", "Mohali", "Chandigarh", "Ludhiana", "Other"]),
                             ("By source", "Q", ["Tool", "Antigravity"])):
        ws.cell(row=r0, column=1, value=title).font = bold
        for i, v in enumerate(vals, 1):
            ws.cell(row=r0 + i, column=1, value=v)
            ws.cell(row=r0 + i, column=2, value=f'=COUNTIF(Applied!{col}2:{col}5000,"{v}")')
        r0 += len(vals) + 2
    ws.cell(row=r0, column=1, value="By day").font = bold
    days = sorted({datetime.fromtimestamp(r["ts"]).date() for r in rows})
    for i, d in enumerate(days, 1):
        ws.cell(row=r0 + i, column=1, value=d.strftime("%d-%b-%Y"))
        ws.cell(row=r0 + i, column=2, value=sum(1 for r in rows if datetime.fromtimestamp(r["ts"]).date() == d))
    ws.column_dimensions["A"].width = 52
    ws.column_dimensions["B"].width = 16
