"""Indeed India - search, read JD, 'Easily apply' (Indeed Apply) multi-step form with the main resume.
Jobs that send you to the company's site are marked `external` (they go to the Antigravity hand-off).
Selectors are best-effort and NOT yet verified live: rehearse with --dry-run --only indeed first.
Indeed uses Cloudflare bot checks: visible browser + human pacing only; a challenge stops the platform for the run."""
import re
from urllib.parse import quote
from core.answers import answer, pick_option
from core.jd_parser import parse_experience
from .base import human_sleep, page_text, check_blocked, snap, Blocked

NAME = "indeed"
BASE = "https://in.indeed.com"
JD_SELECTORS = ["[data-testid='viewjob-job-content']", "#jobDescriptionText", "[data-testid='jobsearch-JobComponent-description']", ".jobsearch-jobDescriptionText"]
CARDS_JS = """() => [...document.querySelectorAll('div.job_seen_beacon, li div.cardOutline')].map(card => {
  const a = card.querySelector('a[data-jk], h2.jobTitle a, a.jcs-JobTitle');
  const g = s => (card.querySelector(s)||{}).innerText||'';
  let id = a ? a.getAttribute('data-jk') : '';
  if (!id && a) { try { id = new URL(a.href).searchParams.get('jk') || ''; } catch (e) {} }
  return {id, title: a ? a.innerText : g('h2.jobTitle'),
          company: g('[data-testid="company-name"], .companyName'),
          loc: g('[data-testid="text-location"], .companyLocation'),
          easy: /easily apply|responsive employer/i.test(card.innerText), text: card.innerText.slice(0, 600)};})"""

# verified live 2026-11-18: the new job page renders "Apply now" as a LINK (<a>), company-site jobs say "Apply on company site"
APPLY_BTN = ("[data-testid='viewjob-indeed-apply'], #indeedApplyButton, button[id*='indeedApplyButton'], "
             ".ia-IndeedApplyButton button")
EXTERNAL_BTN = ("a:has-text('Apply on company site'), button:has-text('Apply on company site'), "
                "#applyButtonLinkContainer a, [aria-label*='company site' i]")
CONTINUE_BTN = ("button:has-text('Continue'), button:has-text('Review your application'), "
                "button[data-testid*='continue' i]")
SUBMIT_BTN = "button:has-text('Submit your application'), button:has-text('Submit application')"
SUCCESS_RX = re.compile(r"your application has been submitted|application submitted|you(?:'ve| have) applied|"
                        r"application (?:was )?sent", re.I)
ERROR_SEL = "[role='alert'], [class*='error' i]:visible, [id*='error' i]:visible"

# label every empty form field on the current step and describe it, so Python can answer it from config.yaml
FIELDS_JS = """() => {
  const vis = e => e.offsetParent !== null || e.type === 'radio' || e.type === 'checkbox';
  const labelOf = e => {
    const fs = e.closest('fieldset'); if ((e.type === 'radio' || e.type === 'checkbox') && fs) {
      const lg = fs.querySelector('legend'); if (lg) return lg.innerText; }
    if (e.id) { const l = document.querySelector(`label[for="${CSS.escape(e.id)}"]`); if (l) return l.innerText; }
    const ids = (e.getAttribute('aria-labelledby') || '').split(' ').filter(Boolean);
    if (ids.length) return ids.map(i => (document.getElementById(i) || {}).innerText || '').join(' ');
    const l = e.closest('label'); if (l) return l.innerText;
    return e.getAttribute('aria-label') || e.getAttribute('placeholder') || ''; };
  const out = []; const groups = {}; let n = 0;
  for (const e of document.querySelectorAll('input, select, textarea')) {
    if (!vis(e) || e.disabled || ['hidden', 'submit', 'button', 'file', 'search'].includes(e.type)) continue;
    if (e.type === 'radio' || e.type === 'checkbox') {
      const k = e.type + ':' + (e.name || labelOf(e));
      if (!groups[k]) { groups[k] = {kind: e.type, label: labelOf(e), opts: [], idx: [], checked: false}; out.push(groups[k]); }
      const g = groups[k]; e.setAttribute('data-ja', String(n));
      const ol = e.id ? document.querySelector(`label[for="${CSS.escape(e.id)}"]`) : e.closest('label');
      g.opts.push((ol ? ol.innerText : e.value) || ''); g.idx.push(n++); g.checked = g.checked || e.checked;
      continue; }
    e.setAttribute('data-ja', String(n));
    const f = {kind: e.tagName === 'SELECT' ? 'select' : 'text', label: labelOf(e), idx: [n++], type: e.type || '',
               empty: e.tagName === 'SELECT' ? (!e.value || e.selectedIndex <= 0) : !(e.value || '').trim(), opts: []};
    if (f.kind === 'select') f.opts = [...e.options].map(o => o.innerText);
    out.push(f); }
  return out; }"""


