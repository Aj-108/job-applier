import sys, time, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import db, report


def test_report_with_fake_rows(tmp_path):
    con = db.connect(tmp_path / "t.db")
    rid = db.start_run(con, False)
    jobs = [("naukri", "applied", "Gurugram"), ("naukri", "applied", "Remote"), ("linkedin", "applied", "Noida"),
            ("instahyre", "external", "Delhi"), ("naukri", "skipped", "Pune"), ("linkedin", "needs_manual", "Mohali")]
    for i, (p, s, loc) in enumerate(jobs):
        j = {"platform": p, "job_id": str(i), "title": "Full Stack Developer", "company": f"Co{i}", "location": loc,
             "remote": loc == "Remote", "url": "http://x"}
        db.log_application(con, rid, j, s, "blocked stack: Django" if s == "skipped" else "", 70 + i, ["React.js", "Node.js"], "")
    db.end_run(con, rid, 4.2)
    g = report.gather(con, rid)
    assert g["status"]["applied"] == 3 and g["total"] == 6
    assert g["by_loc"]["Remote"]["applied"] == 1
    html = report.render_html(g)
    assert "Full Stack Developer" in html and "Needs you (2)" in html
    assert "applied" in report.terminal_summary(g)
    assert db.already_handled(con, "naukri", "0") and not db.already_handled(con, "naukri", "4")
