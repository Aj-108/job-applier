# Job Applier — Architecture & Review Guide

> Written for a reviewing engineer or agent. Read the **At a glance** and **Hard rules** sections first; the rest is reference.

## 1. At a glance

| Item | Value |
|---|---|
| Owner | Archit Jain — MERN / Next.js / Shopify developer, ~2.5 yrs experience, India |
| Goal | Apply automatically for ~1 hour per run to matching jobs: Remote, Punjab (Mohali/Chandigarh/Ludhiana), Delhi, Gurugram, Noida |
| Language / stack | Python 3, Playwright (sync API, persistent **visible** browser profiles), SQLite, openpyxl, python-docx |
| Platforms automated | **Naukri** (inline apply), **Instahyre** (collect, then apply) |
| LinkedIn | **Disabled on purpose** (account-ban risk). Code exists in `platforms/linkedin.py` but `enabled: false`. Never use it. |
| Company-site / external jobs | Handed to **Antigravity** (a browser-agent operator) through files; not automated by this code |
| Resume | **One file only: `Archit_Jain_SDE.docx`**. Never edited, tailored or replaced in any automated path. |
| AI use | Only the manual `tailor` command (Gemini or Anthropic, chosen in `.env`). **Runs make no AI calls.** |
| Outputs | `Applied_Jobs.xlsx` (applied jobs only), `out/report.html`, `out/applications.csv`, `applier.db`, `logs/` screenshots |
| Tests | `.venv/bin/python -m pytest -q` → 29 passing |

Main commands (all through `python main.py ...`; it re-execs itself under `.venv/bin/python`):

```
login naukri|instahyre     log in by hand once (persistent profile; no passwords stored)
run --minutes 60 [--dry-run --only naukri --cap N]
dashboard [--port --all]   live report in the browser
report [--run last|all]    rebuild report
handoff                    write brief + jobs.json + results.csv for Antigravity
import-results [file]      bring results.csv back into DB/Excel
excel                      rebuild Applied_Jobs.xlsx
upload-resume naukri|instahyre   put the unchanged main resume on the site profile
tailor [--clipboard]       manual only: tailor a resume copy to one JD
models / demo-report
```

Normal run: `caffeinate -i python main.py run --minutes 60` (plus `python main.py dashboard` in a second terminal).

## 2. Hard rules (the invariants a reviewer should verify)

