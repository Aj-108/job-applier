"""Hand the jobs this tool cannot finish itself (company-site applications, odd questions) to a browser agent
(e.g. Antigravity) and bring the outcomes back into the database/report."""
import csv
import json
import shutil
import time
from pathlib import Path
from . import coverletter, db, excel
from .companies import norm_company
from .config import OUT, load_config, load_skills, resume_file, never_claim
from .resume_writer import to_pdf

HANDOFF = OUT / "handoff"
RESULT_STATUS = {"applied": "applied", "submitted": "applied", "needs_user": "needs_manual", "needs_manual": "needs_manual",
                 "skipped": "skipped", "failed": "failed"}


def _pdf_of(docx: Path) -> Path | None:
    """PDF rendering of the SAME file (only for sites that refuse .docx). Same name, written next to the hand-off files."""
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t) / docx.name
        shutil.copy(docx, tmp)
        out = to_pdf(tmp)
        if not out:
            return None
        dest = HANDOFF / docx.with_suffix(".pdf").name
        shutil.copy(out, dest)
        return dest


def pending_jobs(con) -> dict:
    """Latest state per job: external / needs_manual / unconfirmed that nothing later resolved; max 1 job per company."""
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM applications WHERE status IN ('external','needs_manual','unconfirmed') ORDER BY ts DESC")]
    done = {(r["platform"], r["job_id"]) for r in con.execute("SELECT platform, job_id FROM applications WHERE status='applied'")}
    applied_cos = {norm_company(r["company"]) for r in con.execute(
        "SELECT company FROM applications WHERE status IN ('applied','unconfirmed') OR reason LIKE 'could not confirm%'")}
    seen_job, seen_co, groups = set(), set(), {"external": [], "needs_manual": [], "unconfirmed": []}
    for r in rows:
        k = (r["platform"], r["job_id"])
        if k in done or k in seen_job:
            continue
        seen_job.add(k)
        if r["status"] != "unconfirmed":
            if norm_company(r["company"]) in applied_cos:            # already applied to this company: never twice
                continue
            co = norm_company(r["company"])
            if co in seen_co:
                continue
            seen_co.add(co)
        groups[r["status"]].append(r)
    return groups


LAST_STATS: dict = {}          # filled by build(): how many jobs were kept / dropped / left for later batches


def _rank_external(con, cfg, jobs: list[dict]) -> tuple[list[dict], int]:
    """Re-judge company-site jobs with the CURRENT rules (many were logged under older, looser ones) and order them:
    roles whose experience range contains my experience first, then best fit score. -> (kept, dropped_count)."""
    from .jd_parser import parse_experience
    from .matcher import evaluate, prescreen
    yrs = float(cfg["profile"].get("total_experience_years") or 0)
    kept, dropped = [], 0
    for r in jobs:
        job = {"title": r["title"] or "", "company": r["company"] or "", "exp_text": r["exp_text"] or ""}
        if prescreen(job, cfg):
            dropped += 1
            continue
        jr = con.execute("SELECT description FROM jobs WHERE platform=? AND job_id=?", (r["platform"], r["job_id"])).fetchone()
        score = r["fit_score"] or 0
        if jr and jr["description"]:
            ev = evaluate({**job, "description": jr["description"]}, cfg)
            if not ev["ok"]:
                dropped += 1
                continue
            score = ev["score"]
        lo, hi = parse_experience(job["exp_text"])
        in_range = (lo is None or lo <= yrs + 0.5) and (hi is None or hi >= yrs - 0.5)      # "0-1 Yrs" is not for 2.8 years
        kept.append((not in_range, -score, r))
    kept.sort(key=lambda t: (t[0], t[1]))
    return [t[2] for t in kept], dropped