def logged_in(page) -> bool:
    page.goto("https://myjobs.indeed.com/applied", wait_until="domcontentloaded")
    human_sleep((3, 5))
    u = page.url
    return "secure.indeed.com/auth" not in u and "/account/login" not in u


def search(page, role: str, loc: str) -> list[dict]:
    """Page 1 of the last-7-days results. 'Easily apply' jobs first (those we can apply to here)."""
    remote = loc.lower() == "remote"
    url = f"{BASE}/jobs?q={quote(role)}&l={quote('Remote' if remote else loc)}&fromage=7"
    page.goto(url, wait_until="domcontentloaded")
    human_sleep((3, 6))
    check_blocked(page)
    jobs, ids = [], set()
    for c in page.evaluate(CARDS_JS):
        if not c["id"] or c["id"] in ids:
            continue
        ids.add(c["id"])
        lo, hi = parse_experience(c["text"])
        jobs.append({"platform": NAME, "job_id": c["id"], "title": c["title"].split("\n")[0].strip(),
                     "company": c["company"].strip(), "location": c["loc"].strip() or loc,
                     "remote": remote or bool(re.search(r"remote|work from home", c["loc"], re.I)),
                     "exp_text": f"{lo}-{hi} yrs" if lo is not None and hi is not None else "",
                     "exp_min": lo, "exp_max": hi, "url": f"{BASE}/viewjob?jk={c['id']}", "_easy": c["easy"]})
    jobs.sort(key=lambda j: not j["_easy"])
    return jobs


def card_outcome(job: dict):
    """Known from the search card, no page load: no 'Easily apply' label = the company's own site (hand-off).
    Opening every such page is what triggers Indeed's Cloudflare check."""
    if not job.get("_easy"):
        return "external", "company site (Indeed card has no 'Easily apply'; page not opened)"
    return None


def details(page, job: dict) -> dict:
    page.goto(job["url"], wait_until="domcontentloaded")
    human_sleep((2, 5))
    check_blocked(page)
    job["description"] = page_text(page, JD_SELECTORS)
    if not job.get("company"):
        el = page.query_selector("[data-testid='inlineHeader-companyName'], [data-company-name]")
        job["company"] = (el.inner_text() if el else "").strip()
    if job.get("exp_min") is None:                   # Indeed has no experience field: read it from the description
        lo, hi = parse_experience(job["description"][:4000])
        job["exp_min"], job["exp_max"] = lo, hi
        job["exp_text"] = f"{lo}+ yrs" if lo is not None else ""
    return job


def upload_profile_resume(page, pdf_path) -> bool:
    page.goto("https://profile.indeed.com/", wait_until="domcontentloaded")
    human_sleep((3, 5))
    try:
        page.set_input_files("input[type='file']", str(pdf_path))
        human_sleep((4, 7))
        return True
    except Exception:
        snap(page, "indeed_resume_upload_failed")
        return False


