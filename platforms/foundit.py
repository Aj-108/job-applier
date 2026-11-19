"""Foundit (formerly Monster India) - search, read JD, one-click apply.

NOT VERIFIED LIVE. Foundit refused every automated fetch while this was written, so URLs and selectors below are best
guesses. Do this before trusting it:
  1. python main.py login foundit
  2. python main.py probe foundit        -> saves a screenshot, HTML and a list of what it found in out/probe/
  3. fix SEARCH / selectors from that, then  python main.py run --only foundit --dry-run --cap 5
Safe by design: anything unclear (extra questions, unknown button, no proof of success) is NOT guessed:
questions -> needs_manual, company-site jobs -> external, unproven click -> unconfirmed (never re-clicked)."""
import math
import re
from urllib.parse import quote
from core.jd_parser import parse_experience
from .base import human_sleep, page_text, check_blocked, snap, Blocked

NAME = "foundit"
BASE = "https://www.foundit.in"
LOGIN_URL = f"{BASE}/rio/login"
DASHBOARD = f"{BASE}/seeker/dashboard"
JD_SELECTORS = ["[class*='jobDescription']", "[class*='job-description']", "[class*='JobDescription']", "#jobDescription", "section[class*='desc']"]

# VERIFIED LIVE 2026-11-19 (read-only probe): the results page is a split view. Each result is <div class="cardContainer" id="<jobId>">
# (.jobTitle, .companyName, .experienceSalary .details, .location). Clicking a card loads the job on the right WITHOUT changing the
# URL: h1.jdTitle, #jobDescription, and the button #applyNowBtn ("Quick Apply"). There are no per-job hrefs.
CARDS_JS = """() => [...document.querySelectorAll('.cardContainer')].map(c => {
  const g = s => ((c.querySelector(s) || {}).innerText || '').trim();
  return {id: c.id, title: g('.jobTitle'), company: g('.companyName'), exp: g('.experienceSalary .details'),
          loc: g('.location'), text: c.innerText.slice(0, 700)}; }).filter(c => c.id && c.title)"""


def logged_in(page) -> bool:
    page.goto(DASHBOARD, wait_until="domcontentloaded")
    human_sleep((3, 5))
    return "login" not in page.url.split("?")[0]


def queries(cfg: dict):
    """(role, location) searches; Foundit filters by an experience RANGE around your years, so one pass is enough."""
    for loc in cfg["locations"]:
        for role in cfg["roles"]:
            yield role, loc


def experience_range(cfg_years: float | None) -> tuple[int, int]:
    yrs = float(cfg_years or 2)
    return max(0, math.floor(yrs) - 1), math.ceil(yrs) + 1       # 2.8 yrs -> 1~4


def search_url(role: str, loc: str, years: float | None = 2.5) -> str:
    lo, hi = experience_range(years)
    remote = loc.lower() == "remote"
    q = f"query={quote(role)}&experienceRanges={lo}~{hi}&freshness=7&sort=1"
    q += "&workFromHome=true" if remote else f"&locations={quote(loc)}"
    return f"{BASE}/srp/results?{q}"


def search(page, role: str, loc: str) -> list[dict]:
    from core.config import load_config
    years = load_config().get("profile", {}).get("total_experience_years")
    url = search_url(role, loc, years)
    page.goto(url, wait_until="domcontentloaded")
    human_sleep((4, 7))
    check_blocked(page)
    try:
        page.wait_for_selector(".cardContainer", timeout=12000)
    except Exception:
        return []
    remote = loc.lower() == "remote"
    jobs = []
    for c in page.evaluate(CARDS_JS):
        lo, hi = parse_experience(c["exp"])
        jobs.append({"platform": NAME, "job_id": c["id"], "title": c["title"], "company": c["company"],
                     "location": c["loc"] or loc, "remote": remote or bool(re.search(r"remote|work from home|wfh", c["text"], re.I)),
                     "exp_text": c["exp"], "exp_min": lo, "exp_max": hi,
                     "url": url, "_search_url": url})        # no per-job page exists: the link is the results page it was found on
    return jobs


def _norm(t: str) -> str:
    return re.sub(r"\W+", " ", (t or "").lower()).strip()


