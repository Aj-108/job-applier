"""The 1-hour loop: for each platform -> search -> read JD -> fit check -> apply (always with the main resume) -> log -> report."""
import importlib
import random
import threading
import time
import traceback
from collections import Counter
from . import db, report, excel
from .config import load_config, resume_file, rules_fingerprint
from .matcher import evaluate, prescreen
from platforms.base import session, browser_host, Blocked

# platforms run in parallel threads: the company check + the Apply click + the DB / Excel write happen under this lock,
# so two sites can never apply to the same company at the same moment (one application per company, ever)
APPLY_LOCK = threading.Lock()

# outcomes where the Apply button was really clicked -> start the anti-spam cooldown before the next click
CLICKED = ("applied", "unconfirmed", "needs_manual")
PROFILE_KEYS = ("current_ctc_lpa", "expected_ctc_lpa", "notice_period_days", "current_location")


def _sync_excel(con, write=False):
    """Never let an Excel problem (file open, locked, corrupt) break a run."""
    try:
        excel.export(con) if write else excel.ingest(con)
    except Exception as e:
        print(f"  [excel] could not {'update' if write else 'read'} {excel.excel_path().name}: {e}")


def _load(name):
    return importlib.import_module(f"platforms.{name}")


def _search_queries(P, cfg, run_id):
    """The platform's own search list (Naukri: role x location x experience) or role x location.
    Rotated by run so the searches at the end of the list (e.g. the last city) are also reached in some runs."""
    if hasattr(P, "queries"):
        qs = list(P.queries(cfg))
    else:
        qs = [(role, loc) for loc in cfg["locations"] for role in cfg["roles"]]
    if not qs:
        return []
    k = (run_id or 0) % len(qs)
    return qs[k:] + qs[:k]


def run_platform(name, page, cfg, con, run_id, deadline, cap, dry_run, state):
    P = _load(name)
    pc = cfg["platforms"][name]
    cap = min(cap or pc["max_apply"], pc["max_apply"])
    main_resume = resume_file(cfg)                    # THE resume. Never edited, never converted, never replaced.
    rules = rules_fingerprint(cfg)
    seen, seen_keys = set(), set()
    done = 0
    stats = Counter()
    per_company = cfg.get("max_per_company", 1)

    def skip(job, reason, score=None, log=True):
        """A decision that will not change until the rules do: log it once, never re-open this job under these rules."""
        if log:
            db.log_application(con, run_id, job, "skipped", reason, score)
        db.remember_skip(con, name, job["job_id"], rules, reason)

    def cooldown():
        """Wait out what is left of the gap since the last real Apply click. Reading / scoring other jobs in between
        already counted towards it, so this is usually short."""
        left = min(state.get("next_click", 0), deadline) - time.time()
        if left > 0 and not dry_run:
            time.sleep(left)

    def apply_one(job, pdf, navigate):
        """Apply to one job. Returns True if it counts toward the cap (applied / dry_run / unconfirmed)."""
        matched = job["_skills"]["core"] + job["_skills"]["adjacent"]
        status, reason = None, ""
        try:
            if navigate:
                P.details(page, job)                      # make sure we are on the job page
            pre = P.precheck(page, job) if hasattr(P, "precheck") else None
            if not pre:
                cooldown()                                # outside the lock: other sites keep working meanwhile
            with APPLY_LOCK:
                _sync_excel(con)                          # pick up anything the browser agent added meanwhile
                if db.company_applied_count(con, job["company"]) >= per_company:
                    status, reason = "skipped", "already applied to this company (one application per company)"
                elif pre:                                 # company site / already applied: no click
                    status, reason = pre
                else:
                    status, reason = P.apply(page, job, pdf, dry_run)
                db.update_application(con, job["_row"], status, reason or "", matched, str(pdf or ""))
                if status == "applied":
                    _sync_excel(con, write=True)
        except Blocked:
            raise
        except Exception as e:
            status, reason = "failed", f"{type(e).__name__}: {e}"
            traceback.print_exc()
            db.update_application(con, job["_row"], status, reason, matched, str(pdf or ""))
        stats[status] += 1
        print(f"  [{name}] {status:<12} {job['title']} @ {job['company']} {('- ' + reason[:110]) if reason else ''}")
        if status in CLICKED:                             # only a real click needs the pause; external / skipped do not
            state["next_click"] = time.time() + random.uniform(*cfg["pacing"]["apply_delay"])
        return status in ("applied", "dry_run", "unconfirmed")

    # search -> read job -> fit check -> apply right away (we are on the job page / popup)
    stop = False
    halt = state.get("stop") or threading.Event()           # set by Ctrl+C in parallel mode
    for q in _search_queries(P, cfg, run_id):
        if stop or time.time() > deadline or done >= cap or halt.is_set():
            break
        try:
            stubs = P.search(page, *q)
            stats["searches"] += 1
        except Blocked:
            raise
        except Exception as e:
            print(f"  [{name}] search failed ({' / '.join(map(str, q))}): {str(e)[:100]}")
            continue
        for job in stubs:
            if time.time() > deadline or done >= cap or halt.is_set():
                stop = True
                break
            if job["job_id"] in seen:
                continue
            seen.add(job["job_id"])
            stats["cards"] += 1
            if db.already_handled(con, name, job["job_id"]):
                stats["handled before"] += 1
                continue
            key = (job["company"].lower().strip(), job["title"].lower().strip(), job["location"].lower().strip())
            if key in seen_keys:                                  # same company reposted the same job under a new id
                continue
            seen_keys.add(key)
            if db.hard_skipped_before(con, name, job["job_id"], rules):   # judged unsuitable before, rules unchanged
                stats["skipped before"] += 1
                continue
            why = prescreen(job, cfg)                               # title/experience from the card: no page load needed
            if why:
                skip(job, why)
                stats["skip: card"] += 1
                continue
            if db.company_applied_count(con, job["company"]) >= per_company:   # known from the card: do not open the page
                skip(job, "already applied to this company (one application per company)")
                stats["skip: company done"] += 1
                continue
            known = P.card_outcome(job) if hasattr(P, "card_outcome") else None
            if known:                                            # e.g. Indeed company-site job: hand-off, no page load
                db.log_application(con, run_id, job, known[0], known[1])
                stats[known[0]] += 1
                continue
            try:
                job = P.details(page, job)
                stats["opened"] += 1
            except Blocked:
                raise
            except Exception as e:
                db.log_application(con, run_id, job, "failed", f"could not read job: {str(e)[:100]}")
                continue
            db.save_job(con, job)
            ev = evaluate(job, cfg)
            if not ev["ok"]:
                skip(job, ev["reason"], ev["score"])
                stats["skip: fit"] += 1
                continue
            job["_fit"], job["_skills"] = ev["score"], ev["skills"]
            job["_row"] = db.log_application(con, run_id, job, "queued", "passed fit check, waiting to apply", ev["score"],
                                             ev["skills"]["core"] + ev["skills"]["adjacent"])
            done += apply_one(job, main_resume, navigate=False)      # we are already on this job's page
    print(f"  [{name}] funnel: " + ", ".join(f"{k} {v}" for k, v in stats.items()))
    return done