def _fill_step(ap) -> str | None:
    """Answer every empty field on this step from config.yaml. Returns a reason if one cannot be answered truthfully."""
    for f in ap.evaluate(FIELDS_JS):
        label = " ".join((f["label"] or "").split())
        optional = "optional" in label.lower()
        if f["kind"] == "text":
            if not f["empty"]:
                continue                               # Indeed pre-fills name / email / phone / city from your account
            a = answer(label)
            if a is None:
                if optional:
                    continue
                return f"unanswerable question: {label[:80]}"
            if f.get("type") == "number" and re.fullmatch(r"\d+\.\d+", a):
                a = str(int(float(a)))               # whole-number field: round DOWN (2.8 yrs -> 2), never up
            ap.fill(f"[data-ja='{f['idx'][0]}']", a)
        elif f["kind"] == "select":
            if not f["empty"]:
                continue
            opts = [o.strip() for o in f["opts"]]
            i = pick_option(label, opts)
            if i is None:
                if optional:
                    continue
                return f"unanswerable question: {label[:80]} | options: {' / '.join(opts)[:120]}"
            ap.select_option(f"[data-ja='{f['idx'][0]}']", index=i)
        elif f["kind"] == "radio":
            if f["checked"]:
                continue
            opts = [o.strip() for o in f["opts"]]
            i = pick_option(label, opts)
            if i is None:
                if optional:
                    continue
                return f"unanswerable question: {label[:80]} | options: {' / '.join(opts)[:120]}"
            ap.check(f"[data-ja='{f['idx'][i]}']", force=True)
        elif f["kind"] == "checkbox" and not f["checked"] and not optional and len(f["opts"]) > 1:
            return f"multi-choice question needs you: {label[:80]}"     # never tick boxes on your behalf
        human_sleep((0.5, 1.5))
    return None


def _visible(ap, selector):
    """First VISIBLE match (Indeed keeps hidden copies of its buttons in the page)."""
    for h in ap.query_selector_all(selector):
        try:
            if h.is_visible():
                return h
        except Exception:
            pass
    return None


def _stored_copy_is_current(text: str, name: str, resume_pdf) -> bool:
    """Indeed shows 'Archit_Jain_SDE.pdf  Uploaded Jul 4, 2026'. Current = uploaded on/after the local file's last change."""
    import datetime as dt
    from pathlib import Path
    m = re.search(re.escape(name) + r"\s*Uploaded\s+([A-Z][a-z]{2,8} \d{1,2}, \d{4})", text)
    if not m:
        return False
    try:
        up = dt.datetime.strptime(m.group(1).replace("Sept", "Sep"), "%b %d, %Y").date()
    except ValueError:
        return False
    return up >= dt.date.fromtimestamp(Path(resume_pdf).stat().st_mtime)


def _whole_numbers(ap) -> bool:
    """After 'Answer must be a valid number (no decimals)': turn every decimal answer into its whole part (round DOWN)."""
    changed = ap.evaluate("""() => { let n = 0; for (const e of document.querySelectorAll('input, textarea')) {
        if (/^\\d+\\.\\d+$/.test((e.value || '').trim())) { n++; e.setAttribute('data-ja-fix', '1'); } } return n; }""")
    for h in ap.query_selector_all("[data-ja-fix='1']"):
        h.fill(str(int(float(h.input_value()))))
    return changed > 0


def _resume_step(ap, resume_pdf):
    """On the resume step use THE main resume file (same file every time, never edited). If the copy stored on Indeed
    is older than the local file, upload the local file (Indeed then lists and selects the new upload)."""
    if "resume" not in ap.url.lower() or not resume_pdf:
        return
    name = getattr(resume_pdf, "name", str(resume_pdf).split("/")[-1])
    try:
        text = ap.evaluate("document.body.innerText") or ""
        if _stored_copy_is_current(text, name, resume_pdf):
            card = _visible(ap, f"text={name}")
            if card:
                card.click()                            # the current file is already on Indeed: just select it
                human_sleep((1, 2))
            return
        inp = ap.query_selector("input[type='file']")
        if inp:
            inp.set_input_files(str(resume_pdf))
            human_sleep((4, 6))
    except Exception:
        pass


