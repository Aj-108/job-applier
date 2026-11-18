"""Instahyre - matched 'opportunities' list, one-click interested/apply. Text-based locators; verify with --dry-run."""
import hashlib
import re
from core.jd_parser import parse_experience
from .base import human_sleep, page_text, check_blocked, snap

NAME = "instahyre"
OPPS = "https://www.instahyre.com/candidate/opportunities/?matching=true"
CARDS_JS = """() => [...document.querySelectorAll('.employer-block, [class*="opportunity"] , .opportunity-card')].map((e,i) => {
  const t = s => (e.querySelector(s)||{}).innerText||'';
  return {id: e.getAttribute('data-id') || e.id || String(i), text: e.innerText.slice(0, 600),
          title: t('.employer-job-name, [class*="job-title"], h3, h4'), company: t('.employer-company-name, [class*="company"]'),
          loc: t('[class*="location"], .location')};})"""


def split_title(title: str, company: str) -> tuple[str, str]:
    """Instahyre cards read 'Swiggy - React.js Developer'; the selectors return that whole string for both fields.
    -> (role, company)."""
    t, c = (title or "").strip(), (company or "").strip()
    if (not c or c == t) and " - " in t:
        co, role = t.split(" - ", 1)
        return role.strip(), co.strip()
    return t, c


CONFIRM_RX = re.compile(r"applied successfully|successfully applied|application (has been )?(sent|submitted)|"
                        r"you have (already )?applied|thank you for applying|profile (has been )?(shared|sent)", re.I)


def logged_in(page) -> bool:
    page.goto(OPPS, wait_until="domcontentloaded")
    human_sleep((3, 5))
    return "login" not in page.url and "signup" not in page.url


def search(page, role: str, loc: str) -> list[dict]:
    """Instahyre has no keyword search for candidates: it lists opportunities matched to your profile.
    We read that list once per call and filter by role/location text on our side."""
    page.goto(OPPS, wait_until="domcontentloaded")
    human_sleep((3, 6))
    check_blocked(page)
    for _ in range(3):                       # lazy-load more cards
        page.mouse.wheel(0, 2500)
        human_sleep((1, 2))
    jobs = []
    for c in page.evaluate(CARDS_JS):
        blob = (c["text"] + " " + c["loc"]).lower()
        if loc.lower() != "remote" and loc.lower() not in blob:
            continue
        if loc.lower() == "remote" and not re.search(r"remote|work from home|wfh", blob):
            continue
        lo, hi = parse_experience(c["text"])
        stable = hashlib.md5((c["title"] + c["company"] + c["loc"]).encode()).hexdigest()[:12]      # list index is not stable
        role, company = split_title(c["title"] or c["text"].split("\n")[0], c["company"])
        jobs.append({"platform": NAME, "job_id": stable, "title": role,
                     "company": company, "location": c["loc"].strip() or loc,
                     "remote": loc.lower() == "remote", "exp_text": f"{lo}-{hi} yrs" if lo is not None else "",
                     "exp_min": lo, "exp_max": hi, "url": OPPS, "description": c["text"]})
    return jobs


def refresh(page):
    """Back to the opportunities list (with cards loaded) before each apply."""
    if "opportunities" not in page.url:
        page.goto(OPPS, wait_until="domcontentloaded")
        human_sleep((3, 5))
    for _ in range(3):
        page.mouse.wheel(0, 2500)
        human_sleep((1, 2))


def details(page, job: dict) -> dict:
    return job                               # the card text is all we have without opening each card


def upload_profile_resume(page, pdf_path) -> bool:
    page.goto("https://www.instahyre.com/candidate/profile/", wait_until="domcontentloaded")
    human_sleep((3, 5))
    try:
        page.set_input_files("input[type='file']", str(pdf_path))
        human_sleep((4, 7))
        return True
    except Exception:
        snap(page, "instahyre_resume_upload_failed")
        return False


def apply(page, job: dict, resume_pdf, dry_run: bool) -> tuple[str, str]:
    cards = page.query_selector_all(".employer-block, [class*='opportunity'], .opportunity-card")
    card = next((c for c in cards if job["title"] and job["title"][:30] in (c.inner_text() or "")), None)
    if not card:
        return "failed", "card not found on page"
    btn = card.query_selector("button:has-text('Apply'), button:has-text('Interested'), a:has-text('Apply')")
    if not btn:
        return "skipped", "no apply button (already applied?)"
    if dry_run:
        return "dry_run", "would apply"
    btn.click()
    human_sleep((2, 4))
    check_blocked(page)
    text = page.evaluate("document.body.innerText")
    after = (card.inner_text() or "") if card.is_visible() else ""
    if CONFIRM_RX.search(text) or re.search(r"(^|\n)\s*applied\s*($|\n)", after, re.I):
        return "applied", ""
    # clicked but nothing proves it went through: never call it applied (the Excel would be wrong) and never click again
    snap(page, "instahyre_unconfirmed")
    return "unconfirmed", "clicked Apply, success not recognised - check Instahyre | page: " + " ".join(text.split())[:160]