def _profile_gaps(cfg) -> list[str]:
    p = cfg.get("profile", {})
    return [k for k in PROFILE_KEYS if p.get(k) in (None, "", "CHANGE_ME")]


def run(minutes=None, dry_run=False, only=None, cap=None):
    cfg = load_config()
    minutes = minutes or cfg["run_minutes"]
    con = db.connect()
    run_id = db.start_run(con, dry_run)
    t_end = time.time() + minutes * 60
    _sync_excel(con)                                          # load companies the agent applied to since the last run
    state = {"cost": 0.0}
    notes = []
    resume = resume_file(cfg)
    if not resume.exists():                # fail early if the resume is missing
        raise SystemExit(f"Resume not found: {resume}")
    gaps = _profile_gaps(cfg)
    if gaps and not dry_run:
        print(f"WARNING: config.yaml profile has no value for {', '.join(gaps)} - "
              f"jobs that ask about them go to 'needs_manual'.")
    names = [n for n, pc in cfg["platforms"].items() if pc["enabled"] and (not only or n == only)]
    parallel = bool(cfg.get("parallel", True)) and len(names) > 1
    stop = threading.Event()

    def one(name, deadline, own_db):
        """Run one platform until its deadline / cap. own_db: a separate DB connection (needed in a thread)."""
        c = db.connect() if own_db else con
        pstate = {"cost": 0.0, "stop": stop}                # per site: its own Apply-click cooldown
        try:
            with session(name) as page:
                if not _load(name).logged_in(page):
                    msg = f"{name}: not logged in - run `python main.py login {name}`"
                    print("  " + msg)
                    notes.append(msg)
                    return
                n = run_platform(name, page, cfg, c, run_id, deadline, cap, dry_run, pstate)
                print(f"  [{name}] done: {n} {'dry-run' if dry_run else 'applied'}")
        except Blocked as b:
            msg = f"{name}: stopped early - platform showed '{b}'"
            print("  " + msg)
            notes.append(msg)
        except Exception as e:
            msg = f"{name}: crashed - {type(e).__name__}: {e}"
            print("  " + msg)
            notes.append(msg)
        finally:
            if own_db:
                c.close()

    try:
        with browser_host():                            # one Chrome for the whole run; each platform = one tab
            if parallel:
                print(f"\n=== {', '.join(n.upper() for n in names)} in parallel ({'dry run' if dry_run else 'LIVE'}) - "
                      f"{minutes} min; caps " + ", ".join(f"{n} {cfg['platforms'][n]['max_apply']}" for n in names) + " ===")
                threads = [threading.Thread(target=one, args=(n, t_end, True), name=n, daemon=True) for n in names]
                for t in threads:
                    t.start()
                    time.sleep(3)                           # do not open all sites in the same second
                try:
                    while any(t.is_alive() for t in threads):
                        for t in threads:
                            t.join(timeout=1)
                except KeyboardInterrupt:
                    stop.set()
                    notes.append("interrupted by user")
                    print("\nStopping after the current job on each site...")
                    for t in threads:
                        t.join(timeout=60)
            else:
                carry = 0.0                 # minutes a platform did not use (finished early / not logged in) go to the next
                for name in names:
                    if time.time() > t_end:
                        break
                    pc = cfg["platforms"][name]
                    t0 = time.time()
                    deadline = min(t_end, t0 + pc["minutes"] * 60 + carry)
                    print(f"\n=== {name.upper()} ({'dry run' if dry_run else 'LIVE'}) - up to {(deadline - t0) / 60:.0f} min / "
                          f"{pc['max_apply']} applications ===")
                    one(name, deadline, False)
                    carry = max(0.0, deadline - time.time())
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
