"""Apply by email (jobs whose only route is an address on the company's own site), through a FREE mailbox over SMTP.
Default: Gmail with an app password (replies land in your own inbox). Credentials come from .env only, never from config.

.env:  SMTP_USER=you@gmail.com   SMTP_PASSWORD=<16-char Google app password>   [SMTP_HOST=smtp.gmail.com  SMTP_PORT=587]
Nothing is sent unless send=True; every send is logged to out/email_log.csv and capped per day."""
import csv
import os
import re
import smtplib
import time
from email.message import EmailMessage
from pathlib import Path
from . import coverletter, db
from .companies import norm_company
from .config import OUT, load_config, resume_file

LOG = OUT / "email_log.csv"
ADDR_RX = re.compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$")
GENERIC_BAD = re.compile(r"^(noreply|no-reply|donotreply|sales|support|billing|abuse|privacy|legal|press|media)@", re.I)


def smtp_settings() -> dict:
    env = os.environ.get
    return {"host": env("SMTP_HOST") or "smtp.gmail.com", "port": int(env("SMTP_PORT") or 587),
            "user": env("SMTP_USER") or "", "password": env("SMTP_PASSWORD") or ""}


def _sent_today() -> int:
    if not LOG.exists():
        return 0
    today = time.strftime("%Y-%m-%d")
    with open(LOG, newline="") as f:
        return sum(1 for r in csv.DictReader(f) if r["time"].startswith(today) and r["status"] == "sent")


def _log(to, subject, job, status, note=""):
    OUT.mkdir(exist_ok=True)
    new = not LOG.exists()
    with open(LOG, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "to", "subject", "job", "status", "note"])
        w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), to, subject, job, status, note])


def build_message(to: str, subject: str, letter: str, cfg: dict, attach: Path | None) -> EmailMessage:
    p = cfg["profile"]
    sig = f"\n\n{p['full_name']}\n{p['phone']}  |  {p['email']}\nGitHub: github.com/Aj-108  |  LinkedIn: linkedin.com/in/archit108"
    msg = EmailMessage()
    st = smtp_settings()
    msg["From"] = f"{p['full_name']} <{st['user']}>"
    msg["To"] = to
    msg["Reply-To"] = st["user"]
    msg["Subject"] = subject
    msg.set_content(letter.strip() + sig)
    if attach and attach.exists():
        msg.add_attachment(attach.read_bytes(), maintype="application", subtype="pdf", filename=attach.name)
    return msg


def send_application(con, job: str, to: str, letter: str, subject: str | None = None, found_on: str = "", send: bool = False) -> dict:
    """job = 'platform:job_id'. -> {ok, sent, problems, subject}. Dry run unless send=True."""
    cfg = load_config()
    out = {"ok": False, "sent": False, "problems": [], "subject": subject}
    if not (cfg.get("preferences") or {}).get("email_applications", False):
        out["problems"].append("preferences.email_applications is off in config.yaml")
    if not ADDR_RX.match(to) or GENERIC_BAD.match(to):
        out["problems"].append(f"not a usable recruiter / careers address: {to}")
    platform, _, job_id = job.partition(":")
    row = con.execute("SELECT * FROM applications WHERE platform=? AND job_id=? ORDER BY id DESC LIMIT 1", (platform, job_id)).fetchone()
    if not row:
        out["problems"].append(f"unknown job {job}")
    elif db.company_applied_count(con, row["company"]) >= cfg.get("max_per_company", 1):
        out["problems"].append(f"already applied to {row['company']} (one application per company)")
    out["problems"] += coverletter.check(letter, cfg)
    st = smtp_settings()
    if send and not (st["user"] and st["password"]):
        out["problems"].append("SMTP_USER / SMTP_PASSWORD are not set in .env")
    cap = int((cfg.get("email") or {}).get("max_per_day", 15))
    if send and _sent_today() >= cap:
        out["problems"].append(f"daily email cap reached ({cap})")
    if row and not subject:
        out["subject"] = subject = f"Application for {row['title']} - {cfg['profile']['full_name']} ({cfg['profile']['total_experience_years']} yrs, MERN / Next.js)"
    if out["problems"]:
        return out
    out["ok"] = True
    msg = build_message(to, subject, letter, cfg, resume_file(cfg))
    if not send:
        return out
    with smtplib.SMTP(st["host"], st["port"], timeout=30) as s:
        s.starttls()
        s.login(st["user"], st["password"])
        s.send_message(msg)
    out["sent"] = True
    _log(to, subject, job, "sent", found_on)
    con.execute("UPDATE applications SET status='applied', reason=?, ts=? WHERE id=?",
                (f"applied on the company site by email to {to}" + (f" (address found on {found_on})" if found_on else ""), time.time(), row["id"]))
    con.commit()
    return out
