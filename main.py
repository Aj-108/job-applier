#!/usr/bin/env python
"""Job applier CLI.   python main.py <login|tailor|run|report|demo-report> ..."""
import os
import sys
from pathlib import Path

# Always run inside the project's .venv (where the packages are installed), even if it was not activated.
_VENV_PY = Path(__file__).resolve().parent / ".venv" / "bin" / "python"
if _VENV_PY.exists() and Path(sys.prefix).resolve() != _VENV_PY.parent.parent.resolve():
    os.execv(str(_VENV_PY), [str(_VENV_PY), *sys.argv])

import argparse


def cmd_tailor(a):
    from core.pipeline import build_resume, _orig
    from core.jd_parser import extract_skills
    import subprocess
    from core.config import ai_settings as claude_settings
    jd = subprocess.run(["pbpaste"], capture_output=True, text=True).stdout if a.clipboard else Path(a.jd).read_text()
    if not jd.strip():
        sys.exit("Job description is empty (copy the job text first, or pass --jd file.txt)")
    cs = claude_settings()
    print(f"AI: provider={cs['provider']} model={cs['model']} effort/thinking={cs['effort']} api_key={'set' if cs['has_key'] else 'NOT SET (rules-based tailoring)'}")
    r = build_resume(jd, a.name, use_claude=(False if a.no_claude else None))
    o, t = _orig()["orig"], r["tailored"]
    print(f"engine: {r['engine']}   cost: Rs {r['cost_inr']:.2f}   notes: {r['notes'] or '-'}")
    print(f"JD skills found: {extract_skills(jd)}")
    print(f"\nHEADLINE\n  old: {o['headline']}\n  new: {t['headline']}")
    print(f"\nSUMMARY\n  old: {o['summary']}\n  new: {t['summary']}")
    for cat in o["skills"]:
        if o["skills"][cat] != t["skills"][cat]:
            print(f"\nSKILLS/{cat}\n  old: {', '.join(o['skills'][cat])}\n  new: {', '.join(t['skills'][cat])}")
    for pid, txt in t["bullets"].items():
        print(f"\nBULLET {pid}\n  old: {o['bullets'][int(pid)]}\n  new: {txt}")
    print(f"\nfiles: {r['docx']}  {r['pdf']}")
    if a.clipboard and r["pdf"]:
        subprocess.run(["open", "-R", str(r["pdf"])])          # reveal the PDF in Finder, ready to upload by hand


def cmd_models(a):
    """List the Gemini models your key can use (pick one for GEMINI_MODEL in .env) and run a tiny test call."""
    import os
    from core.config import ai_settings
    from google import genai
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY is empty in .env")
    c = genai.Client(api_key=key)
    names = sorted(m.name.replace("models/", "") for m in c.models.list() if "generateContent" in (m.supported_actions or []))
    print("Models that support generateContent:\n  " + "\n  ".join(names))
    m = ai_settings()["model"] if ai_settings()["provider"] == "gemini" else "gemini-flash-latest"
    try:
        r = c.models.generate_content(model=m, contents="Reply with the single word: ok")
        print(f"\nTest call with GEMINI_MODEL={m}: {r.text.strip()!r}  -> works")
    except Exception as e:
        print(f"\nTest call with GEMINI_MODEL={m} FAILED: {e}\nPick a model from the list above and set GEMINI_MODEL in .env")