def details(page, job: dict) -> dict:
    """Select the job's card on its results page; the job (description + Quick Apply) loads in the right-hand pane."""
    url = job.get("_search_url") or job["url"]
    sel = f"[id='{job['job_id']}']"
    if not page.query_selector(f".cardContainer{sel}") or "/srp/results" not in page.url:
        page.goto(url, wait_until="domcontentloaded")
        human_sleep((4, 6))
        check_blocked(page)
        page.wait_for_selector(".cardContainer", timeout=12000)
    page.click(f"{sel} .jobTitle")
    human_sleep((2, 4))
    check_blocked(page)
    for _ in range(10):                                       # wait until the pane shows THIS job, not the previous one
        shown = page.evaluate("(document.querySelector('h1.jdTitle') || {}).innerText || ''")
        if _norm(shown) == _norm(job["title"]):
            break
        human_sleep((1, 1.5))
    job["description"] = page_text(page, ["#jobDescription"] + JD_SELECTORS)
    return job


# ---- applying ---------------------------------------------------------------------------------------------------------
APPLY_RX = re.compile(r"^\s*(quick\s+)?apply( now)?\s*$", re.I)
APPLIED_RX = re.compile(r"applied|already applied", re.I)
SUCCESS_RX = re.compile(r"applied successfully|successfully applied|application (has been )?(sent|submitted)|"
                        r"you have (already )?applied|thank you for applying", re.I)

LABELS_JS = "() => [...document.querySelectorAll('#applyNowBtn')].filter(b => b.offsetParent !== null).map(b => (b.innerText || '').trim())"
FORM_JS = """() => [...document.querySelectorAll('[role="dialog"] input:not([type=hidden]), [role="dialog"] textarea, [role="dialog"] select,'
  + ' [class*="modal" i] input:not([type=hidden]), [class*="modal" i] textarea, [class*="modal" i] select, [class*="popup" i] input:not([type=hidden])')]
  .filter(e => e.offsetParent !== null && e.type !== 'file').length"""


def _labels(page) -> list[str]:
    try:
        return page.evaluate(LABELS_JS)
    except Exception:
        return []


def precheck(page, job: dict) -> tuple[str, str] | None:
    """Outcome known WITHOUT clicking, else None = go ahead and click."""
    labels = _labels(page)
    if not labels:
        return "failed", "no Apply button in the Foundit job pane"
    lab = labels[0]
    if APPLY_RX.match(lab):
        return None
    if APPLIED_RX.search(lab):
        return "skipped", f"already applied (Foundit shows '{lab}')"
    if re.search(r"company|website|site", lab, re.I):
        return "external", "redirects to company website"
    return "failed", f"unrecognised Apply button '{lab}'"


def _applied(page) -> bool:
    try:
        if SUCCESS_RX.search(page.evaluate("document.body.innerText")):
            return True
        return any(APPLIED_RX.search(l) and not APPLY_RX.match(l) for l in _labels(page))
    except Exception:
        return False


def apply(page, job: dict, resume_pdf, dry_run: bool) -> tuple[str, str]:
    """-> (status, reason). Assumes details(page, job) has just loaded the job page."""
    pre = precheck(page, job)
    if pre:
        return pre
    if dry_run:
        return "dry_run", "would apply"
    ctx = page.context
    before = len(ctx.pages)
    page.evaluate("() => [...document.querySelectorAll('#applyNowBtn')].find(b => b.offsetParent !== null).click()")
    human_sleep((3, 5))
    check_blocked(page)
    if len(ctx.pages) > before:                                  # opened the company's own site
        for p in ctx.pages[before:]:
            try:
                p.close()
            except Exception:
                pass
        return "external", "Apply opened the company website"
    try:
        extra = page.evaluate(FORM_JS)
    except Exception:
        extra = 0
    if extra:                                                     # a form with questions: never guess answers
        snap(page, "foundit_questions")
        return "needs_manual", f"Foundit asked {extra} extra question field(s) - finish on the site"
    human_sleep((2, 3))
    if _applied(page):
        return "applied", ""
    snap(page, "foundit_unconfirmed")
    try:
        txt = " ".join(page.evaluate("document.body.innerText").split())
    except Exception:
        txt = ""
    return "unconfirmed", "clicked Apply, success not recognised - check Foundit > Applied jobs | page: " + txt[:160]


# ---- developer aid ----------------------------------------------------------------------------------------------------
def probe(page, url: str | None = None) -> dict:
    """What the page really looks like, so selectors can be fixed from facts. Reads only, clicks nothing."""
    page.goto(url or search_url("full stack developer", "Delhi"), wait_until="domcontentloaded")
    human_sleep((5, 8))
    snap(page, "foundit_probe")
    anchors = page.evaluate("""() => [...document.querySelectorAll('a')].slice(0, 400).map(a => ({href: a.href, t: (a.innerText||'').trim().slice(0,60)}))
        .filter(a => /job|apply|login/i.test(a.href + a.t))""")
    return {"url": page.url, "title": page.title(), "cards": page.evaluate(CARDS_JS)[:5], "job_links": anchors[:40],
            "buttons": _labels(page), "html": page.content()}
