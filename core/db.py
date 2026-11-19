"""SQLite store: every job seen and every application attempt, for dedupe + reporting."""
import sqlite3
import time
from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at REAL, ended_at REAL, dry_run INTEGER, claude_cost_inr REAL DEFAULT 0, notes TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
  platform TEXT, job_id TEXT, title TEXT, company TEXT, location TEXT, remote INTEGER,
  exp_min REAL, exp_max REAL, url TEXT, description TEXT, first_seen REAL,
  PRIMARY KEY (platform, job_id)
);
CREATE TABLE IF NOT EXISTS applications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER, ts REAL, platform TEXT, job_id TEXT, title TEXT, company TEXT, location TEXT,
  remote INTEGER, exp_text TEXT, fit_score REAL, status TEXT, reason TEXT,
  matched_skills TEXT, resume_path TEXT, url TEXT
);
CREATE INDEX IF NOT EXISTS idx_app_job ON applications(platform, job_id);
-- jobs judged unsuitable (title / experience / stack / fit score), per version of the skip rules
CREATE TABLE IF NOT EXISTS skip_cache (
  platform TEXT, job_id TEXT, rules TEXT, reason TEXT, ts REAL,
  PRIMARY KEY (platform, job_id, rules)
);
"""

# statuses: queued | unconfirmed | applied | dry_run | skipped | external | needs_manual | failed


def connect(path=DB_PATH) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=30)          # platforms run in parallel threads, each with its own connection
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def start_run(con, dry_run: bool) -> int:
    cur = con.execute("INSERT INTO runs(started_at, dry_run) VALUES (?,?)", (time.time(), int(dry_run)))
    con.commit()
    return cur.lastrowid


def end_run(con, run_id: int, cost_inr: float = 0.0, notes: str = ""):
    con.execute("UPDATE runs SET ended_at=?, claude_cost_inr=?, notes=? WHERE id=?",
                (time.time(), cost_inr, notes, run_id))
    con.commit()


def already_handled(con, platform: str, job_id: str) -> bool:
    """True if this job never needs another look: applied / company site / needs you / unconfirmed in an earlier run, or
    the site itself showed 'Applied' for it (applied by hand, or by an earlier run whose success was not recognised).
    Permanent: unlike a rule-based skip it does not depend on the skip rules."""
    r = con.execute(
        """SELECT 1 FROM applications WHERE platform=? AND job_id=? AND (
             status IN ('applied','external','needs_manual','unconfirmed')
             OR (status='skipped' AND (reason='already applied' OR reason LIKE 'already applied (%'))) LIMIT 1""",
        (platform, job_id)).fetchone()
    return r is not None


def save_job(con, job: dict):
    con.execute(
        "INSERT OR IGNORE INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (job["platform"], job["job_id"], job.get("title"), job.get("company"), job.get("location"),
         int(bool(job.get("remote"))), job.get("exp_min"), job.get("exp_max"), job.get("url"),
         job.get("description"), time.time()))
    con.commit()


def log_application(con, run_id: int, job: dict, status: str, reason: str = "", fit: float | None = None,
                    matched: list[str] | None = None, resume_path: str = "") -> int:
    cur = con.execute(
        """INSERT INTO applications(run_id, ts, platform, job_id, title, company, location, remote, exp_text,
           fit_score, status, reason, matched_skills, resume_path, url) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (run_id, time.time(), job["platform"], job["job_id"], job.get("title"), job.get("company"),
         job.get("location"), int(bool(job.get("remote"))), job.get("exp_text", ""), fit, status, reason,
         ",".join(matched or []), resume_path, job.get("url")))
    con.commit()
    return cur.lastrowid


def update_application(con, row_id: int, status: str, reason: str = "", matched: list[str] | None = None,
                       resume_path: str = ""):
    """Turn a 'queued' row into its final outcome (so each job appears once)."""
    con.execute("UPDATE applications SET ts=?, status=?, reason=?, matched_skills=?, resume_path=? WHERE id=?",
                (time.time(), status, reason, ",".join(matched or []), resume_path, row_id))
    con.commit()


def finalize_queued(con, run_id: int):
    """Jobs still 'queued' when the run ends were never reached (time / cap ran out, or you pressed Ctrl+C)."""
    con.execute("UPDATE applications SET status='skipped', reason='not reached before time/cap ran out' "
                "WHERE run_id=? AND status='queued'", (run_id,))
    con.commit()


def company_applied_count(con, company: str, *_ignored) -> int:
    """How many applications went (or may have gone) to this company, EVER, on any platform or via the browser agent.
    Company names are compared normalised ('Acme Pvt Ltd' == 'ACME')."""
    from .companies import norm_company
    want = norm_company(company)
    if not want:
        return 0
    n = 0
    for r in con.execute("SELECT company FROM applications WHERE status IN ('applied','unconfirmed') "
                         "OR reason LIKE 'could not confirm%'"):
        if norm_company(r["company"]) == want:
            n += 1
    return n


def hard_skipped_before(con, platform, job_id, rules: str) -> bool:
    """True if this job was already judged unsuitable under the CURRENT skip rules (see config.rules_fingerprint).
    When the rules change (skills_bank.yaml, min_fit_score, ...), old skips are re-checked once."""
    r = con.execute("SELECT 1 FROM skip_cache WHERE platform=? AND job_id=? AND rules=? LIMIT 1",
                    (platform, job_id, rules)).fetchone()
    return bool(r)


def remember_skip(con, platform, job_id, rules: str, reason: str):
    con.execute("INSERT OR REPLACE INTO skip_cache VALUES (?,?,?,?,?)", (platform, job_id, rules, reason, time.time()))
    con.commit()