def cmd_dashboard(a):
    """Live report in the browser: rebuilt from the database on every refresh (auto-refreshes every 10 s)."""
    import http.server, socketserver, threading, webbrowser
    from core import db, report

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                self._get()
            except (BrokenPipeError, ConnectionResetError):
                pass                                  # browser closed / refreshed mid-response: nothing to do

        def _get(self):
            if self.path.endswith("favicon.ico"):
                self.send_response(204); self.end_headers(); return
            if self.path.endswith("applications.csv"):
                report.build(db.connect(), None if a.all else db.connect().execute("SELECT MAX(id) m FROM runs").fetchone()["m"])
                body, ctype = (report.OUT / "applications.csv").read_bytes(), "text/csv"
            else:
                con = db.connect()
                rid = None if a.all else con.execute("SELECT MAX(id) m FROM runs").fetchone()["m"]
                page = report.render_html(report.gather(con, rid), "Job Applier - live")
                page = page.replace("<head>", "<head><meta http-equiv='refresh' content='10'>", 1)
                body, ctype = page.encode(), "text/html; charset=utf-8"
            self.send_response(200); self.send_header("Content-Type", ctype); self.end_headers(); self.wfile.write(body)

        def log_message(self, *x):
            pass

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    socketserver.ThreadingTCPServer.daemon_threads = True
    with socketserver.ThreadingTCPServer(("127.0.0.1", a.port), H) as srv:
        url = f"http://127.0.0.1:{a.port}"
        print(f"Live report: {url}   (Ctrl+C to stop; the applier keeps running in its own terminal)")
        threading.Timer(1, lambda: webbrowser.open(url)).start()
        srv.serve_forever()


def cmd_upload_resume(a):
    """Upload your MAIN resume (archit_sde.pdf, unchanged) to a site profile."""
    import importlib
    from core.config import load_config, ROOT
    from platforms.base import session
    cfg = load_config()
    f = ROOT / (cfg.get("resume_pdf") or cfg.get("resume_docx") or "archit_sde.pdf")
    P = importlib.import_module(f"platforms.{a.platform}")
    with session(a.platform) as page:
        if not P.logged_in(page):
            sys.exit(f"not logged in - run: python main.py login {a.platform}")
        ok = P.upload_profile_resume(page, f)
        print(f"{a.platform}: upload of {f.name} {'done' if ok else 'FAILED (upload it by hand on the site)'}")


def cmd_handoff(a):
    from core import db, handoff
    d = handoff.build(db.connect())
    g = handoff.pending_jobs(db.connect())
    print(f"Hand-off ready in {d}/\n  ANTIGRAVITY_BRIEF.md  <- give this to the agent\n  results.csv           <- the agent fills this in\n  (resume: {f.name})\n"
          f"Jobs: {len(g['external'])} company-site, {len(g['needs_manual'])} need answers, {len(g['unconfirmed'])} to verify.\n"
          f"When the agent is done: python main.py import-results")


def cmd_import(a):
    from core import db, handoff, report
    con = db.connect()
    n = handoff.import_results(con, Path(a.file) if a.file else None)
    print("imported:", n)
    print(report.terminal_summary(report.build(con, None)))


def cmd_excel(a):
    """Rebuild Applied_Jobs.xlsx (first loads any rows the browser agent added by hand)."""
    from core import db, excel
    con = db.connect()
    added = excel.ingest(con)
    p = excel.export(con)
    n = con.execute("SELECT COUNT(*) c FROM applications WHERE status='applied'").fetchone()["c"]
    print(f"{p}\n  {n} applied jobs listed" + (f"  ({added} added by the browser agent / by hand were loaded first)" if added else ""))


def cmd_login(a):
    from platforms.base import interactive_login
    interactive_login(a.platform)


def cmd_run(a):
    from core.config import ai_settings as claude_settings
    cs = claude_settings()
    print(f"AI: provider={cs['provider']} model={cs['model']} effort/thinking={cs['effort']} api_key={'set' if cs['has_key'] else 'NOT SET (rules-based tailoring)'}")
    from core.scheduler import run
    run(minutes=a.minutes, dry_run=a.dry_run, only=a.only, cap=a.cap)


def cmd_report(a):
    from core import db, report
    con = db.connect()
    rid = None
    if a.run == "last":
        r = con.execute("SELECT MAX(id) m FROM runs").fetchone()["m"]
        rid = r
    elif a.run.isdigit():
        rid = int(a.run)
    g = report.build(con, rid)
    print(report.terminal_summary(g))
    print("\nHTML: out/report.html\nCSV : out/applications.csv")


