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
    from core.config import resume_file
    from platforms.base import session
    f = resume_file()
    P = importlib.import_module(f"platforms.{a.platform}")
    with session(a.platform) as page:
        if not P.logged_in(page):
            sys.exit(f"not logged in - run: python main.py login {a.platform}")
        ok = P.upload_profile_resume(page, f)
        print(f"{a.platform}: upload of {f.name} {'done' if ok else 'FAILED (upload it by hand on the site)'}")


def cmd_handoff(a):
    from core import db, handoff
    from core.config import resume_file
    f = resume_file()
    d = handoff.build(db.connect(), limit=a.limit, questions=a.questions)
    s = handoff.LAST_STATS
    print(f"Hand-off ready in {d}/\n  ANTIGRAVITY_BRIEF.md  <- give this to the agent\n  results.csv           <- the agent fills this in\n  (resume: {f.name})\n"
          f"This brief: {s['in_brief']} company-site jobs (best fit first)"
          + (f" + {s['questions']} that need answers" if a.questions else "") + f", {s['unconfirmed']} to verify.\n"
          f"Not in this brief: {s['later']} more company-site jobs for the next batch, {s['dropped']} dropped as poor fits under the current rules.\n"
          f"When the agent is done: python main.py import-results   then   python main.py handoff   for the next batch")


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


def cmd_rescore(a):
    """Re-judge every job skipped so far with the CURRENT rules (skills_bank.yaml, min_fit_score, matcher) - no browser.
    Shows how many earlier skips would now pass, so you can check a rule change before a live run."""
    from collections import Counter
    from core import db
    from core.config import load_config
    from core.matcher import evaluate, prescreen
    cfg, con = load_config(), db.connect()
    jobs = {(r["platform"], r["job_id"]): dict(r) for r in con.execute("SELECT * FROM jobs")}
    rows = con.execute("""SELECT a.* FROM applications a JOIN (SELECT platform, job_id, MAX(id) m FROM applications GROUP BY 1, 2) l
                          ON a.id = l.m WHERE a.status = 'skipped' AND a.reason NOT LIKE 'already applied%'
                          AND a.reason NOT LIKE 'not reached%'""").fetchall()
    now_pass, still = [], Counter()
    for r in rows:
        j = jobs.get((r["platform"], r["job_id"])) or {"title": r["title"], "exp_text": r["exp_text"] or "", "description": ""}
        j.setdefault("exp_text", r["exp_text"] or "")
        why = prescreen(j, cfg)
        if not why and j.get("description"):
            ev = evaluate(dict(j), cfg)
            why = "" if ev["ok"] else ev["reason"]
        elif not why:
            why = "(page never read - will be opened next run)"
        if why:
            still[why.split("(")[0].split(":")[0].split("<")[0].strip()] += 1
        else:
            now_pass.append(r)
    print(f"{len(rows)} jobs skipped so far.  Under the current rules {len(now_pass)} of them would now PASS.\n")
    for r in now_pass[:a.show]:
        print(f"  + [{r['platform']}] {r['title']} @ {r['company']}   (was: {(r['reason'] or '')[:70]})")
    print("\nStill skipped, by reason:")
    for k, v in still.most_common():
        print(f"  {v:5}  {k}")


def cmd_verify(a):
    """Settle 'unconfirmed' applications by reading each site's own Applied jobs list (read-only: nothing is clicked)."""
    from core import db, verify, excel
    from platforms.base import browser_host
    con = db.connect()
    names = [a.platform] if a.platform else ["naukri", "instahyre", "indeed"]
    if not any(verify.needs_verify(con, n) for n in names):
        print("Nothing unconfirmed or recently applied - nothing to verify.")
        return
    with browser_host():
        results = verify.run_verify(con, names, retry_missing=a.retry_missing)
    for res in results:
        print(f"\n{res['platform']}: the site lists {res['listed']} applied jobs")
        for r in res["confirmed"]:
            print(f"   confirmed    {r['title']} @ {r['company']}")
        for r in res["to_external"]:
            print(f"   company site {r['title']} @ {r['company']}   (moved to the Antigravity list)")
        for r in res["other_title"]:
            print(f"   other title  {r['title']} @ {r['company']}   (left unconfirmed)")
        for r in res["missing"]:
            print(f"   NOT LISTED   {r['title']} @ {r['company']}   " + ("(reset: will be retried)" if a.retry_missing else "(left unconfirmed)"))
    try:
        excel.export(con)
    except Exception as e:
        print(f"could not update Excel: {e}")


