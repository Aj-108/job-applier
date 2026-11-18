"""Naukri.com - search, read JD, one-click apply (+ questionnaire bot). Selectors are best-effort: verify with --dry-run."""
import math
import re
from urllib.parse import quote
from core.answers import answer, pick_option
from core.jd_parser import parse_experience
from .base import human_sleep, page_text, check_blocked, snap, Blocked

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


PAGE_SIZE = 20          # Naukri shows 20 cards per result page


def queries(cfg: dict):
    """(role, location, experience) searches. Naukri's experience=N filter returns jobs whose range contains N, so with
    2.8 years we search both 2 and 3 ("3-5 yrs" jobs never show up under experience=2)."""
    yrs = float(cfg.get("profile", {}).get("total_experience_years") or 2)
    exps = sorted({max(0, math.floor(yrs)), max(0, math.ceil(yrs))})
    for loc in cfg["locations"]:
        for role in cfg["roles"]:
            for exp in exps:
                yield role, loc, exp


def search_url(role: str, loc: str, exp: int = 2, pg: int = 1) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", role.lower()).strip("-")
    q = f"experience={exp}&jobAge=7"
    suffix = f"-{pg}" if pg > 1 else ""            # page 2 = ".../react-developer-jobs-in-delhi-2?..."
    if loc.lower() == "remote":
        return f"https://www.naukri.com/{slug}-jobs{suffix}?k={quote(role)}&wfhType=2&{q}"
    return f"https://www.naukri.com/{slug}-jobs-in-{loc.lower()}{suffix}?k={quote(role)}&l={quote(loc)}&{q}"


def search(page, role: str, loc: str, exp: int = 2) -> list[dict]:
    """Page 1 of the results, plus page 2 when page 1 was full (new jobs are often past the first 20)."""
    jobs = _search_page(page, role, loc, exp, 1)
    if len(jobs) >= PAGE_SIZE:
        try:
            jobs += _search_page(page, role, loc, exp, 2)
        except Blocked:
            raise
        except Exception:
            pass                                    # page 2 is a bonus: keep page 1's jobs
    return jobs


def _search_page(page, role: str, loc: str, exp: int, pg: int) -> list[dict]:
    page.goto(search_url(role, loc, exp, pg), wait_until="domcontentloaded")
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


HISTORY_URL = "https://www.naukri.com/myapply/historypage"
ENTRIES_JS = """() => [...document.querySelectorAll('.jdTitle')].map(t => ({title: t.innerText.trim(),
  company: ((t.parentElement.parentElement.querySelector('.company') || {}).innerText || '').split('\\n')[0].trim()}))
  .filter(e => e.title && e.company)"""


def _history(page, tab: str) -> list[dict]:
    """Application History > one filter tab, fully loaded (10 per load; 'loadMoreLnk' loads the next 10)."""
    page.goto(HISTORY_URL, wait_until="domcontentloaded")
    page.wait_for_selector(".jdTitle", timeout=20000)
    human_sleep((2, 4))
    check_blocked(page)
    page.locator("li.filterComp", has_text=tab).click()
    human_sleep((3, 4))
    for _ in range(30):                                       # 10 per load: 300 applications is plenty
        n = page.evaluate("() => document.querySelectorAll('.jdTitle').length")
        if not page.evaluate("() => { const a = document.querySelector('.loadMoreLnk'); if (!a) return false; a.scrollIntoView(); a.click(); return true; }"):
            break
        human_sleep((3, 4))
        if page.evaluate("() => document.querySelectorAll('.jdTitle').length") == n:
            break
    return page.evaluate(ENTRIES_JS)


def applied_list(page) -> list[dict]:
    """What Naukri says you applied to ON Naukri (My Naukri > Application History > 'Applies on Naukri'). Read-only."""
    return _history(page, "Applies on Naukri")


def external_list(page) -> list[dict]:
    """Jobs Naukri files under 'Applies on External Site': a click on Apply there only redirects, it is NOT an application."""
    return _history(page, "Applies on External Site")


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


# the questionnaire drawer; everything is looked up INSIDE it (a page-wide "button:has-text('Save')" hit a button
# behind the drawer's overlay -> click timed out)
CHATBOT = "[id$='ChatbotContainer'], ._chatBotContainer"


def _js_click(handle):
    handle.evaluate("e => e.click()")


def _chatbot(page) -> str | None:
    """Answer Naukri's post-apply questionnaire. Returns None if finished, or a reason string if we must stop."""
    for _ in range(10):
        human_sleep((1, 2))
        if _applied(page):
            return None
        cb = page.query_selector(CHATBOT)
        root = cb or page
        qs = root.query_selector_all(".botMsg, [class*='botItem'] , [class*='chatbot'] .msg")
        if not qs:
            return None
        q = (qs[-1].inner_text() or "").strip()
        # radio / chip options
        opts = [o for o in root.query_selector_all("[class*='ssrc__radio'] label, [class*='chipsContainer'] .chip, .chatbot_Chip")
                if o.is_visible()]
        if opts:
            texts = [(o.inner_text() or "").strip() for o in opts]
            idx = pick_option(q, texts)
            if idx is None:
                return f"unanswerable question: {q[:80]} | options: {' / '.join(texts)[:120]}"
            _js_click(opts[idx])
        else:
            box = root.query_selector("div[contenteditable='true'], .chatbot_InputContainer input, textarea")
            if not box:
                return None
            a = answer(q)
            if a is None:
                return f"unanswerable question: {q[:80]}"
            box.click()
            box.fill(a) if box.evaluate("e => e.tagName") in ("INPUT", "TEXTAREA") else page.keyboard.type(a)
        sv = next((b for b in root.query_selector_all(".sendMsg, button:has-text('Save'), button:has-text('Submit')")
                   if b.is_visible()), None)
        if sv:
            _js_click(sv)
    return "questionnaire too long"


# Naukri's real success banner (seen live): Applied to "Full Stack Developer"  /  Next step: Start your interview preparation...
SUCCESS_RX = re.compile(r"applied successfully|successfully applied|application (has been )?(sent|submitted)|you have (already )?applied|"
                        r"applied to this job|applied to\s+[\"\u201c\u2018']", re.I)


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


APPLY_BTN = "#apply-button, button.apply-button, button:has-text('Apply')"


def precheck(page, job: dict) -> tuple[str, str] | None:
    """Outcome known WITHOUT clicking (company site / already applied / no button), else None = go ahead and click."""
    if page.query_selector("#company-site-button, button:has-text('Apply on company site')"):
        return "external", "redirects to company website"
    btn = page.query_selector(APPLY_BTN)
    if not btn:
        if _applied(page) or page.query_selector("button:has-text('Applied')"):
            return "skipped", "already applied"
        return "failed", "no apply button found"
    label = (btn.inner_text() or "").strip().lower()
    if label.startswith("applied"):
        return "skipped", "already applied (Naukri shows 'Applied')"
    if "company" in label:
        return "external", "redirects to company website"
    return None


def apply(page, job: dict, resume_pdf, dry_run: bool) -> tuple[str, str]:
    """-> (status, reason). Assumes details(page, job) has just loaded the job page."""
    pre = precheck(page, job)
    if pre:
        return pre
    btn = page.query_selector(APPLY_BTN)
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
