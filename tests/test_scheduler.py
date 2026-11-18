import sys, time, types
from contextlib import contextmanager
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import db, scheduler
import core.config as cc
RESUME = cc.resume_file().name

JOBS = [
    ("1", "Full Stack Developer", "Acme", "Gurugram", "2-4 Yrs", "React.js Node.js Express MongoDB TypeScript REST APIs Redux Docker Git"),
    ("2", "Python Django Developer", "PyCo", "Noida", "3-5 Yrs", "Django Flask Python AWS Kubernetes PostgreSQL"),
    ("3", "Senior MERN Architect", "BigCo", "Delhi", "8-12 Yrs", "React Node MongoDB Express leadership"),
    ("4", "React Developer", "Remo", "Remote", "1-3 Yrs", "React.js Next.js TypeScript Tailwind CSS REST APIs Jest"),
    ("5", "Shopify Developer", "Shoppy", "Mohali", "2-4 Yrs", "Shopify Liquid JavaScript Node.js Remix Polaris Webhooks"),
    ("6", "MERN Developer", "Exty", "Noida", "2-3 Yrs", "React Node MongoDB Express TypeScript"),
]


def fake_platform(name, jobs=JOBS):
    m = types.ModuleType(name)
    m.calls = []
    m.logged_in = lambda page: True
    m.search = lambda page, role, loc: [{"platform": name, "job_id": j[0], "title": j[1], "company": j[2], "location": j[3],
                                          "remote": j[3] == "Remote", "exp_text": j[4], "url": f"http://x/{j[0]}"} for j in jobs if j[3] == loc]
    m.details = lambda page, job: {**job, "description": next(j[5] for j in jobs if j[0] == job["job_id"])}

    def upload(page, pdf):
        raise AssertionError("profile resume must NEVER be uploaded when tailor_resume is false")
    m.upload_profile_resume = upload

    def apply(page, job, pdf, dry):
        m.calls.append((job["job_id"], Path(pdf).name))
        if job["company"] == "Exty":
            return "external", "redirects to company website"
        return ("dry_run", "would apply") if dry else ("applied", "")
    m.apply = apply
    m.precheck = lambda page, job: ("external", "redirects to company website") if job["company"] == "Exty" else None
    return m


def setup(monkeypatch, tmp_path, mods):
    from core import excel
    from contextlib import nullcontext
    monkeypatch.setattr(scheduler, "browser_host", nullcontext)            # never start a real Chrome in tests
    monkeypatch.setattr(excel, "excel_path", lambda: tmp_path / "Applied_Jobs.xlsx")
    real = db.connect
    monkeypatch.setattr(scheduler.db, "connect", lambda: real(tmp_path / "t.db"))
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(scheduler, "_load", lambda n: mods[n])

    @contextmanager
    def fs(n):
        yield object()
    monkeypatch.setattr(scheduler, "session", fs)
    return real(tmp_path / "t.db")


def test_naukri_inline_uses_main_resume_only(monkeypatch, tmp_path):
    m = fake_platform("naukri")
    con = setup(monkeypatch, tmp_path, {"naukri": m})
    scheduler.run(minutes=5, dry_run=False, only="naukri")
    rows = {r["job_id"]: dict(r) for r in con.execute("SELECT * FROM applications")}
    assert rows["2"]["status"] == "skipped" and "blocked" in rows["2"]["reason"]
    assert rows["3"]["status"] == "skipped" and "experience" in rows["3"]["reason"]
    assert rows["1"]["status"] == "applied" and rows["1"]["resume_path"].endswith(RESUME)
    assert rows["6"]["status"] == "external"
    assert all(name == RESUME for _, name in m.calls)           # the main resume, nothing else
    scheduler.run(minutes=5, dry_run=False, only="naukri")                       # second run: no repeat applications
    assert con.execute("SELECT COUNT(*) c FROM applications WHERE job_id='1' AND status='applied'").fetchone()["c"] == 1
    assert (cc.OUT / "report.html").exists()


