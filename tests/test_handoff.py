import sys, csv
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import db, handoff


def test_pending_and_import(tmp_path):
    con = db.connect(tmp_path / "t.db")
    rid = db.start_run(con, False)
    mk = lambda i, co: {"platform": "naukri", "job_id": str(i), "title": f"Node Dev {i}", "company": co, "location": "Noida", "remote": 0, "url": f"http://j/{i}", "exp_text": "2-4 Yrs"}
    db.log_application(con, rid, mk(1, "A"), "external", "redirects to company website", 90, ["Node.js"])
    db.log_application(con, rid, mk(2, "A"), "external", "redirects to company website", 90, ["Node.js"])      # same company -> one only
    db.log_application(con, rid, mk(3, "B"), "needs_manual", "unanswerable question: Are you ok with night shifts?", 90, [])
    db.log_application(con, rid, mk(4, "C"), "external", "redirects", 90, [])
    db.log_application(con, rid, mk(4, "C"), "applied", "", 90, [])                                              # already resolved
    db.log_application(con, rid, mk(5, "D"), "unconfirmed", "clicked Apply", 90, [])
    g = handoff.pending_jobs(con)
    assert len(g["external"]) == 1 and len(g["needs_manual"]) == 1 and len(g["unconfirmed"]) == 1
    res = tmp_path / "results.csv"
    res.write_text("platform,job_id,company,title,status,notes\nnaukri,1,A,x,submitted,confirmation page seen\nnaukri,3,B,x,needs_user,night shifts?\nnaukri,9,Z,x,applied,\nnaukri,5,D,x,,\n")
    n = handoff.import_results(con, res)
    assert n["applied"] == 1 and n["needs_manual"] == 1 and n["ignored"] == 2
    row = con.execute("SELECT status, reason FROM applications WHERE job_id='1' ORDER BY id LIMIT 1").fetchone()
    assert row["status"] == "applied" and "browser agent" in row["reason"]


def test_handoff_points_to_the_original_resume(tmp_path, monkeypatch):
    from core.config import ROOT, load_config
    con = db.connect(tmp_path / "t.db")
    monkeypatch.setattr(handoff, "HANDOFF", tmp_path / "h")
    handoff.build(con)
    brief = (tmp_path / "h" / "ANTIGRAVITY_BRIEF.md").read_text()
    assert str((ROOT / load_config()["resume_docx"]).resolve()) in brief
    assert "Archit_Jain_Resume" not in brief
