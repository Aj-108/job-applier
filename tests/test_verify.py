import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import db, verify


def row(i, title, co):
    return {"id": i, "title": title, "company": co, "ts": 1.0}


def test_titles_match_loosely():
    assert verify.titles_match("Full Stack Developer (MERN Stack)", "Full Stack Developer")
    assert verify.titles_match("React JS Developer", "ReactJS Developer")
    assert verify.titles_match("Node.js Developer", "NodeJS Developer")
    assert not verify.titles_match("Shopify Developer", "React Native Developer")


def test_match_applied_three_ways():
    rows = [row(1, "MERN Stack Developer", "Dotflick Pvt Ltd"), row(2, "Shopify Developer", "Vayuz Technologies"),
            row(3, "Backend Developer", "Forward Eye Technologies"), row(4, "React Developer", "Osource India")]
    entries = [{"title": "MERN Stack Developer", "company": "Dotflick"},                   # company suffix differs: same company
               {"title": "Full Stack Engineer", "company": "Forward Eye Technologies"},    # company listed, other role
               {"title": "Java Developer", "company": "Someone Else"}]
    ok, other, missing = verify.match_applied(rows, entries)
    assert [r["id"] for r in ok] == [1] and [r["id"] for r in other] == [3] and [r["id"] for r in missing] == [2, 4]


def test_settle_updates_tracker(tmp_path):
    con = db.connect(tmp_path / "t.db")
    rid = db.start_run(con, False)
    for jid, t, c in [("1", "MERN Stack Developer", "Dotflick"), ("2", "Shopify Developer", "Vayuz")]:
        r = db.log_application(con, rid, {"platform": "naukri", "job_id": jid, "title": t, "company": c, "location": "x", "remote": 0,
                                          "url": "u", "exp_text": ""}, "unconfirmed", "clicked Apply, not recognised")
    res = verify.settle(con, "naukri", [{"title": "MERN Stack Developer", "company": "Dotflick"}])
    st = {r["job_id"]: r["status"] for r in con.execute("SELECT job_id, status FROM applications")}
    assert st == {"1": "applied", "2": "unconfirmed"} and len(res["missing"]) == 1
    verify.settle(con, "naukri", [{"title": "MERN Stack Developer", "company": "Dotflick"}], retry_missing=True)
    st = {r["job_id"]: r["status"] for r in con.execute("SELECT job_id, status FROM applications")}
    assert st["2"] == "failed"                                      # reset only on request


def test_run_verify_and_scheduler_hook(monkeypatch, tmp_path):
    import types
    from contextlib import contextmanager
    from core import scheduler
    import core.config as cc
    P = types.ModuleType("naukri")
    P.logged_in = lambda page: True
    P.applied_list = lambda page: [{"title": "MERN Stack Developer", "company": "Dotflick"}]
    @contextmanager
    def fake_session(name):
        yield object()
    monkeypatch.setattr(verify, "session", fake_session)
    monkeypatch.setattr(verify, "_load", lambda n: P)
    con = db.connect(tmp_path / "t.db")
    rid = db.start_run(con, False)
    db.log_application(con, rid, {"platform": "naukri", "job_id": "1", "title": "MERN Stack Developer", "company": "Dotflick",
                                  "location": "x", "remote": 0, "url": "u", "exp_text": ""}, "unconfirmed", "?")
    # empty read = failed read: nothing changes
    P.applied_list = lambda page: []
    assert verify.run_verify(con, ["naukri"], log=lambda *a: None) == []
    assert con.execute("SELECT status FROM applications").fetchone()["status"] == "unconfirmed"
    P.applied_list = lambda page: [{"title": "MERN Stack Developer", "company": "Dotflick"}]
    assert len(verify.run_verify(con, ["naukri"], log=lambda *a: None)[0]["confirmed"]) == 1
    assert con.execute("SELECT status FROM applications").fetchone()["status"] == "applied"


def test_company_variants():
    assert verify.same_company("Mobiloitte Technologies", "Mobiloitte")
    assert verify.same_company("Dotflick Pvt Ltd", "Dotflick Solutions")
    assert not verify.same_company("Tech Mahindra", "Techindo Systems")
    assert not verify.same_company("Al-hadaya", "altBIG")
    assert not verify.same_company("Tech", "Techindo")                     # too short to be a prefix match


def test_external_site_jobs_are_not_applications(tmp_path):
    con = db.connect(tmp_path / "t.db")
    rid = db.start_run(con, False)
    def log(jid, t, c, st):
        db.log_application(con, rid, {"platform": "naukri", "job_id": jid, "title": t, "company": c, "location": "x", "remote": 0,
                                      "url": "u", "exp_text": ""}, st, "r")
    log("1", "Full Stack Developer", "Trigyn Technologies", "applied")        # banner said Applied, Naukri filed it as external
    log("2", "MERN Stack Developer", "Dotflick", "applied")                    # really applied
    log("3", "React Developer", "Moneymul", "unconfirmed")
    res = verify.settle(con, "naukri", [{"title": "MERN Stack Developer", "company": "Dotflick Solutions"}],
                        external=[{"title": "Full Stack Developer", "company": "Trigyn Technologies Pvt Ltd"}])
    st = {r["job_id"]: r["status"] for r in con.execute("SELECT job_id, status FROM applications")}
    assert st == {"1": "external", "2": "applied", "3": "unconfirmed"}
    assert [r["company"] for r in res["to_external"]] == ["Trigyn Technologies"] and len(res["missing"]) == 1
