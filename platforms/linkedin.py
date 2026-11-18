"""LinkedIn Easy Apply with a per-job tailored PDF. HIGH ACCOUNT RISK - keep caps low. Verify with --dry-run first."""
import re
from urllib.parse import quote
from core.answers import answer
from core.jd_parser import parse_experience
from .base import human_sleep, page_text, check_blocked, snap

NAME = "linkedin"
CARDS_JS = """() => [...document.querySelectorAll('li[data-occludable-job-id], .job-card-container')].map(e => {
  const t = s => (e.querySelector(s)||{}).innerText||'';
  return {id: e.getAttribute('data-occludable-job-id') || e.getAttribute('data-job-id') || '',
          title: t('.job-card-list__title, a.job-card-container__link'), company: t('.artdeco-entity-lockup__subtitle, .job-card-container__primary-description'),
          loc: t('.artdeco-entity-lockup__caption, .job-card-container__metadata-item')};}).filter(x => x.id)"""


def logged_in(page) -> bool:
    page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
    human_sleep((3, 5))
    return "login" not in page.url and "authwall" not in page.url and "checkpoint" not in page.url


def search(page, role: str, loc: str) -> list[dict]:
    remote = loc.lower() == "remote"
    url = ("https://www.linkedin.com/jobs/search/?keywords=" + quote(role) + "&location=" + quote("India" if remote else loc) +
           "&f_AL=true&f_E=2%2C3&f_TPR=r604800" + ("&f_WT=2" if remote else ""))
    page.goto(url, wait_until="domcontentloaded")
    human_sleep((4, 7))
    check_blocked(page)
    jobs = []
    for c in page.evaluate(CARDS_JS):
        jobs.append({"platform": NAME, "job_id": c["id"], "title": c["title"].split("\n")[0].strip(), "company": c["company"].strip(),
                     "location": c["loc"].strip() or loc, "remote": remote or "remote" in c["loc"].lower(), "exp_text": "",
                     "url": f"https://www.linkedin.com/jobs/view/{c['id']}/"})
    return jobs


def details(page, job: dict) -> dict:
    page.goto(job["url"], wait_until="domcontentloaded")
    human_sleep((3, 6))
    check_blocked(page)
    job["description"] = page_text(page, ["#job-details", ".jobs-description__content", ".jobs-box__html-content"])
    lo, hi = parse_experience(job["description"][:3000])
    job["exp_min"], job["exp_max"] = lo, hi
    job["exp_text"] = f"{lo}+ yrs" if lo is not None else ""
    return job


MODAL = ".jobs-easy-apply-modal, [role='dialog']"


def _fill_step(modal) -> str | None:
    """Fill visible inputs from config. Return a reason if a required question can't be answered."""
    for grp in modal.query_selector_all(".jobs-easy-apply-form-section__grouping, .fb-dash-form-element"):
        label_el = grp.query_selector("label, legend, span.fb-dash-form-element__label")
        label = (label_el.inner_text() if label_el else "").strip()
        inp = grp.query_selector("input[type='text'], input[type='number'], input[type='tel'], textarea")
        sel = grp.query_selector("select")
        radios = grp.query_selector_all("input[type='radio']")
        if inp and not (inp.input_value() or "").strip():
            a = answer(label)
            if a is None:
                return f"unanswerable question: {label[:80]}"
            inp.fill(a)
        elif sel and (sel.input_value() in ("", "Select an option")):
            a = answer(label, "choice")
            if a is None:
                return f"unanswerable question: {label[:80]}"
            try:
                sel.select_option(label=a)
            except Exception:
                return f"dropdown option not found: {label[:80]}"
        elif radios and not any(r.is_checked() for r in radios):
            a = answer(label, "choice")
            if a is None:
                return f"unanswerable question: {label[:80]}"
            lab = next((r for r in grp.query_selector_all("label") if (r.inner_text() or "").strip().lower() == a.lower()), None)
            if not lab:
                return f"radio option not found: {label[:80]}"
            lab.click()
    return None


def apply(page, job: dict, resume_pdf, dry_run: bool) -> tuple[str, str]:
    btn = page.query_selector("button.jobs-apply-button, button[aria-label*='Easy Apply']")
    if not btn or "easy apply" not in (btn.inner_text() or "").lower():
        return "skipped", "not an Easy Apply job (or already applied)"
    if dry_run:
        return "dry_run", "would apply"
    btn.click()
    human_sleep((2, 4))
    check_blocked(page)
    for step in range(6):
        modal = page.query_selector(MODAL)
        if not modal:
            return "failed", "easy apply dialog missing"
        up = modal.query_selector("input[type='file']")
        if up and resume_pdf:
            up.set_input_files(str(resume_pdf))
            human_sleep((2, 4))
        why = _fill_step(modal)
        if why:
            snap(page, "linkedin_question")
            _discard(page)
            return "needs_manual", why
        submit = modal.query_selector("button[aria-label='Submit application']")
        if submit:
            fu = modal.query_selector("label:has-text('Follow')")      # untick 'follow company'
            if fu:
                try:
                    fu.click()
                except Exception:
                    pass
            submit.click()
            human_sleep((3, 5))
            done = re.search(r"application (was )?sent|applied", page.evaluate("document.body.innerText"), re.I)
            _close_done(page)
            return ("applied", "") if done else ("failed", "could not confirm submit")
        nxt = modal.query_selector("button[aria-label='Continue to next step'], button[aria-label='Review your application']")
        if not nxt:
            _discard(page)
            return "needs_manual", "unexpected form layout"
        nxt.click()
        human_sleep((1.5, 3))
        if modal.query_selector(".artdeco-inline-feedback--error"):
            snap(page, "linkedin_form_error")
            _discard(page)
            return "needs_manual", "form validation error"
    _discard(page)
    return "needs_manual", "form longer than 6 steps"


def _discard(page):
    try:
        page.click("button[aria-label='Dismiss']")
        human_sleep((1, 2))
        page.click("button[data-control-name='discard_application_confirm_btn'], button:has-text('Discard')")
    except Exception:
        pass


def _close_done(page):
    try:
        page.click("button[aria-label='Dismiss']")
    except Exception:
        pass
