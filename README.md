# Job Applier

Automatically applies to matching jobs on **Naukri**, **Instahyre** and **Indeed** (Easily apply) for about an hour per run, hands company-website jobs to **Antigravity** (a browser agent), and keeps one Excel file of every job applied to.

- Uses **one resume only**, never edited. Never applies twice to the same company. Never invents skills.
- LinkedIn is **not automated** (ban risk). Apply to LinkedIn jobs by hand.
- Design and review notes: [ARCHITECTURE.md](ARCHITECTURE.md)

## 1. Setup (once)

```
git clone <your-repo-url> && cd <repo>
./setup.sh
```

`setup.sh` creates the virtual environment, installs the dependencies and Chromium, and creates `config.yaml` and `.env` from the templates.

Then:

1. **Resume**: copy your resume (PDF or `.docx`) into the project folder. In `config.yaml`, set `resume_pdf:` (or `resume_docx:`) to its file name.
2. **Profile**: in `config.yaml`, fill in the `profile:` block: name, email, phone, experience, current and expected CTC, notice period, location, and years per skill. These answers fill job forms. Anything not covered is never guessed; that job is left for you.
3. **Search settings** (optional), also in `config.yaml`: `roles`, `locations`, `min_fit_score`, `max_experience_years`, and the per-platform `minutes` and `max_apply`.
4. **Skills** (optional): review `skills_bank.yaml`. `core` and `adjacent` are skills you may claim; `blocked` are stacks the tool will skip (Java, PHP, Python and so on).
5. **AI key** (optional): only the manual `tailor` command uses AI. Set `AI_PROVIDER=gemini|anthropic|none` and the key in `.env`. Runs work without it.

Log in to each site once. Your real Google Chrome opens with a separate job-applier profile (`.browser/chrome`) and
nothing automated attached, so "Sign in with Google" works. Log in by hand in each tab, then **quit that Chrome
window (Cmd+Q)** and press Enter. Your everyday Chrome is not touched and can stay open.

```
python main.py login all          # or: login naukri | instahyre | indeed
```

During a run the tool starts that same Chrome profile itself, so the job-applier Chrome window must be closed first.
(`browser: playwright` in config.yaml switches back to the bundled Chromium, where Google sign-in is refused.)

## 2. Run

Rehearse first. This searches and scores but does not click Apply:

```
python main.py run --minutes 10 --dry-run --cap 5
```

Real run (about 60 minutes). On macOS, `caffeinate` keeps the Mac awake:

```
caffeinate -i python main.py run --minutes 60
```

Live report in a second terminal. It opens in the browser and refreshes itself:

```
python main.py dashboard
```

Useful options: `--only naukri|instahyre|indeed` (one platform), `--cap 5` (max applications), `--dry-run`.

All platforms run **at the same time** (`parallel: true`): one Chrome, one tab per site, each with its own pacing, for
the whole run, up to each site's `max_apply`. With `parallel: false` they run one after another (Naukri 35 min,
Instahyre 10, Indeed 15; minutes one platform does not use go to the next). `--only <site>` always runs just that site.

**Unconfirmed jobs**: after every real run the tool reads Naukri's own "Application History" and settles any job whose success
message it could not read (`verify_after_run` in `config.yaml`). Run it by hand any time with `python main.py verify naukri`
(add `--retry-missing` to retry jobs Naukri does not list as applied). Company-site jobs Naukri files under "external site" move
to the Antigravity list.

## Google Sheet (optional)

Mirrors `Applied_Jobs.xlsx` into a Google Sheet: one tab per day (frozen once the day is over) plus an "All Applied"
master tab that always holds every application. Off by default.

**Setup (one time, free, about 10 minutes):**
1. [console.cloud.google.com](https://console.cloud.google.com) → create a project → **APIs & Services → Library** →
   enable "Google Sheets API".
2. **APIs & Services → Credentials** → Create Credentials → **Service Account** → create it → **Keys** → Add key →
   JSON. Save the downloaded file in this folder (e.g. `service-account.json`). It is a credential - never commit it
   (`.gitignore` already excludes `service-account*.json`).
3. Create a Google Sheet (or use an existing one). Open **Share**, and add the service account's email (it ends
   `...iam.gserviceaccount.com`, shown in the JSON file and the Cloud Console) as **Editor**.
4. Copy the Sheet's id from its URL: `docs.google.com/spreadsheets/d/<sheet_id>/edit`.
5. In `config.yaml`, under `google_sheet:`, set `enabled: true`, `sheet_id`, and `service_account_file` (the path
   from step 2).

Then `python main.py sync-sheet` pushes once by hand, and every real `python main.py run` pushes automatically at
the end (never on a dry run; a sync failure is noted but never stops the run).

**Questions the tool cannot answer**: `python main.py questions` lists the questions that sent jobs to `needs_manual`.
Add TRUE answers to `config.yaml` under `answers:` (a regex for the question + the answer); the next runs use them.
At the end of each platform the terminal prints a **funnel** (cards seen, skipped from the card, opened, skipped for fit,
applied, external...) so you can see where jobs drop out.