def _facts(cfg) -> str:
    p = cfg["profile"]
    sy = ", ".join(f"{k}: {v}" for k, v in p["skill_years"].items())
    return "\n".join([
        f"- Full name: {p['full_name']}", f"- Email: {p['email']}", f"- Phone: {p['phone']}",
        f"- Total experience: {p['total_experience_years']} years (Software Developer, 75Ways Technologies, Jan 2024 - present)",
        f"- Education: B.Tech Computer Science, College of Engineering Roorkee, 2020-2024",
        f"- Current CTC: {p['current_ctc_lpa']} LPA   |   Expected CTC: {p['expected_ctc_lpa']} LPA",
        f"- Notice period: {p['notice_period_days']} days", f"- Current location: {p['current_location']}",
        f"- Open to relocate: {'yes' if p['willing_to_relocate'] else 'no'}   |   Authorised to work in India: {'yes' if p['work_authorized_india'] else 'no'}   |   Needs visa sponsorship: no",
        f"- Years of experience per skill: {sy}",
        "- Links: github.com/Aj-108 , linkedin.com/in/archit108"])


def _prefs(cfg) -> str:
    pr, a = cfg.get("preferences") or {}, cfg.get("answers") or []
    unit = {"rupees": "annual, in full rupees (current 780000 / expected 1200000 style)", "lpa": "annual, in LPA", "ask": "do not guess: stop"}
    lines = [f"- Salary field with NO unit: {unit.get(str(pr.get('salary_unit_default', 'rupees')).lower(), 'stop')}. If it names a unit (LPA, lakh, "
             f"month) follow it; a MONTHLY field -> stop (needs_user).",
             f"- Required consent / accuracy / privacy-policy / terms checkbox: {'tick it' if pr.get('consent_checkbox') else 'do NOT tick; needs_user'} "
             f"(never marketing, newsletter, WhatsApp or SMS opt-ins).",
             f"- Cover letter: {'write it yourself (rules below)' if pr.get('cover_letter_ai') else 'needs_user'}.",
             f"- Email applications: {'allowed (rules below)' if pr.get('email_applications') else 'not allowed; needs_user'}.",
             "- Workplace model (onsite / hybrid / remote): any is fine -> answer Yes / comfortable (pick Hybrid if you must choose one).",
             "- Shift (day / night / US hours / rotational): any is fine -> answer Yes.",
             "- Relocation / commute to Delhi NCR: I live in Mohali. Say I am willing to RELOCATE; never say I can commute.",
             "- CAPTCHA anywhere: skip the job."]
    for item in a:
        lines.append(f"- Question matching /{item.get('match')}/ -> \"{item.get('answer')}\"")
    return "\n".join(lines)


