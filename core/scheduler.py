"""The 1-hour loop: for each platform -> search -> read JD -> fit check -> apply (always with the main resume) -> log -> report."""
import importlib
import time
import traceback
from . import db, report, excel
from .config import load_config, ROOT
from .matcher import evaluate, prescreen
from platforms.base import session, human_sleep, Blocked

def _sync_excel(con, write=False):
    """Never let an Excel problem (file open, locked, corrupt) break a run."""
    try:
        excel.export(con) if write else excel.ingest(con)
    except Exception as e:
        print(f"  [excel] could not {'update' if write else 'read'} {excel.excel_path().name}: {e}")


def _load(name):
    return importlib.import_module(f"platforms.{name}")


def _search_queries(cfg):
    for loc in cfg["locations"]:
        for role in cfg["roles"]:
            yield role, loc


def run_platform(name, page, cfg, con, run_id, deadline, cap, dry_run, state):
    P = _load(name)
    pc = cfg["platforms"][name]
    cap = min(cap or pc["max_apply"], pc["max_apply"])
    main_resume = ROOT / (cfg.get("resume_pdf") or cfg.get("resume_docx") or "archit_sde.pdf")           # THE resume. Never edited, never converted, never replaced.
    inline = name != "instahyre"                      # apply right after reading each job - no waiting phase
    seen, seen_keys, candidates = set(), set(), []
    t_collect = deadline if inline else time.time() + max(60, (deadline - time.time()) * 0.4)
    done = 0

    def apply_one(job, pdf, navigate):
        """Apply to one job. Returns True if it counts toward the cap (applied / dry_run / unconfirmed)."""
        _sync_excel(con)                                  # pick up anything the browser agent added meanwhile
        if db.company_applied_count(con, job["company"]) >= cfg.get("max_per_company", 1):
            db.update_application(con, job["_row"], "skipped", "already applied to this company (one application per company)")
            return False
        matched = job["_skills"]["core"] + job["_skills"]["adjacent"]
        note = ""
        try:
            if navigate:
                P.details(page, job)                      # make sure we are on the job page
            if hasattr(P, "refresh"):
                P.refresh(page)                           # instahyre: go back to the opportunities list first
            status, reason = P.apply(page, job, pdf, dry_run)
        except Blocked:
            raise
        except Exception as e:
            status, reason = "failed", f"{type(e).__name__}: {e}"
            traceback.print_exc()
        db.update_application(con, job["_row"], status, reason or note, matched, str(pdf or ""))
        print(f"  [{name}] {status:<12} {job['title']} @ {job['company']} {('- ' + reason[:110]) if reason else ''}")
        if status == "applied":
            _sync_excel(con, write=True)
        human_sleep(cfg["pacing"]["apply_delay"], cfg)
        return status in ("applied", "dry_run", "unconfirmed")

    # ---- phase A: search -> read job -> fit check (-> apply right away when nothing needs to be prepared first)
    stop = False
    for role, loc in _search_queries(cfg):
        if stop or time.time() > t_collect or (not inline and len(candidates) >= cap * 2):
            break
        try:
            stubs = P.search(page, role, loc)
        except Blocked:
            raise
        except Exception as e:
            print(f"  [{name}] search failed ({role} / {loc}): {str(e)[:100]}")
            continue
        for job in stubs:
            if time.time() > t_collect or (not inline and len(candidates) >= cap * 2) or (inline and done >= cap):
                stop = True
                break
            if job["job_id"] in seen:
                continue
            seen.add(job["job_id"])
            if db.already_handled(con, name, job["job_id"]):
                continue
            key = (job["company"].lower().strip(), job["title"].lower().strip(), job["location"].lower().strip())
            if key in seen_keys:                                  # same company reposted the same job under a new id
                continue
            seen_keys.add(key)
            if db.hard_skipped_before(con, name, job["job_id"]):    # already judged unsuitable in an earlier run
                continue
            why = prescreen(job, cfg)                               # title/experience from the card: no page load needed
            if why:
                db.log_application(con, run_id, job, "skipped", why)
                continue
            try:
                job = P.details(page, job)
            except Blocked:
                raise
            except Exception as e:
                db.log_application(con, run_id, job, "failed", f"could not read job: {str(e)[:100]}")
                continue
            db.save_job(con, job)
            ev = evaluate(job, cfg)
            if not ev["ok"]:
                db.log_application(con, run_id, job, "skipped", ev["reason"], ev["score"])
                continue
            job["_fit"], job["_skills"] = ev["score"], ev["skills"]
            job["_row"] = db.log_application(con, run_id, job, "queued", "passed fit check, waiting to apply", ev["score"],
                                             ev["skills"]["core"] + ev["skills"]["adjacent"])
            if inline:
                done += apply_one(job, main_resume, navigate=False)      # we are already on this job's page
            else:
                candidates.append(job)
                print(f"  [{name}] candidate: {job['title']} @ {job['company']} ({job['location']}) fit {ev['score']:.0f}")
                human_sleep((1, 3), cfg)
    candidates.sort(key=lambda j: -j["_fit"])

    # ---- phase B: apply to the queue (instahyre)
    for job in candidates:
        if time.time() > deadline or done >= cap:
            break
        done += apply_one(job, main_resume, navigate=False)
    return done


def run(minutes=None, dry_run=False, only=None, cap=None):
    cfg = load_config()
    minutes = minutes or cfg["run_minutes"]
    con = db.connect()
    run_id = db.start_run(con, dry_run)
    t_end = time.time() + minutes * 60
    _sync_excel(con)                                          # load companies the agent applied to since the last run
    state = {"cost": 0.0}
    notes = []
    resume_file = ROOT / (cfg.get("resume_pdf") or cfg.get("resume_docx") or "archit_sde.pdf")
    if not resume_file.exists():           # fail early if the resume is missing
        raise SystemExit(f"Resume not found: {resume_file}")
    try:
        for name, pc in cfg["platforms"].items():
            if only and name != only or not pc["enabled"] or time.time() > t_end:
                continue
            deadline = min(t_end, time.time() + pc["minutes"] * 60)
            print(f"\n=== {name.upper()} ({'dry run' if dry_run else 'LIVE'}) - up to {pc['minutes']} min / {pc['max_apply']} applications ===")
            try:
                with session(name) as page:
                    if not _load(name).logged_in(page):
                        msg = f"{name}: not logged in - run `python main.py login {name}`"
                        print("  " + msg)
                        notes.append(msg)
                        continue
                    n = run_platform(name, page, cfg, con, run_id, deadline, cap, dry_run, state)
                    print(f"  [{name}] done: {n} {'dry-run' if dry_run else 'applied'}")
            except Blocked as b:
                msg = f"{name}: stopped early - platform showed '{b}'"
                print("  " + msg)
                notes.append(msg)
            except Exception as e:
                msg = f"{name}: crashed - {type(e).__name__}: {e}"
                print("  " + msg)
                notes.append(msg)
    except KeyboardInterrupt:
        notes.append("interrupted by user")
    finally:
        db.finalize_queued(con, run_id)
        _sync_excel(con, write=True)
        db.end_run(con, run_id, state["cost"], " | ".join(notes))
        g = report.build(con, run_id)
        print(report.terminal_summary(g))
        for n in notes:
            print("NOTE:", n)
        print("\nHTML report: out/report.html   |   CSV: out/applications.csv   |   rebuild anytime: python main.py report")
