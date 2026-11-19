"""Instahyre - the 'Recommended jobs' list (already matched to your Instahyre profile), read straight from the page's
Angular data. Each job: 'View job' opens a popup with the full description and an Apply button.
Verified live 2026-11-18: cards = .employer-block (only 'View job' + 'Not interested' on the card), popup =
.candidate-apply-modal, description = .right-section-modal, list paged 30 at a time ('Next').
'Not interested' is NEVER clicked."""
import re
from core.jd_parser import parse_experience
from .base import human_sleep, check_blocked, snap

NAME = "instahyre"
OPPS = "https://www.instahyre.com/candidate/opportunities/?matching=true"
MAX_PAGES = 5           # recommended list (30 per page)
SEARCH_SKILLS = ["React.js", "Node.js", "Next.js", "MERN Stack", "Shopify"]   # default; config: platforms.instahyre.skills
SEARCH_PAGES = 10       # per skill search (a React.js search has ~1400 jobs, all India; we keep your cities only)
MODAL = ".candidate-apply-modal"
APPLY_BTN = f"{MODAL} .bar-actions .apply button.btn-primary"
CLOSE_BTN = f"{MODAL} .application-modal-close"

# every job card's data from Angular: stable opportunity id, title, company, location(s), skills, status
OPPS_JS = """() => [...document.querySelectorAll('.employer-block')].map(e => {
  const s = window.angular && angular.element(e).scope(); const o = s && s.opp; if (!o) return null;
  const j = o.job || {}; const loc = Array.isArray(j.locations) ? j.locations.join(', ') : (j.locations || '');
  return {id: 'j' + j.id, title: j.title || j.candidate_title || '', company: (o.employer || {}).company_name || j.hiring_company_name || '',
          loc, keywords: (j.keywords || []).join(', '), status: o.interview_status, active: j.is_active !== false,
          url: j.opportunity_url ? 'https://www.instahyre.com' + j.opportunity_url : ''};}).filter(Boolean)"""

# a job's key everywhere = 'j' + job id (search results have no opportunity id; the job id is on every card)
KEY_JS = "e => { const o = (angular.element(e).scope() || {}).opp || {}; return 'j' + (o.job || {}).id; }"
FIRST_ID_JS = (f"() => {{ const e = document.querySelector('.employer-block'); return e ? ({KEY_JS})(e) : ''; }}")

# Instahyre spells places its own way; config.yaml locations -> what Instahyre may show
LOC_ALIASES = {
    "remote": ["remote", "work from home", "wfh", "anywhere"],
    "gurugram": ["gurugram", "gurgaon"],
    "delhi": ["delhi", "new delhi", "ncr"],
    "noida": ["noida"],
    "mohali": ["mohali", "sas nagar"],
    "chandigarh": ["chandigarh", "panchkula"],
}

CONFIRM_RX = re.compile(r"applied successfully|successfully applied|application (has been )?(sent|submitted)|"
                        r"you have (already )?applied|thank you for applying|profile (has been )?(shared|sent)", re.I)


def split_title(title: str, company: str) -> tuple[str, str]:
    """Instahyre cards read 'Swiggy - React.js Developer'; the selectors return that whole string for both fields.
    -> (role, company)."""
    t, c = (title or "").strip(), (company or "").strip()
    if (not c or c == t) and " - " in t:
        co, role = t.split(" - ", 1)
        return role.strip(), co.strip()
    return t, c


def location_ok(loc_text: str, wanted: list[str]) -> bool:
    low = (loc_text or "").lower()
    for w in wanted:
        if any(a in low for a in LOC_ALIASES.get(w.lower(), [w.lower()])):
            return True
    return False


def logged_in(page) -> bool:
    page.goto(OPPS, wait_until="domcontentloaded")
    human_sleep((3, 5))
    return "login" not in page.url and "signup" not in page.url


def queries(cfg: dict):
    """The recommended list (matched to your Instahyre profile) page by page, then skill searches page by page.
    (skill, page); skill '' = recommended."""
    pc = cfg.get("platforms", {}).get("instahyre", {})
    for pg in range(1, MAX_PAGES + 1):
        yield "", pg
    for pg in range(1, int(pc.get("search_pages", SEARCH_PAGES)) + 1):
        for skill in pc.get("skills", SEARCH_SKILLS):
            yield skill, pg


def _js_click(handle):
    """Click through Angular's handler directly: an (invisible) job-popup layer can sit over the page and swallow
    real mouse clicks."""
    handle.evaluate("e => e.click()")


def _skill_search(page, skill: str) -> bool:
    """Type the skill, pick the exact suggestion from the search box's own dropdown, press Search."""
    inp = page.query_selector("input[placeholder*='skills or title' i]")
    if not inp:
        return False
    inp.click()
    inp.type(skill, delay=60)
    human_sleep((1.5, 2.5))
    first = page.evaluate(FIRST_ID_JS)
    picked = page.evaluate("""s => { const inp = document.querySelector("input[placeholder*='skills or title' i]");
        const box = inp.closest('div:has(button.skills-search-btn)') || inp.parentElement.parentElement;
        const el = [...box.querySelectorAll('*')].find(e => e.children.length === 0 && e.offsetParent &&
                    (e.innerText || '').trim().toLowerCase() === s.toLowerCase());
        if (!el) return false; el.click(); return true; }""", skill)
    if not picked:
        page.keyboard.press("Escape")
        return False
    human_sleep((1, 2))
    _js_click(page.query_selector("button.skills-search-btn"))
    page.wait_for_function(f"t => ({FIRST_ID_JS})() !== t", arg=first, timeout=20000)
    human_sleep((2, 3))
    return bool(re.search(r"Search Results\s*\d+", page.evaluate("document.body.innerText")))


