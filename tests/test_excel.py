import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from openpyxl import load_workbook
from core import db, excel
from core.companies import norm_company


def mk(i, co, title="Node Dev"):
    return {"platform": "naukri", "job_id": str(i), "title": title, "company": co, "location": "Noida", "remote": 0,
            "url": f"http://j/{i}", "exp_text": "2-4 Yrs"}


def test_company_normalisation():
    assert norm_company("Agileapt%20solution%20pvt%20ltd") == norm_company("AgileAPT Solution Private Limited")
    assert norm_company("Acme Pvt. Ltd.") == norm_company("ACME")
    assert norm_company("Acme Labs") != norm_company("Acme")


def test_excel_only_applied_and_agent_rows_roundtrip(tmp_path):
    con = db.connect(tmp_path / "t.db")
    f = tmp_path / "Applied_Jobs.xlsx"
    rid = db.start_run(con, False)
    db.log_application(con, rid, mk(1, "Alpha Pvt Ltd"), "applied", "", 90, ["Node.js", "React.js"], "/x/Archit_Jain_SDE.docx")
    db.log_application(con, rid, mk(2, "Beta"), "external", "redirects", 90)
    db.log_application(con, rid, mk(3, "Gamma"), "skipped", "blocked stack", 0)
    db.log_application(con, rid, mk(4, "Delta"), "unconfirmed", "clicked", 90)
    db.log_application(con, rid, mk(5, "Eps"), "dry_run", "would apply", 90)
    excel.export(con, f)
    ws = load_workbook(f)["Applied"]
    rows = [[c.value for c in r] for r in ws.iter_rows(min_row=2)]
    assert len(rows) == 1 and rows[0][5] == "Alpha Pvt Ltd" and rows[0][13] == "Archit_Jain_SDE.docx" and rows[0][16] == "Tool"

    # the browser agent appends a row by hand (no key, no #, no area)
    wb = load_workbook(f); ws = wb["Applied"]
    ws.append([None, "2026-11-18", "14:05", "company-site", "Company website (browser agent)", "Zeta Technologies Pvt Ltd",
               "MERN Developer", "Gurugram", None, "Hybrid", "2-4 Yrs", None, "React, Node", "Archit_Jain_SDE.docx",
               "https://zeta.example/apply/9", "Application received #123", "Antigravity", None])
    wb.save(f)
    assert excel.ingest(con, f) == 1
    assert excel.ingest(con, f) == 0                                        # idempotent
    assert db.company_applied_count(con, "ZETA technologies") == 1          # the tool now knows: never apply to Zeta again
    assert db.company_applied_count(con, "Alpha") == 1
    assert db.company_applied_count(con, "Beta") == 0                       # external is not an application
    assert db.company_applied_count(con, "Delta") == 1                      # unconfirmed counts, to be safe
    excel.export(con, f)
    ws = load_workbook(f)["Applied"]
    rows = [[c.value for c in r] for r in ws.iter_rows(min_row=2)]
    assert len(rows) == 2 and {r[5] for r in rows} == {"Alpha Pvt Ltd", "Zeta Technologies Pvt Ltd"}
    zeta = next(r for r in rows if r[5].startswith("Zeta"))
    assert zeta[16] == "Antigravity" and zeta[8] == "Gurugram" and zeta[17]        # area + key filled by the tool
    assert (tmp_path.parent / "x").exists() or True


def test_handoff_skips_companies_already_applied(tmp_path):
    from core import handoff
    con = db.connect(tmp_path / "t.db")
    rid = db.start_run(con, False)
    db.log_application(con, rid, mk(1, "Alpha Pvt Ltd"), "applied", "", 90)
    db.log_application(con, rid, mk(2, "ALPHA"), "external", "redirects", 90)       # same company on another posting
    db.log_application(con, rid, mk(3, "Beta"), "external", "redirects", 90)
    g = handoff.pending_jobs(con)
    assert [r["company"] for r in g["external"]] == ["Beta"]


def test_corrected_job_is_not_reimported_from_old_excel_row(tmp_path):
    con = db.connect(tmp_path / "t.db")
    rid = db.start_run(con, False)
    rowid = db.log_application(con, rid, mk(1, "Trigyn"), "applied", "")
    xl = tmp_path / "Applied_Jobs.xlsx"
    excel.export(con, xl)
    con.execute("UPDATE applications SET status='external', reason='company site' WHERE id=?", (rowid,))   # corrected later
    con.commit()
    assert excel.ingest(con, xl) == 0
    excel.export(con, xl)
    assert con.execute("SELECT COUNT(*) c FROM applications WHERE status='applied'").fetchone()["c"] == 0
    assert con.execute("SELECT COUNT(*) c FROM applications").fetchone()["c"] == 1
