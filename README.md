# Job Applier

Automatically applies to matching jobs on **Naukri** and **Instahyre** for about an hour per run, hands company-website jobs to **Antigravity** (a browser agent), and keeps one Excel file of every job applied to.

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

1. **Resume**: copy your `.docx` resume into the project folder. In `config.yaml`, set `resume_docx:` to its file name.
2. **Profile**: in `config.yaml`, fill in the `profile:` block: name, email, phone, experience, current and expected CTC, notice period, location, and years per skill. These answers fill job forms. Anything not covered is never guessed; that job is left for you.
3. **Search settings** (optional), also in `config.yaml`: `roles`, `locations`, `min_fit_score`, `max_experience_years`, and the per-platform `minutes` and `max_apply`.
4. **Skills** (optional): review `skills_bank.yaml`. `core` and `adjacent` are skills you may claim; `blocked` are stacks the tool will skip (Java, PHP, Python and so on).
5. **AI key** (optional): only the manual `tailor` command uses AI. Set `AI_PROVIDER=gemini|anthropic|none` and the key in `.env`. Runs work without it.

Log in to each site once. A browser opens; log in by hand. Only the session cookies are kept, in `.browser/`:

```
python main.py login naukri
python main.py login instahyre
```

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

Useful options: `--only naukri` (one platform), `--cap 5` (max applications), `--dry-run`.
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