After changing `skills_bank.yaml` or `min_fit_score`, check the effect without a browser:
`python main.py rescore` lists earlier skipped jobs that the new rules would now accept. Skip decisions are cached per
rules version, so jobs are re-checked once automatically after a rule change.
Keep the browser window open and the Mac awake while it runs. If a CAPTCHA, a limit or an "Access Denied" page appears, the tool stops that platform and moves on. Do not try to get around it.

## 3. Company-website jobs (Antigravity)

Some jobs redirect to the company's own site. The tool does not apply there; Antigravity does.

1. Create the hand-off: `python main.py handoff`
2. Give Antigravity the file `out/handoff/ANTIGRAVITY_BRIEF.md`. It contains the rules, your facts and the job list.
3. Antigravity adds the jobs it applies to into `Applied_Jobs.xlsx` and writes everything else (needs you, failed, skipped) into `out/handoff/results.csv`.
4. When it finishes: `python main.py import-results`

Antigravity is told never to open LinkedIn, solve CAPTCHAs or log in for you. Jobs with a CAPTCHA come back as `needs_user`; finish those by hand.

## 4. Where to look

| What | Where |
|---|---|
| Every applied job (applied only) | `Applied_Jobs.xlsx` (sheets **Applied** and **Summary**) |
| Rebuild the Excel | `python main.py excel` |
| Full report with stats and skip reasons | `out/report.html`, `out/applications.csv`; rebuild with `python main.py report` |
| Jobs that need you | `out/handoff/results.csv` and the dashboard |
| Screenshots when an apply could not be confirmed | `logs/` |

Job statuses: `applied`, `unconfirmed` (clicked but not proven; check Naukri → Applied jobs), `external` (company site), `needs_manual`, `skipped`, `failed`, `queued`, `dry_run`.

If a row is `unconfirmed` and the job really shows as applied on the site, add it to `Applied_Jobs.xlsx` by hand. The tool then counts that company as done.

## 5. All commands

| Command | What it does |
|---|---|
| `login naukri\|instahyre` | Log in once by hand |
| `run [--minutes N] [--dry-run] [--only P] [--cap N]` | Search and apply |
| `dashboard [--port N] [--all]` | Live report in the browser |
| `report [--run last\|all]` | Rebuild the report |
| `handoff` | Prepare jobs and the brief for Antigravity |
| `import-results [file]` | Bring Antigravity's results back |
| `excel` | Rebuild `Applied_Jobs.xlsx` |
| `upload-resume naukri\|instahyre` | Put your unchanged main resume on the site profile |
| `tailor [--jd file \| --clipboard]` | Manual only: make a tailored resume copy for one job (for example a LinkedIn job you apply to by hand) |
| `models` | List Gemini models for your key |
| `demo-report` | Sample report with fake data |

`python main.py ...` works without activating the virtual environment.

## 6. Rules built in

- One resume, never changed.
- One application per company, ever (Naukri, Instahyre and Antigravity combined; "Pvt Ltd" and case are ignored).
- Jobs asking for more than `max_experience_years`, non-developer titles, and blocked stacks are skipped, often from the search card alone without opening the job.
- Form questions are answered only from your `profile`. Unknown questions are never guessed.
- Human-like delays between actions and between applications; visible browser; no CAPTCHA or login bypass.

## 7. Not in git

`.gitignore` keeps these on your machine: `.env`, `.browser/` (logged-in sessions), `config.yaml`, resume files, `applier.db`, `Applied_Jobs.xlsx`, `out/`, `logs/`. Templates are `config.example.yaml` and `.env.example`. Use a **private** repo anyway.

## 8. Troubleshooting

- **`config.yaml not found`**: `cp config.example.yaml config.yaml` and fill it in.
- **Not logged in / session expired**: run `python main.py login <platform>` again.
- **"Access Denied" or a CAPTCHA**: stop, wait, and try again later. Do not retry in a loop.
- **Many `unconfirmed`**: open the screenshots in `logs/` to see what the site showed after the click.
- **Tests**: `.venv/bin/python -m pytest -q` (29 should pass; they never touch your real data).

## Disclaimer

Automating job sites may break their terms of service. Keep volumes modest and use this at your own risk.


## Foundit (new, not yet verified live)

Foundit is built but **off by default**, because its pages could not be inspected while it was written. To bring it up:

1. Add to `platforms:` in your `config.yaml` (copy the line from `config.example.yaml`): `foundit: { enabled: false, minutes: 10, max_apply: 15 }`
2. `python main.py login foundit` (log in by hand, then quit that Chrome with Cmd+Q)
3. `python main.py probe foundit`: reads one search page and saves `out/probe/foundit.json` + `.html` and a screenshot in `logs/`. If it prints `cards found: 0`, the selectors need fixing from that file.
4. `python main.py run --only foundit --dry-run --cap 5`: searches and scores, clicks nothing.
5. Set `enabled: true` and use a small `max_apply` for the first real run, then check Foundit > Applied jobs.

Unknown situations are never guessed: extra questions go to `needs_manual`, company-site jobs to `external`, an unproven click to `unconfirmed`.