def cmd_demo(a):
    """Fill a throw-away DB with fake rows so you can see what the report looks like."""
    import random, tempfile, time
    from core import db, report
    from core.config import OUT
    con = db.connect(Path(tempfile.mkdtemp()) / "demo.db")
    rid = db.start_run(con, False)
    locs = ["Gurugram", "Noida", "Delhi", "Remote", "Mohali", "Chandigarh", "Pune", "Bengaluru"]
    for i in range(60):
        p = random.choice(["naukri", "naukri", "instahyre", "linkedin"])
        s = random.choices(["applied", "skipped", "external", "needs_manual", "failed"], [60, 25, 7, 6, 2])[0]
        loc = random.choice(locs)
        j = {"platform": p, "job_id": str(i), "title": random.choice(["Full Stack Developer", "React Developer", "MERN Developer"]),
             "company": f"Company {random.randint(1, 30)}", "location": loc, "remote": loc == "Remote",
             "url": "https://example.com", "exp_text": "2-4 Yrs"}
        db.log_application(con, rid, j, s, "fit score 40 < 55" if s == "skipped" else "", random.randint(55, 95),
                           random.sample(["React.js", "Node.js", "MongoDB", "TypeScript", "Next.js", "REST APIs"], 3), "")
        con.execute("UPDATE applications SET ts=? WHERE id=(SELECT MAX(id) FROM applications)", (time.time() + i * 55,))
    db.end_run(con, rid, 187.5)
    g = report.build(con, rid)
    print(report.terminal_summary(g))
    print(f"\nDemo report written to {OUT / 'report.html'}  (fake data - run `report` after a real run to overwrite)")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tailor", help="tailor the resume to one job description (offline test)")
    t.add_argument("--jd"); t.add_argument("--clipboard", action="store_true", help="read the job description from the clipboard"); t.add_argument("--name", default="test"); t.add_argument("--no-claude", action="store_true")
    t.set_defaults(f=cmd_tailor)
    l = sub.add_parser("login", help="log in to a platform once, by hand, in a persistent browser profile")
    l.add_argument("platform", choices=["naukri", "linkedin", "instahyre"]); l.set_defaults(f=cmd_login)
    r = sub.add_parser("run", help="run the applier")
    r.add_argument("--minutes", type=int, default=60); r.add_argument("--dry-run", action="store_true")
    r.add_argument("--only", choices=["naukri", "linkedin", "instahyre"]); r.add_argument("--cap", type=int, help="max applications per platform")
    r.set_defaults(f=cmd_run)
    p = sub.add_parser("report", help="rebuild the report"); p.add_argument("--run", default="last", help="last | all | <run id>")
    p.set_defaults(f=cmd_report)
    d = sub.add_parser("dashboard", help="live auto-refreshing report in your browser")
    d.add_argument("--port", type=int, default=8765); d.add_argument("--all", action="store_true", help="all runs, not just the latest")
    d.set_defaults(f=cmd_dashboard)
    u = sub.add_parser("upload-resume", help="put your main resume (unchanged) on a site profile")
    u.add_argument("platform", choices=["naukri", "instahyre"]); u.set_defaults(f=cmd_upload_resume)
    sub.add_parser("handoff", help="prepare company-site / needs-answer jobs for a browser agent (Antigravity)").set_defaults(f=cmd_handoff)
    i = sub.add_parser("import-results", help="bring the agent's results.csv back into the tracker")
    i.add_argument("file", nargs="?"); i.set_defaults(f=cmd_import)
    sub.add_parser("excel", help="rebuild Applied_Jobs.xlsx (applied jobs only)").set_defaults(f=cmd_excel)
    sub.add_parser("models", help="list Gemini models for your key + test call").set_defaults(f=cmd_models)
    sub.add_parser("demo-report", help="see a sample report with fake data").set_defaults(f=cmd_demo)
    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
