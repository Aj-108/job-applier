"""Naukri.com - search, read JD, one-click apply (+ questionnaire bot). Selectors are best-effort: verify with --dry-run."""
import re
from urllib.parse import quote
from core.answers import answer, pick_option
from core.jd_parser import parse_experience
from .base import human_sleep, page_text, check_blocked, snap

NAME = "naukri"
JD_SELECTORS = ["section[class*='job-desc']", "div[class*='JDC__dang']", ".job-desc", "div[class*='dang-inner-html']"]
CARDS_JS = """() => [...document.querySelectorAll('.srp-jobtuple-wrapper, article.jobTuple')].map(e => {
  const a = e.querySelector('a.title'); const g = s => (e.querySelector(s)||{}).innerText||'';
  return {id: e.getAttribute('data-job-id') || (a && a.href.split('-').pop().split('?')[0]), title: a ? a.innerText : '',
          url: a ? a.href : '', company: g('a.comp-name'), exp: g('.expwdth'), loc: g('.locWdth')};})"""


def logged_in(page) -> bool:
    page.goto("https://www.naukri.com/mnjuser/homepage", wait_until="domcontentloaded")
    human_sleep((2, 4))
    return "nlogin" not in page.url and "login" not in page.url.split("?")[0]


def search_url(role: str, loc: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", role.lower()).strip("-")
    q = "experience=2&jobAge=7"
    if loc.lower() == "remote":
        return f"https://www.naukri.com/{slug}-jobs?k={quote(role)}&wfhType=2&{q}"
    return f"https://www.naukri.com/{slug}-jobs-in-{loc.lower()}?k={quote(role)}&l={quote(loc)}&{q}"


def search(page, role: str, loc: str) -> list[dict]:
    page.goto(search_url(role, loc), wait_until="domcontentloaded")
    human_sleep((3, 6))
    check_blocked(page)
    jobs = []
    for c in page.evaluate(CARDS_JS):
        if not c["id"] or not c["url"]:
            continue
        lo, hi = parse_experience(c["exp"])
        jobs.append({"platform": NAME, "job_id": c["id"], "title": c["title"].strip(), "company": c["company"].strip(),
                     "location": c["loc"].strip() or loc, "remote": loc.lower() == "remote" or "remote" in c["loc"].lower()
                     or "work from home" in c["loc"].lower(), "exp_text": c["exp"], "exp_min": lo, "exp_max": hi,
                     "url": c["url"]})
    return jobs


def details(page, job: dict) -> dict:
    page.goto(job["url"], wait_until="domcontentloaded")
    human_sleep((2, 5))
    check_blocked(page)
    job["description"] = page_text(page, JD_SELECTORS)
    return job


def upload_profile_resume(page, pdf_path) -> bool:
    """Replace the resume on your Naukri profile (Naukri cannot take a different file per application)."""
    page.goto("https://www.naukri.com/mnjuser/profile", wait_until="domcontentloaded")
    human_sleep((3, 5))
    try:
        page.set_input_files("input[type='file']", str(pdf_path))
        human_sleep((4, 7))
        return True
    except Exception:
        snap(page, "naukri_resume_upload_failed")
        return False


def _chatbot(page) -> str | None:
    """Answer Naukri's post-apply questionnaire. Returns None if finished, or a reason string if we must stop."""
    for _ in range(10):
        human_sleep((1, 2))
        if _applied(page):
            return None
        qs = page.query_selector_all(".botMsg, [class*='botItem'] , [class*='chatbot'] .msg")
        if not qs:
            return None
        q = (qs[-1].inner_text() or "").strip()
        # radio / chip options
        opts = page.query_selector_all("[class*='ssrc__radio'] label, [class*='chipsContainer'] .chip, .chatbot_Chip")
        if opts:
            texts = [(o.inner_text() or "").strip() for o in opts]
            idx = pick_option(q, texts)
            if idx is None:
                return f"unanswerable question: {q[:80]} | options: {' / '.join(texts)[:120]}"
            opts[idx].click()
        else:
            box = page.query_selector("div[contenteditable='true'], .chatbot_InputContainer input, textarea")
            if not box:
                return None
            a = answer(q)
            if a is None:
                return f"unanswerable question: {q[:80]}"
            box.click()
            box.fill(a) if box.evaluate("e => e.tagName") in ("INPUT", "TEXTAREA") else page.keyboard.type(a)
        sv = page.query_selector(".sendMsg, button:has-text('Save'), button:has-text('Submit')")
        if sv:
            sv.click()
    return "questionnaire too long"


SUCCESS_RX = re.compile(r"applied successfully|successfully applied|application (has been )?(sent|submitted)|you have (already )?applied|applied to this job", re.I)


def _applied(page) -> bool:
    try:
        t = page.evaluate("document.body.innerText")
        if SUCCESS_RX.search(t):
            return True
        # the Apply button turns into a disabled "Applied" button
        return bool(page.evaluate("""() => [...document.querySelectorAll('button, div[class*="apply"], span')]
            .some(e => /^\\s*applied\\s*$/i.test(e.innerText || '') && e.offsetParent !== null)"""))
    except Exception:
        return False


def apply(page, job: dict, resume_pdf, dry_run: bool) -> tuple[str, str]:
    """-> (status, reason). Assumes details(page, job) has just loaded the job page."""
    if page.query_selector("#company-site-button, button:has-text('Apply on company site')"):
        return "external", "redirects to company website"
    btn = page.query_selector("#apply-button, button.apply-button, button:has-text('Apply')")
    if not btn:
        if _applied(page) or page.query_selector("button:has-text('Applied')"):
            return "skipped", "already applied"
        return "failed", "no apply button found"
    label = (btn.inner_text() or "").strip().lower()
    if label.startswith("applied"):
        return "skipped", "already applied (Naukri shows 'Applied')"
    if "company" in label:
        return "external", "redirects to company website"
    if dry_run:
        return "dry_run", "would apply"
    btn.click()
    human_sleep((2, 4))
    check_blocked(page)
    why = _chatbot(page)
    if why:
        snap(page, "naukri_questionnaire")
        return "needs_manual", why
    human_sleep((2, 3))
    if _applied(page):
        return "applied", ""
    # clicked Apply but cannot prove it went through: never call it failed (it may have applied) and never retry blindly
    snap(page, "naukri_unconfirmed")
    try:
        txt = " ".join(page.evaluate("document.body.innerText").split())
    except Exception:
        txt = ""
    return "unconfirmed", "clicked Apply, success message not recognised - check Naukri > Applied jobs | page: " + txt[:160]