def build(con, limit: int = 15, questions: bool = False) -> Path:
    """Write the brief for a browser agent. limit = jobs per brief (best fit first; the brief itself says max 15 per
    session - run it again after `import-results` for the next batch). questions=True also lists the jobs that stopped
    on a question (same facts as the script has, so the agent usually cannot answer them either)."""
    cfg, sk = load_config(), load_skills()
    HANDOFF.mkdir(parents=True, exist_ok=True)
    main_resume = resume_file(cfg).resolve()
    dest_pdf = HANDOFF / "archit_sde.pdf"
    if main_resume.exists() and main_resume != dest_pdf:
        shutil.copy(main_resume, dest_pdf)
    g = pending_jobs(con)
    kept, dropped = _rank_external(con, cfg, g["external"])
    batch = kept[:limit]
    g_all = {**g, "external": kept}
    g = {**g, "external": batch, "needs_manual": g["needs_manual"] if questions else []}
    LAST_STATS.update(external_total=len(kept) + dropped, dropped=dropped, in_brief=len(batch),
                      later=len(kept) - len(batch), questions=len(g["needs_manual"]), unconfirmed=len(g["unconfirmed"]))
    todo = g["external"] + g["needs_manual"]
    (HANDOFF / "jobs.json").write_text(json.dumps(g_all, indent=2, default=str))
    with open(HANDOFF / "results.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["platform", "job_id", "company", "title", "status", "notes"])
        for r in todo:
            w.writerow([r["platform"], r["job_id"], r["company"], r["title"], "", ""])

    def job_block(i, r):
        return (f"### {i}. {r['title']} - {r['company']}\n- Link: {r['url']}\n- Location: {r['location']}   |   Experience asked: {r['exp_text']}\n"
                f"- Platform: {r['platform']}   |   job_id: `{r['job_id']}`\n- Why it is here: {(r['reason'] or '').splitlines()[0][:200]}\n"
                f"- My skills that match this job: {(r['matched_skills'] or '').replace(',', ', ')}\n")

    ext = "\n".join(job_block(i, r) for i, r in enumerate(g["external"], 1)) or "_none_"
    man = "\n".join(job_block(i, r) for i, r in enumerate(g["needs_manual"], 1)) or "_none_"
    unc = "\n".join(f"- {r['title']} - {r['company']}  ({r['url']})" for r in g["unconfirmed"]) or "_none_"
    brief = f"""# Task: finish job applications on company websites

You are my browser operator. A script already applied to jobs on Naukri / Instahyre / Indeed for me. The jobs below could not be
finished by the script: they redirect to the company's own careers site, or ask something the script would not guess.
Complete them for me, carefully and honestly.

## Candidate facts (the ONLY source of truth for answers)
{_facts(cfg)}

**Resume - use ONLY this file, never edit or rewrite it:** `{main_resume}` (or `{dest_pdf}`)
Do not generate, tailor or upload any other version of my resume.

## Saved preferences (asked once - use them, do not ask again)
{_prefs(cfg)}

## Rules - read before starting
1. **Truth only.** Use only the facts above. Never invent experience, skills, employers, dates, numbers or certificates.
2. **Not covered = stop.** If a form asks something the facts and saved preferences do not answer (a new yes/no, a skill not listed
   above, "years with X" for a tech I do not have), do NOT guess. (Cover letters are NOT a stop: see rule 10. Salary fields with no unit,
   workplace model, shift and plain consent boxes are covered by the saved preferences.) Stop that job, write the exact question in the
   `notes` column of results.csv, and set status `needs_user`.
3. **Never claim skills I do not have.** These are blocked for me: {', '.join(never_claim(sk)[:24])}, ... If a job's MAIN requirement is one of them, skip it (status `skipped`).
4. **CAPTCHA, OTP, passkeys:** skip the job (status `skipped`, note "captcha"); do not try to get around them. **Logins:** do not try to get around them. If a site needs an account to be created or a login, set status `needs_user`
   with the note "needs account/login" and move on (I will tell you if creating accounts is allowed).
5. **Do not touch** my Naukri / LinkedIn / Instahyre / Indeed profile, resume, settings or messages. Do not message recruiters.
   **Never open linkedin.com, never log in to LinkedIn and never use "Apply with LinkedIn" / "Easy Apply"** - LinkedIn bans automated
   activity and I could lose my account. If a company site only accepts LinkedIn, set status `needs_user` with the note "needs LinkedIn".
6. **One application per company - EVER.** Before opening a job, look for the company in the Excel file below (column F, ignore case and
   "Pvt Ltd/Ltd"). If it is already there, do NOT apply: status `skipped`, note "company already in Applied_Jobs.xlsx". Never apply twice to
   the same company in this session either. If the job is closed or expired, status `skipped`.
7. **Submit** only when every required field was filled from the facts above. Then copy the confirmation text/URL into `notes`.
8. **Pace:** a few minutes per application, maximum 15 in one session. Never run several applications in parallel.
9. **Every job you successfully submit goes into the Excel file** (next section) the moment you submit it. Every job you do NOT submit
   (`needs_user`, `skipped`, `failed`) goes into `results.csv` in this folder instead.
10. **Cover letter needed** (a required field, or an email application): write it yourself, see "Cover letters" below.
11. **Email-only jobs** (the company's own page shows just an address): apply with `python main.py email ...`, see "Applying by email" below.
12. **Never open or click anything marketing**: no newsletter, WhatsApp, SMS or "keep me updated" boxes.

## Cover letters
{coverletter.guidelines(cfg)}

## Applying by email (only when the job's own company page gives just an address)
1. Use an address printed on the company's OWN careers / job page (careers@, hr@, jobs@, recruitment@, career@), never a guessed one.
2. Write the letter to a text file, check it: `python main.py coverletter --file letter.txt` (must print OK).
3. Dry run: `python main.py email --job <platform>:<job_id> --to <address> --letter-file letter.txt --found-on <page url>`
4. Send: the same command plus `--send`. It attaches the resume, applies the per-company and per-day limits, records the job as applied
   and updates the Excel itself - do NOT add that job to the Excel by hand.
5. If it says NOT SENT, fix the reason it prints; never work around it.

## The Excel file - ALWAYS write applied jobs here
File: `{excel.excel_path()}`   (sheet **Applied**; it lists every job applied to, by the script and by you)
- Open it before starting (to check companies) and again right before you add a row - reload it if it changed on disk.
- After each successful submit, **append ONE new row at the bottom of the "Applied" sheet**. Do not edit, sort, move or delete existing rows,
  do not touch the "Summary" sheet, keep the header row. Save the file (as .xlsx, same name, same folder) and close it.
- Fill these columns (leave A, I, L and R blank - the script fills them):

| Col | Header | What to write |
|---|---|---|
| B | Date | day of the application, `YYYY-MM-DD` |
| C | Time | `HH:MM` |
| D | Platform | `company-site` |
| E | Applied via | `Company website (browser agent)` |
| F | Company | exact company name |
| G | Job title | exact title |
| H | Job location | city / Remote as on the posting |
| J | Work type | `Remote`, `Onsite` or `Hybrid` |
| K | Experience asked | e.g. `2-4 Yrs` |
| M | Matched skills | my skills that match this job (optional) |
| N | Resume used | `archit_sde.pdf` |
| O | Job link | URL of the application / job page on the company site |
| P | Notes | the confirmation text or reference number shown after submit |
| Q | Source | `Antigravity` |

A row with no Company or no Job title is ignored. Only SUBMITTED applications belong in this Excel file.

## How to open each job
The link is the Naukri / Instahyre / Indeed job page. Open it, click **"Apply on company site"** (or the equivalent), and continue
on the company's site. If the job board asks for a login or a verification check, stop and tell me (the agent browser has its own
profile).

## A. Jobs that redirect to the company website - this batch: {len(g['external'])}{f" (best fit first; {len(kept) - len(batch)} more wait for the next batch)" if len(kept) > len(batch) else ""}
Work through them in this order.

{ext}
{f"""
## B. Jobs where the questionnaire needs my answer ({len(g['needs_manual'])})
Read the reason line: it names the question. If you can answer it from the facts above (e.g. a range option that contains my
experience), do it; otherwise leave it as `needs_user`.

{man}
""" if questions else ""}
## C. Possibly already applied - VERIFY ONLY, do not apply again ({len(g['unconfirmed'])})
Open the job board's own "Applied jobs" page (Naukri > My Naukri, Instahyre > Applied jobs, Indeed > My jobs > Applied; only if
already logged in) and tell me which of these appear there. Do not click Apply.

{unc}

## When you are done
Tell me: how many you submitted (rows added to the Excel), and the `needs_user` (with the exact questions), `skipped`, `failed` ones
(rows in results.csv). I import both back into my tracker.
"""
    (HANDOFF / "ANTIGRAVITY_BRIEF.md").write_text(brief)
    return HANDOFF


def import_results(con, path: Path | None = None) -> dict:
    path = path or HANDOFF / "results.csv"
    n = {"applied": 0, "needs_manual": 0, "skipped": 0, "failed": 0, "ignored": 0}
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            st = RESULT_STATUS.get((r.get("status") or "").strip().lower())
            if not st:
                n["ignored"] += 1
                continue
            note = (r.get("notes") or "").strip()
            reason = ("applied on the company site via browser agent" + (f": {note}" if note else "")) if st == "applied" \
                else (note or f"browser agent: {st}")
            cur = con.execute("UPDATE applications SET status=?, reason=?, ts=? WHERE platform=? AND job_id=? AND status IN "
                              "('external','needs_manual','unconfirmed')", (st, reason[:300], time.time(), r["platform"], r["job_id"]))
            n[st if cur.rowcount else "ignored"] += 1
    con.commit()
    try:
        excel.export(con)
    except Exception as e:
        print(f"could not update {excel.excel_path().name}: {e}")
    return n