def _next_button(ap, wait: float = 20):
    """-> (submit_button_or_None, button_to_click_or_None). Indeed shows loading screens between steps
    ("Preparing review"), so wait for a Submit / Continue button to appear before calling the step unknown."""
    import time
    end = time.time() + wait
    while True:
        submit = _visible(ap, SUBMIT_BTN)
        nxt = submit or _visible(ap, CONTINUE_BTN)
        if nxt or time.time() > end or _done(ap):
            return submit, nxt
        time.sleep(1)


def _done(ap) -> bool:
    try:
        return "post-apply" in ap.url or bool(SUCCESS_RX.search(ap.evaluate("document.body.innerText")))
    except Exception:
        return False


def precheck(page, job: dict) -> tuple[str, str] | None:
    """Outcome known WITHOUT clicking (already applied / company site), else None = go ahead and click."""
    if page.evaluate("""() => [...document.querySelectorAll('button, a, span')].some(e => e.offsetParent !== null &&
            /^\\s*(applied|you applied.*)\\s*$/i.test(e.innerText || ''))"""):
        return "skipped", "already applied (Indeed shows 'Applied')"
    if _visible(page, EXTERNAL_BTN):
        return "external", "redirects to company website"
    btn = _visible(page, APPLY_BTN)
    if not btn:
        return "external", "no Indeed Apply button (company site / apply by email)"
    return None


def apply(page, job: dict, resume_pdf, dry_run: bool) -> tuple[str, str]:
    """-> (status, reason). Assumes details(page, job) has just loaded the job page."""
    pre = precheck(page, job)
    if pre:
        return pre
    btn = _visible(page, APPLY_BTN)
    if dry_run:
        return "dry_run", "would apply"
    ctx = page.context
    before = len(ctx.pages)
    btn.click()
    human_sleep((3, 5))
    ap = ctx.pages[-1] if len(ctx.pages) > before else page      # Indeed Apply may open in a new tab
    try:
        for _ in range(10):
            check_blocked(ap)
            if _done(ap):
                return "applied", ""
            _resume_step(ap, resume_pdf)
            why = _fill_step(ap)
            if why:
                snap(ap, "indeed_question")
                return "needs_manual", why
            submit, nxt = _next_button(ap)
            if not nxt:
                snap(ap, "indeed_unknown_step")
                return "needs_manual", "unexpected form step - finish it on Indeed (draft is saved)"
            url_before = ap.url
            nxt.click()
            human_sleep((2, 4))
            if submit:
                import time
                end = time.time() + 20                      # confirmation page can take a while to load
                while time.time() < end:
                    if _done(ap):
                        return "applied", ""
                    time.sleep(1)
                snap(ap, "indeed_unconfirmed")
                return "unconfirmed", "clicked Submit, confirmation not recognised - check Indeed > My jobs > Applied"
            if ap.url == url_before:
                err = _visible(ap, ERROR_SEL)
                if err and re.search(r"no decimals|whole number|integer", err.inner_text() or "", re.I) and _whole_numbers(ap):
                    _visible(ap, CONTINUE_BTN).click()      # retry once with whole numbers
                    human_sleep((2, 4))
                    err = _visible(ap, ERROR_SEL) if ap.url == url_before else None
                if err and (err.inner_text() or "").strip():
                    snap(ap, "indeed_form_error")
                    return "needs_manual", f"form error: {(err.inner_text() or '').strip()[:100]}"
        snap(ap, "indeed_too_long")
        return "needs_manual", "application form longer than 10 steps"
    except Blocked:
        raise
    finally:
        if ap is not page:
            try:
                ap.close()
            except Exception:
                pass
