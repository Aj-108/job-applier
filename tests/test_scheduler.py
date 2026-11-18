import sys, time, types
from contextlib import contextmanager
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import db, scheduler
import core.config as cc

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
    return m


def setup(monkeypatch, tmp_path, mods):
    from core import excel
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
    assert rows["1"]["status"] == "applied" and rows["1"]["resume_path"].endswith("archit_sde.pdf")
    assert rows["6"]["status"] == "external"
    assert all(name == "archit_sde.pdf" for _, name in m.calls)           # the main resume, nothing else
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


def test_instahyre_two_phase_no_upload(monkeypatch, tmp_path):
    m = fake_platform("instahyre")
    m.refreshed = []
    m.refresh = lambda page: m.refreshed.append(1)
    con = setup(monkeypatch, tmp_path, {"instahyre": m})
    scheduler.run(minutes=5, dry_run=False, only="instahyre")
    assert con.execute("SELECT COUNT(*) c FROM applications WHERE status='applied'").fetchone()["c"] >= 2
    assert len(m.refreshed) >= 2                                   # went back to the list before each apply
    assert con.execute("SELECT COUNT(*) c FROM applications WHERE status='queued'").fetchone()["c"] == 0