def test_cap_dedupe_and_no_queued_left(monkeypatch, tmp_path):
    jobs = JOBS + [("7", "Full Stack Developer", "Acme", "Gurugram", "2-4 Yrs", JOBS[0][5])]   # same posting, new id
    m = fake_platform("naukri", jobs)
    con = setup(monkeypatch, tmp_path, {"naukri": m})
    scheduler.run(minutes=5, dry_run=True, only="naukri", cap=2)
    assert con.execute("SELECT COUNT(*) c FROM applications WHERE status='dry_run'").fetchone()["c"] == 2
    assert con.execute("SELECT COUNT(*) c FROM applications WHERE status='queued'").fetchone()["c"] == 0
    assert con.execute("SELECT COUNT(*) c FROM applications WHERE job_id='7'").fetchone()["c"] == 0


def test_company_cap(monkeypatch, tmp_path):
    jobs = [(str(i), f"Node JS Developer {i}", "SameCo", "Noida", "2-4 Yrs", "Node.js Express MongoDB JavaScript REST APIs") for i in range(5)]
    m = fake_platform("naukri", jobs)
    con = setup(monkeypatch, tmp_path, {"naukri": m})
    scheduler.run(minutes=5, dry_run=False, only="naukri")
    assert con.execute("SELECT COUNT(*) c FROM applications WHERE status='applied'").fetchone()["c"] == 1     # one per company, ever
    assert con.execute("SELECT COUNT(*) c FROM applications WHERE reason LIKE 'already applied to%'").fetchone()["c"] == 4


def test_instahyre_inline_no_upload(monkeypatch, tmp_path):
    m = fake_platform("instahyre")
    con = setup(monkeypatch, tmp_path, {"instahyre": m})
    scheduler.run(minutes=5, dry_run=False, only="instahyre")
    assert con.execute("SELECT COUNT(*) c FROM applications WHERE status='applied'").fetchone()["c"] >= 2
    assert con.execute("SELECT COUNT(*) c FROM applications WHERE status='queued'").fetchone()["c"] == 0


def test_skips_are_cached_and_company_checked_before_opening(monkeypatch, tmp_path):
    m = fake_platform("naukri")
    opened = []
    real_details = m.details
    m.details = lambda page, job: (opened.append(job["job_id"]), real_details(page, job))[1]
    con = setup(monkeypatch, tmp_path, {"naukri": m})
    scheduler.run(minutes=5, dry_run=False, only="naukri")
    first = list(opened)
    assert "3" not in first                                   # too senior: rejected from the card, page never opened
    opened.clear()
    scheduler.run(minutes=5, dry_run=False, only="naukri")
    assert opened == []                                       # nothing re-opened: applied / external / skipped all remembered
    # a rejected job is logged once, not once per run
    assert con.execute("SELECT COUNT(*) c FROM applications WHERE job_id='2'").fetchone()["c"] == 1


def test_same_company_new_job_not_opened(monkeypatch, tmp_path):
    jobs = [("1", "Node JS Developer", "SameCo", "Noida", "2-4 Yrs", "Node.js Express MongoDB JavaScript REST APIs"),
            ("2", "React Developer", "SameCo", "Noida", "2-4 Yrs", "React.js Next.js TypeScript")]
    m = fake_platform("naukri", jobs)
    opened = []
    real_details = m.details
    m.details = lambda page, job: (opened.append(job["job_id"]), real_details(page, job))[1]
    setup(monkeypatch, tmp_path, {"naukri": m})
    scheduler.run(minutes=5, dry_run=False, only="naukri")
    assert opened == ["1"]