1. **One resume, never modified.** Automated paths pass `Archit_Jain_SDE.docx` straight through. Tests assert profile upload would raise if called during a run.
2. **No invented skills.** `skills_bank.yaml` has `core` (on the resume), `adjacent` (safe relatives) and `blocked` (Django, Flask, FastAPI, Python, Java, Spring, .NET, C#, PHP, Laravel, Angular, Vue, Flutter, React Native, Kotlin, Swift, Go, Rails, Kubernetes, Terraform, AWS/Azure/GCP, ML, SAP, Salesforce, WordPress, Android, iOS…). Jobs whose main stack is blocked are skipped. Form answers come only from `config.yaml` `profile`, never guessed.
3. **One application per company, ever.** Enforced by normalized company name (`core/companies.py:norm_company`) in the scheduler (`db.company_applied_count`), in the handoff (`pending_jobs`), and in the Antigravity brief. `unconfirmed` clicks count as applied for this rule.
4. **Excel lists applied jobs only.** Sheet `Applied` (+ `Summary`). Skipped / failed / needs-user / unconfirmed live in SQLite, `results.csv` and the dashboard, never in the Excel.
5. **No LinkedIn automation**, including by Antigravity (brief forbids opening linkedin.com / "Easy Apply" / "Apply with LinkedIn"; such jobs return as `needs_user`).
6. **No CAPTCHA or login bypass.** On a block (Akamai "Access Denied", CAPTCHA, limit message) the platform stops (`Blocked` exception) and the run continues with the next platform.
7. **Never claim success without proof.** If Apply was clicked but success can't be confirmed, status is `unconfirmed` (screenshot saved, never re-clicked).

## 3. Repository map

```
main.py                 CLI (argparse); venv re-exec
config.yaml             roles, locations, caps, pacing, min_fit_score, max_experience_years, profile answers, resume_docx
skills_bank.yaml        core / adjacent / blocked skill lists
.env                    AI_PROVIDER, GEMINI_*/ANTHROPIC_* keys (git-ignored, chmod 600). Only used by `tailor`.
core/
  config.py             load_env, load_config, load_skills, ai_settings, path constants
  db.py                 SQLite schema + helpers (runs, jobs, applications)
  companies.py          norm_company()
  jd_parser.py          skill synonyms, extract_skills, is_blocked_stack, parse_experience
  matcher.py            prescreen() (card-only) and evaluate() (full) -> ok/score/reason
  answers.py            answer(label) from config; pick_option(question, options) for chatbot/radio choices
  scheduler.py          run(): per-platform loop, caps, deadlines, company-once, Excel sync
  report.py             stats, report.html, CSV, terminal summary, dashboard data
  excel.py              Applied_Jobs.xlsx: ingest (Excel->DB) and export (DB->Excel)
  handoff.py            Antigravity brief/jobs.json/results.csv + import_results
  tailor.py, resume_writer.py, pipeline.py   MANUAL tailoring only (validator, rules/Claude/Gemini engines, PDF)
platforms/
  base.py               persistent session, interactive_login, human_sleep, snap, page_text, Blocked, check_blocked
  naukri.py             search / details / apply / chatbot / success detection
  instahyre.py          search / refresh / apply / success detection
  linkedin.py           DISABLED
tests/                  test_tailor, test_scheduler, test_report, test_handoff, test_excel; conftest isolates DB/Excel and forces AI_PROVIDER=none
out/                    report.html, applications.csv, handoff/{ANTIGRAVITY_BRIEF.md, jobs.json, results.csv, Archit_Jain_SDE.pdf}, old_tailored_resumes/
logs/                   screenshots of unconfirmed/failed clicks
```

## 4. Data model (`applier.db`)

- `runs` — one row per `run`.
- `jobs(platform, job_id, title, company, location, remote, exp_min, exp_max, url, description, first_seen)` — pages actually read.
- `applications(id, run_id, ts, platform, job_id, title, company, location, remote, exp_text, fit_score, status, reason, matched_skills, resume_path, url)`.
- **Statuses:** `queued` (passed fit, waiting) · `applied` · `unconfirmed` · `dry_run` · `skipped` · `external` (company site) · `needs_manual` · `failed`. `finalize_queued` turns leftover `queued` into `skipped: not reached before time/cap ran out` at run end.
- Key helpers: `already_handled` (applied/external/needs_manual/unconfirmed), `company_applied_count` (ever, normalized; counts applied + unconfirmed), `hard_skipped_before` (skipped earlier for blocked stack / title / experience, so never reopened).

## 5. Run flow (`core/scheduler.py`)

```
run():
  ingest Applied_Jobs.xlsx -> DB            (picks up rows Antigravity/user added)
  for each enabled platform (Naukri, Instahyre) with its own minutes + max_apply:
    open persistent browser session; skip platform if not logged in or Blocked
    for (role x location) search queries:
      cards = P.search(...)
      for each card:
        skip if: seen this run | already_handled | same company+title+location | hard_skipped_before
        prescreen(card)         # title + experience from the card; NO page load. skip -> log 'skipped'
        P.details(job)          # opens the job page, reads description
        evaluate(job)           # exp, dev title, blocked stack, fit score; skip -> log 'skipped'
        log 'queued'
        Naukri (inline): apply_one() immediately
        Instahyre: collect candidates first, sort by fit, apply in phase B (refresh() before each)
    apply_one(job):
        re-sync Excel; if company_applied_count>0 -> skipped (one per company)
        P.apply(page, job, main_resume, dry_run) -> status
        update DB row; on 'applied' export Excel; sleep apply_delay (30-90s)
  finally: finalize_queued, export Excel, build report (HTML + CSV + terminal summary)
```

Pacing: `action_delay [3,9]s`, `apply_delay [30,90]s`, visible browser. Per-run budget `run_minutes: 60` (Naukri 30 / max 40, Instahyre 15 / max 25).

## 6. Filtering logic (`core/matcher.py`)

`prescreen` (card only, cheap) and `evaluate` (full, after reading the JD):

1. Experience: min asked > `max_experience_years` (4) → skip.
2. Title must contain a developer word (`developer, engineer, programmer, full stack, mern, sde…`); titles with `trainer, sales, analyst, tester, devops, support, intern, lead, principal, architect, manager, consultant…` are skipped.
3. Blocked stack: title mentions a blocked tech, or JD's blocked-skill mentions ≥4 and exceed core-skill count (`is_blocked_stack`).
4. Fit score 0–100 = weighted core/adjacent skill overlap; +10 for stack words in title; a stack-named title (node/react/next/shopify/js…) with ≥1 core skill and no blocked skill floors at 60; −10 per foreign skill (max −30); needs `min_fit_score` (55).
- Known deliberate strictness: mixed titles like "Full Stack Java Developer – React.js" or "Angular & Node.js" are skipped (would require claiming skills Archit lacks).

## 7. Platform specifics

**Naukri** (`platforms/naukri.py`): search URL `…-jobs[-in-city]?k=…&experience=2&jobAge=7` (`wfhType=2` for Remote). `apply()`: button label starting "applied" → skipped; "apply on company site" → `external`; otherwise click, answer chatbot questionnaire via `pick_option` (ranges, notice period, yes/no) / `answers.answer`, then `_applied()` checks `SUCCESS_RX` or the "Applied" button. Unproven → `unconfirmed`. Naukri's Akamai firewall blocks headless/probe traffic ("Access Denied"); use the visible persistent profile only. Known unfixed: occasional page-load timeouts (job retried next run).

**Instahyre** (`platforms/instahyre.py`): `split_title()` turns "Swiggy - React.js Developer" into (role, company); stable md5 job ids; `refresh()` returns to the list before each apply; success only on `CONFIRM_RX` (applied successfully / application sent / you have already applied / thank you for applying / profile shared…). **Apply/refresh/success paths are not yet verified live.**

## 8. Antigravity handoff (external company sites)

Files in `out/handoff/`:
- `ANTIGRAVITY_BRIEF.md` — instructions + candidate facts + rules (truth only, stop if a field isn't covered, no CAPTCHA/login bypass, never touch site profiles, no LinkedIn, one company once, check Excel first).
- `jobs.json` — pending jobs (one per company, excluding already-applied companies and resolved jobs).
- `results.csv` — columns `platform,job_id,company,title,status,notes`; the agent writes `submitted` / `needs_user` / `failed` / `skipped` here. Applied jobs are also added to `Applied_Jobs.xlsx` (Source = Antigravity).
- `Archit_Jain_SDE.pdf` — PDF of the same main resume.

Loop: `python main.py handoff` → run Antigravity on the brief → `python main.py import-results` (maps results to DB, exports Excel).

## 9. Excel (`core/excel.py`)

`Applied_Jobs.xlsx` in the project root. Columns: `#, Date, Time, Platform, Applied via, Company, Job title, Job location, Area, Work type, Experience asked, Fit score, Matched skills, Resume used, Job link, Notes, Source, Key`. `Summary` sheet uses live formulas (totals, by platform/area/source/day). Sync is bidirectional: `ingest()` (Excel→DB, so manual/agent rows count toward company-once) always runs before `export()`. Backup at `out/Applied_Jobs.backup.xlsx`.

## 10. Reporting

`core/report.py` builds `out/report.html` (tiles: seen/applied/skipped/queued/unconfirmed/external; breakdowns by platform, location, skip reason, skills, time slot), `out/applications.csv`, and a terminal summary. `dashboard` serves the HTML on localhost with auto-refresh (threaded; BrokenPipe handled).

## 11. Manual tailoring (not part of runs)

`python main.py tailor` reads the docx at fixed paragraph indexes, asks Gemini/Anthropic (per `.env`) for headline/summary/skill order/≤2 bullet rewrites, then `core/tailor.py` validates (no blocked terms, numbers/dates unchanged, length ±15%) and `resume_writer`/`pipeline` produce a docx/PDF copy under `out/`. PDF: LibreOffice (crashes on this Mac) → textutil→HTML→Chromium subprocess fallback. Original docx is never modified.

## 12. Status & known gaps (as of 2026-11-18)

- Applied (Excel): 7 — Leadpulse.io, Fizzy Softwares, Ahom Technologies, Perfectiongeeks, Greatertechhub, Consulligent, WebSenor.
- 5–6 earlier `unconfirmed` Naukri rows still to be verified by the user in Naukri → Applied jobs (First Ladder, Cybersify, Shashwath ×2, Altbig).
- Needs the user: Training Basket (image CAPTCHA), AMS Digital (reCAPTCHA). Failed: Avani Enterprises (site down).
- Unverified live: Instahyre apply path; Naukri new success detection and questionnaire ranges; card-level `prescreen` (just added, unit-tested only); whether the Naukri profile resume is named `Archit_Jain_SDE.docx`.
- Skip distribution (149 jobs / 4 runs): ~65 blocked stack, 59 "not reached" (before prescreen was added), 16 non-dev titles, ~18 low fit, 4 experience.

## 13. Review checklist

- [ ] Grep that no automated path calls `upload_profile_resume`, `build_resume` or any AI function (`scheduler.py`, `platforms/*`).
- [ ] Confirm `company_applied_count` is checked right before every `P.apply` and again in `handoff.pending_jobs`.
- [ ] Confirm Excel export filters `status='applied'` only (`core/excel.py`).
- [ ] Confirm `linkedin.enabled: false` and brief forbids LinkedIn.
- [ ] Check `answers.py` never fabricates: unknown label → no answer → job becomes `needs_manual`/skipped, not guessed.
- [ ] Run `.venv/bin/python -m pytest -q` (expect 29 passed; tests never touch the real DB/Excel or AI).
- [ ] Look at `logs/` screenshots for `unconfirmed` rows and tune success detection against what the site really shows.


## 13. Throughput changes (2026-11-18)

- **Time**: Naukri 35 / Instahyre 10 / Indeed 15 min; unused minutes carry over to the next platform (`scheduler.run`).
- **Cooldown**: `apply_delay [25,50]` is a minimum gap between two real Apply clicks (`applied` / `unconfirmed` / `needs_manual`),
  not a sleep after every job. Reading and scoring the next jobs counts towards it. `precheck()` (Naukri, Indeed) detects
  company-site / already-applied jobs without clicking, so those never wait.
- **No wasted page loads**: company already applied is checked from the search card; every skip (card, company, fit) is
  stored in `skip_cache` keyed by `config.rules_fingerprint()` (skills bank + limits + `matcher.RULES_VERSION`).
- **Naukri search**: `queries()` = role x location x experience {floor, ceil of total years}; page 2 when page 1 is full;
  the query list is rotated by run id.
- **Rules**: `soft_blocked` (AWS, Azure, GCP, Kubernetes, Terraform) never skips or costs points, and is still never claimed
  (`config.never_claim`). Title checks are whole-word regexes (`matcher.DEV_RX / STACK_RX / SKIP_TITLE_RX`). 2 matching
  skills cap the score at 55 (was 40). Blocked-stack test compares mentions with mentions. Bare "next" / "rest" /
  "responsive" are no longer skills. Page-body fallback is cut at "Similar jobs" etc. (`base.strip_noise`).
- **Block check**: visible captcha / Cloudflare widgets, bot wording only on short pages or the title, limit wording anywhere.
- **Indeed** (`platforms/indeed.py`): in.indeed.com search (last 7 days, Easily-apply cards first), JD `#jobDescriptionText`,
  Indeed Apply multi-step form filled from `config.yaml` (`FIELDS_JS` labels every field; unknown -> `needs_manual`, checkbox
  groups never ticked), main resume uploaded on the resume step. **Not yet verified live.**
- `openpyxl` was missing from requirements.txt (Excel updates silently failed on a fresh install) - added.

## 14. Instahyre rework (Phase 3, verified live 2026-11-18)

Why it never applied before: cards only have **View job** + **Not interested**; the Apply button lives in the job popup.
The old `apply()` looked for Apply on the card, found none, and returned "skipped". It also scored jobs on 600 chars of
card text (score capped at 40), filtered cities by literal text ("Gurugram" never matches Instahyre's "Gurgaon"), and
`li:has-text('Next')` matched the "Next.js" skill tags instead of the pager.

Now (`platforms/instahyre.py`):
- Job data is read from Angular (`angular.element(.employer-block).scope().opp`): job id (`'j' + job.id` - search results have no
  opportunity id), title, company, locations, skills, `interview_status` (0 = pending; anything else is skipped).
- Sources: the recommended list (pages 1-5), then skill searches (`platforms.instahyre.skills`, default React.js,
  Node.js, Next.js, MERN Stack, Shopify; `search_pages`, default 10). Search = type skill, pick the exact suggestion
  inside the search box's dropdown, JS-click Search. Search URLs cannot be loaded directly (they show the recommended list).
- Cities are filtered on our side with aliases (`LOC_ALIASES`: Gurgaon = Gurugram, Work From Home = Remote, ...). The
  site's city filter is not used: its text matches card chips and opens job popups.
- `details()` opens the popup (`.candidate-apply-modal`) and reads `.right-section-modal` (full JD + "2-6 Years").
- `apply()` JS-clicks the popup's Apply; success = "Applied jobs N" counter goes up, or opp status 1, or a confirmation
  text; otherwise `unconfirmed` + screenshot. The popup is closed after every job. All clicks are JS clicks because an
  invisible popup layer can swallow real mouse clicks.
- Instahyre is now applied inline like the other platforms (no collect phase, no `refresh()`).
- **Still unverified**: what the page shows right after a real Apply click (the first live apply will tell).

## 15. Phase 4 (2026-11-18)

- **Parallel** (`parallel: true`, default): `base.browser_host()` starts the job-applier Chrome once per run;
  each platform is a thread with its own Playwright connection (`connect_over_cdp`), its own tab, its own DB connection
  (`sqlite3 timeout=30`) and its own Apply cooldown. `scheduler.APPLY_LOCK` covers company check + Apply click + DB/Excel
  write, so two sites never apply to the same company at once. Ctrl+C sets a stop event; each thread ends after its
  current job. Threads start 3 s apart.
- **Answers**: `config.yaml answers: [{match: regex, answer: text}]`, checked first by `answers.answer()` (and therefore
  by `pick_option()`); invalid regexes are ignored. `python main.py questions` lists unanswered questions by frequency.
- **Indeed, verified live**: Apply = `[data-testid='viewjob-indeed-apply']` (a link to smartapply.indeed.com),
  JD = `[data-testid='viewjob-job-content']`. Cards without "Easily apply" are logged `external` without opening the page
  (`card_outcome`) - opening every job page triggered Cloudflare. Resume step: re-uploads the local PDF when Indeed's
  stored copy is older than the file. Whole-number fields: "no decimals" error -> answers rounded DOWN and retried once.
  Verified live end to end 2026-11-18 (Constems-AI, MERN Stack Developer): "Your application has been submitted!" on
  /form/post-apply, listed under Indeed > My jobs > Applied. Loading screens ("Preparing review") are waited out (20 s),
  and the confirmation is polled for 20 s after Submit.
- Block check: the "protected by reCAPTCHA" footer / invisible reCAPTCHA v3 badge on Indeed Apply is NOT a block
  (brand name stripped before the word check; badge iframes ignored). Real challenges are still caught.
- Rules: Dynamics 365, AEM, Ruby/ROR blocked (Zoho allowed); "fresher" / "trainee" titles skipped (RULES_VERSION 3).