def _open_list(page, pg: int, skill: str = "") -> bool:
    """Fresh list (recommended, or a skill search) on page pg. False if that search / page does not exist."""
    page.goto(OPPS, wait_until="domcontentloaded")
    page.wait_for_selector(".employer-block", timeout=20000)
    human_sleep((2, 4))
    check_blocked(page)
    if skill and not _skill_search(page, skill):
        return False
    if pg == 1:
        return True
    # numbered pager (ng-click="nthPage(n)"). Not "li:has-text('Next')": that also matches the "Next.js" skill tags.
    btn = next((li for li in page.query_selector_all("li[ng-click*='nthPage']")
                if (li.inner_text() or "").strip() == str(pg) and li.is_visible()), None)
    if not btn:
        return False
    first = page.evaluate(FIRST_ID_JS)
    _js_click(btn)
    page.wait_for_function(f"t => ({FIRST_ID_JS})() !== t", arg=first, timeout=15000)
    human_sleep((2, 3))
    return True


def search(page, skill: str = "", pg: int = 1) -> list[dict]:
    from core.config import load_config
    wanted = load_config()["locations"]
    if not _open_list(page, pg, skill):
        return []
    jobs = []
    for o in page.evaluate(OPPS_JS):
        if o["status"] != 0 or not o["active"]:            # 0 = PENDING (not applied / not declined yet)
            continue
        if not location_ok(o["loc"], wanted):
            continue
        role, company = split_title(o["title"], o["company"])
        jobs.append({"platform": NAME, "job_id": o["id"], "title": role, "company": company, "location": o["loc"],
                     "remote": location_ok(o["loc"], ["remote"]), "exp_text": "", "exp_min": None, "exp_max": None,
                     "url": o["url"] or OPPS, "description": o["keywords"], "_q": (pg, skill)})
    return jobs


def _card(page, job_id: str):
    for e in page.query_selector_all(".employer-block"):
        if e.evaluate(KEY_JS) == job_id:
            return e
    return None


def _close(page):
    try:
        btn = page.query_selector(CLOSE_BTN)
        if btn and btn.is_visible():
            btn.click()
        else:
            page.keyboard.press("Escape")
        human_sleep((1, 2))
    except Exception:
        pass


def details(page, job: dict) -> dict:
    """Open the job's popup and read the full description + experience range from it."""
    _close(page)
    card = _card(page, job["job_id"])
    if not card:                                            # the list re-rendered (e.g. after an apply): reload its page
        _open_list(page, *job.get("_q", (1, "")))
        card = _card(page, job["job_id"])
    if not card:
        raise RuntimeError("job card not on the list any more")
    _js_click(card.query_selector("button.btn-interested, button:has-text('View job')"))
    page.wait_for_selector(f"{MODAL} .right-section-modal", state="visible", timeout=15000)
    human_sleep((2, 4))
    check_blocked(page)
    text = page.inner_text(f"{MODAL} .right-section-modal")
    job["description"] = text[:9000]
    lo, hi = parse_experience(text[:600])                   # header line: "Gurgaon  2-6 Years"
    job["exp_min"], job["exp_max"] = lo, hi
    job["exp_text"] = f"{lo}-{hi} yrs" if lo is not None and hi is not None else (f"{lo}+ yrs" if lo is not None else "")
    return job


def _applied_count(page) -> int | None:
    m = re.search(r"Applied jobs\s*(\d+)", page.evaluate("document.body.innerText"))
    return int(m.group(1)) if m else None


def precheck(page, job: dict):
    """No Apply button in the popup (already applied / sourced job): nothing to click."""
    btn = page.query_selector(APPLY_BTN)
    if not btn or not btn.is_visible():
        _close(page)
        return "skipped", "no Apply button in the job popup (already applied?)"
    return None


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
    """Click Apply in the job popup (opened by details()). Instahyre sends your Instahyre profile + resume."""
    pre = precheck(page, job)
    if pre:
        return pre
    if dry_run:
        _close(page)
        return "dry_run", "would apply"
    before = _applied_count(page)
    _js_click(page.query_selector(APPLY_BTN))
    human_sleep((3, 5))
    check_blocked(page)
    after = _applied_count(page)
    text = page.evaluate("document.body.innerText")
    status = page.evaluate("""id => { for (const e of document.querySelectorAll('.employer-block')) {
        const o = ((angular.element(e).scope() || {}).opp || {}); if ('j' + (o.job || {}).id === id) return o.interview_status; }
        return null; }""", job["job_id"])
    if (before is not None and after is not None and after > before) or status == 1 or CONFIRM_RX.search(text):
        _close(page)
        return "applied", ""
    # clicked but nothing proves it went through: never call it applied (the Excel would be wrong) and never click again
    snap(page, "instahyre_unconfirmed")
    _close(page)
    return "unconfirmed", (f"clicked Apply, success not recognised (applied count {before} -> {after}) - check Instahyre > "
                           f"Applied jobs | page: " + " ".join(text.split())[:120])