def test_cooldown_only_after_a_real_click(monkeypatch, tmp_path):
    jobs = [("1", "Full Stack Developer", "A", "Noida", "2-4 Yrs", JOBS[0][5]),
            ("2", "Full Stack Developer", "Exty", "Noida", "2-4 Yrs", JOBS[0][5]),     # external: no click
            ("3", "Full Stack Developer", "C", "Noida", "2-4 Yrs", JOBS[0][5])]
    m = fake_platform("naukri", jobs)
    setup(monkeypatch, tmp_path, {"naukri": m})
    waits = []
    monkeypatch.setattr(time, "sleep", lambda s: waits.append(s))
    clock = [1000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    monkeypatch.setattr(scheduler, "random", types.SimpleNamespace(uniform=lambda a, b: 40.0))   # cooldown only
    scheduler.run(minutes=5, dry_run=False, only="naukri")
    assert 40.0 in waits                                  # job 3 waited for the cooldown started by job 1
    assert waits.count(40.0) == 1                         # job 2 (external) neither waited nor started a cooldown


def test_unused_minutes_go_to_next_platform(monkeypatch, tmp_path):
    a, b = fake_platform("naukri"), fake_platform("indeed")
    a.logged_in = lambda page: False
    setup(monkeypatch, tmp_path, {"naukri": a, "indeed": b})
    seen = {}
    real = scheduler.run_platform
    def spy(name, page, cfg, con, run_id, deadline, *rest):
        seen[name] = deadline - time.time()
        return real(name, page, cfg, con, run_id, deadline, *rest)
    monkeypatch.setattr(scheduler, "run_platform", spy)
    cfg = cc.load_config()
    cfg["platforms"] = {"naukri": {"enabled": True, "minutes": 30, "max_apply": 5},
                        "indeed": {"enabled": True, "minutes": 10, "max_apply": 5}}
    cfg["parallel"] = False
    monkeypatch.setattr(scheduler, "load_config", lambda: cfg)
    scheduler.run(minutes=60, dry_run=True)
    assert seen["indeed"] > 35 * 60                       # its own 10 min + naukri's unused 30


def test_platform_queries_used_and_rotated(monkeypatch, tmp_path):
    m = fake_platform("naukri")
    calls = []
    m.queries = lambda cfg: [("r", "Noida", 2), ("r", "Noida", 3)]
    m.search = lambda page, *q: calls.append(q) or []
    setup(monkeypatch, tmp_path, {"naukri": m})
    scheduler.run(minutes=5, dry_run=True, only="naukri")
    assert sorted(calls) == [("r", "Noida", 2), ("r", "Noida", 3)]


def test_parallel_platforms_share_one_application_per_company(monkeypatch, tmp_path):
    jobs = [("1", "Full Stack Developer", "SameCo", "Noida", "2-4 Yrs", JOBS[0][5]),
            ("2", "React Developer", "OtherCo", "Noida", "2-4 Yrs", JOBS[3][5])]
    a, b = fake_platform("naukri", jobs), fake_platform("indeed", [("9", "MERN Developer", "SameCo", "Noida", "2-4 Yrs", JOBS[0][5])])
    con = setup(monkeypatch, tmp_path, {"naukri": a, "indeed": b})
    cfg = cc.load_config()
    cfg["platforms"] = {"naukri": {"enabled": True, "minutes": 30, "max_apply": 5},
                        "indeed": {"enabled": True, "minutes": 30, "max_apply": 5}}
    cfg["parallel"] = True
    monkeypatch.setattr(scheduler, "load_config", lambda: cfg)
    scheduler.run(minutes=5, dry_run=False)
    rows = con.execute("SELECT platform, company, status FROM applications").fetchall()
    assert {r["platform"] for r in rows} == {"naukri", "indeed"}                       # both sites ran
    same = [r for r in rows if r["company"] == "SameCo" and r["status"] == "applied"]
    assert len(same) == 1                                                              # never twice to one company
    assert con.execute("SELECT COUNT(*) c FROM applications WHERE status='queued'").fetchone()["c"] == 0