def cmd_sync_sheet(a):
    """Push applied jobs to the Google Sheet (daily tabs + a master tab). See 'Google Sheet' in README.md to set it up."""
    from core import db, sheets
    from core.config import load_config
    cfg = load_config()
    if not sheets.configured(cfg):
        sys.exit("google_sheet is not set up: set enabled: true, sheet_id and service_account_file in config.yaml (see README.md).")
    sheets.sync(db.connect(), cfg)


def cmd_coverletter(a):
    """Check a cover letter / email body the agent wrote: nothing is used unless this prints OK."""
    from core import coverletter
    text = Path(a.file).read_text()
    problems = coverletter.check(text)
    print(f"{len(text.split())} words")
    if problems:
        print("NOT OK:\n  - " + "\n  - ".join(problems))
        sys.exit(1)
    print("OK")


def cmd_email(a):
    """Apply by email. Without --send it only checks everything and shows what would go out."""
    from core import db, mailer
    res = mailer.send_application(db.connect(), a.job, a.to, Path(a.letter_file).read_text(), subject=a.subject,
                                  found_on=a.found_on or "", send=a.send)
    if not res["ok"]:
        print("NOT SENT:\n  - " + "\n  - ".join(res["problems"]))
        sys.exit(1)
    print(f"{'SENT' if res['sent'] else 'dry run OK (add --send to send)'}  to {a.to}\n  subject: {res['subject']}")
    if res["sent"]:
        from core import excel
        try:
            excel.export(db.connect())
        except Exception as e:
            print(f"could not update Excel: {e}")


def cmd_email_test(a):
    """Send one test mail to yourself to prove the SMTP login works."""
    import smtplib
    from core import mailer
    from core.config import load_config
    st = mailer.smtp_settings()
    if not (st["user"] and st["password"]):
        sys.exit("Set SMTP_USER and SMTP_PASSWORD in .env first (see .env.example).")
    msg = mailer.build_message(st["user"], "job-applier test mail", "This is a test from job-applier. If you can read this, "
                               "sending works. Nothing was sent to anyone else.", load_config(), None)
    with smtplib.SMTP(st["host"], st["port"], timeout=30) as s:
        s.starttls(); s.login(st["user"], st["password"]); s.send_message(msg)
    print(f"Test mail sent to {st['user']} - check that inbox.")


def cmd_questions(a):
    """Questions that sent jobs to needs_manual, most frequent first, as ready-to-fill config.yaml `answers:` entries."""
    import re
    from collections import Counter
    from core import db
    con = db.connect()
    qs, opts = Counter(), {}
    for r in con.execute("SELECT reason FROM applications WHERE status='needs_manual'"):
        m = re.match(r"(?:unanswerable question|multi-choice question needs you): (.*?)(?: \| options: (.*))?$", r["reason"] or "")
        if m:
            q = " ".join(m.group(1).split())
            qs[q] += 1
            if m.group(2):
                opts[q] = m.group(2)
    if not qs:
        print("No unanswered questions recorded yet.")
        return
    print("Questions that stopped an application (count). Add TRUE answers to config.yaml under `answers:`\n")
    for q, n in qs.most_common(a.top):
        print(f"  {n:3}  {q}" + (f"\n       options: {opts[q]}" if q in opts else ""))
    print("\nExample entry:\nanswers:\n  - match: \"" + re.escape(qs.most_common(1)[0][0][:40].lower()) + "\"\n    answer: \"...\"")


def cmd_probe(a):
    """Developer aid: open one page of a platform in your logged-in browser and save what it looks like (reads only)."""
    import importlib, json
    from core.config import OUT
    from platforms.base import session
    P = importlib.import_module(f"platforms.{a.platform}")
    out = OUT / "probe"
    out.mkdir(parents=True, exist_ok=True)
    with session(a.platform) as page:
        if not P.logged_in(page):
            sys.exit(f"not logged in - run: python main.py login {a.platform}")
        info = P.probe(page, a.url)
    (out / f"{a.platform}.html").write_text(info.pop("html"))
    (out / f"{a.platform}.json").write_text(json.dumps(info, indent=2, ensure_ascii=False))
    print(f"saved {out}/{a.platform}.json and .html (+ screenshot in logs/). cards found: {len(info['cards'])}")
    for c in info["cards"]:
        print("  ", c["id"], "|", c["title"], "|", c["lines"][:4])


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
    l.add_argument("platform", choices=["all", "naukri", "linkedin", "instahyre", "indeed", "foundit"]); l.set_defaults(f=cmd_login)
    pr = sub.add_parser("probe", help="save what one platform page really looks like (to fix selectors)")
    pr.add_argument("platform", choices=["foundit"]); pr.add_argument("--url"); pr.set_defaults(f=cmd_probe)
    r = sub.add_parser("run", help="run the applier")
    r.add_argument("--minutes", type=int, default=60); r.add_argument("--dry-run", action="store_true")
    r.add_argument("--only", choices=["naukri", "linkedin", "instahyre", "indeed", "foundit"]); r.add_argument("--cap", type=int, help="max applications per platform")
    r.set_defaults(f=cmd_run)
    p = sub.add_parser("report", help="rebuild the report"); p.add_argument("--run", default="last", help="last | all | <run id>")
    p.set_defaults(f=cmd_report)
    d = sub.add_parser("dashboard", help="live auto-refreshing report in your browser")
    d.add_argument("--port", type=int, default=8765); d.add_argument("--all", action="store_true", help="all runs, not just the latest")
    d.set_defaults(f=cmd_dashboard)
    u = sub.add_parser("upload-resume", help="put your main resume (unchanged) on a site profile")
    u.add_argument("platform", choices=["naukri", "instahyre", "indeed"]); u.set_defaults(f=cmd_upload_resume)
    h = sub.add_parser("handoff", help="write the brief (.md) for a browser agent (Antigravity): company-site jobs, best fit first")
    h.add_argument("--limit", type=int, default=15, help="jobs per brief (default 15)")
    h.add_argument("--questions", action="store_true", help="also include jobs that stopped on a question")
    h.set_defaults(f=cmd_handoff)
    i = sub.add_parser("import-results", help="bring the agent's results.csv back into the tracker")
    i.add_argument("file", nargs="?"); i.set_defaults(f=cmd_import)
    v = sub.add_parser("verify", help="settle 'unconfirmed' jobs from each site's own Applied jobs list (read-only)")
    v.add_argument("platform", nargs="?", choices=["naukri", "instahyre", "indeed"])
    v.add_argument("--retry-missing", action="store_true", help="jobs NOT on the Applied list are reset so the next run retries them")
    v.set_defaults(f=cmd_verify)
    sub.add_parser("sync-sheet", help="push applied jobs to the Google Sheet (daily tabs + a master tab)").set_defaults(f=cmd_sync_sheet)
    cl = sub.add_parser("coverletter", help="check a cover letter / email body against the resume facts")
    cl.add_argument("--file", required=True); cl.set_defaults(f=cmd_coverletter)
    em = sub.add_parser("email", help="apply by email to an address found on the company's own site (dry run unless --send)")
    em.add_argument("--job", required=True, help="platform:job_id, e.g. naukri:290426505797")
    em.add_argument("--to", required=True); em.add_argument("--letter-file", required=True)
    em.add_argument("--subject"); em.add_argument("--found-on", help="URL of the page where the address was found")
    em.add_argument("--send", action="store_true"); em.set_defaults(f=cmd_email)
    sub.add_parser("email-test", help="send one test mail to yourself (checks the SMTP login)").set_defaults(f=cmd_email_test)
    qq = sub.add_parser("questions", help="list the questions that sent jobs to needs_manual (to fill `answers:`)")
    qq.add_argument("--top", type=int, default=30); qq.set_defaults(f=cmd_questions)
    rs = sub.add_parser("rescore", help="re-judge earlier skipped jobs with the current rules (no browser)")
    rs.add_argument("--show", type=int, default=30, help="how many now-passing jobs to list"); rs.set_defaults(f=cmd_rescore)
    sub.add_parser("excel", help="rebuild Applied_Jobs.xlsx (applied jobs only)").set_defaults(f=cmd_excel)
    sub.add_parser("models", help="list Gemini models for your key + test call").set_defaults(f=cmd_models)
    sub.add_parser("demo-report", help="see a sample report with fake data").set_defaults(f=cmd_demo)
    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
