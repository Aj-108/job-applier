"""Fit score 0-100 and skip decisions, before any money is spent on Claude."""
from .jd_parser import extract_skills, is_blocked_stack, parse_experience


# the title must look like a developer role, and must not be one of these non-developer / wrong-level roles
DEV_WORDS = ("developer", "engineer", "programmer", "full stack", "fullstack", "full-stack", "mern", "sde", "web developer")
SKIP_TITLE = ("trainer", "tutor", "teacher", "faculty", "sales", "marketing", "recruiter", "hr ", "content", "designer",
              "analyst", "tester", " qa", "qa ", "devops", "support", "intern", "freelanc", "part time", "part-time",
              "lead", "principal", "architect", "manager", "director", "head of", "staff engineer", "consultant")


def prescreen(job: dict, cfg: dict) -> str:
    """Cheap check from the search-result card alone (title + experience), BEFORE opening the job page.
    Returns a skip reason, or '' if the job is worth opening."""
    from .jd_parser import _vocab
    lo = job.get("exp_min")
    if lo is None:
        lo, _ = parse_experience(job.get("exp_text", ""))
    if lo is not None and lo > cfg["max_experience_years"]:
        return f"experience too high ({lo}+ yrs)"
    title = job.get("title", "")
    tl = f" {title.lower()} "
    if not any(w in tl for w in DEV_WORDS):
        return "title is not a developer role"
    bad = next((w for w in SKIP_TITLE if w in tl), None)
    if bad:
        return f"title not suitable ('{bad.strip()}')"
    for b, pat in _vocab()["bpats"].items():
        if pat.search(title):
            return f"blocked stack: title mentions blocked stack: {b}"
    return ""


def evaluate(job: dict, cfg: dict) -> dict:
    """Returns {'ok': bool, 'score': float, 'reason': str, 'skills': {...}}."""
    title, desc = job.get("title", ""), job.get("description", "")
    lo, hi = job.get("exp_min"), job.get("exp_max")
    if lo is None:
        lo, hi = parse_experience(job.get("exp_text", "") + " " + desc[:400])
        job["exp_min"], job["exp_max"] = lo, hi
    if lo is not None and lo > cfg["max_experience_years"]:
        return {"ok": False, "score": 0, "reason": f"experience too high ({lo}+ yrs)", "skills": {}}
    tl = f" {title.lower()} "
    if not any(w in tl for w in DEV_WORDS):
        return {"ok": False, "score": 0, "reason": "title is not a developer role", "skills": {}}
    bad = next((w for w in SKIP_TITLE if w in tl), None)
    if bad:
        return {"ok": False, "score": 0, "reason": f"title not suitable ('{bad.strip()}')", "skills": {}}
    blocked, why = is_blocked_stack(title, desc)
    if blocked:
        return {"ok": False, "score": 0, "reason": f"blocked stack: {why}", "skills": {}}
    sk = extract_skills(f"{title}\n{desc}")
    n_core, n_adj = len(sk["core"]), len(sk["adjacent"])
    total = max(n_core + n_adj, 1)
    score = 100 * (n_core + 0.5 * n_adj) / total
    if n_core + n_adj < 3:          # too little signal -> do not trust a high ratio
        score = min(score, 40)
    t = title.lower()
    if any(k in t for k in ("full stack", "fullstack", "mern", "react", "node", "next", "shopify", "software")):
        score += 10
    # a title that names one of OUR stacks ("Node.js Developer") with a matching skill in the JD and no foreign stack is a
    # real match even when the posting lists only 1-2 skills (the "few skills" cap above would otherwise sink it)
    stack_title = any(k in t for k in ("node", "react", "mern", "next.js", "nextjs", "shopify", "express", "typescript", "javascript"))
    if stack_title and n_core >= 1 and not sk["blocked"]:
        score = max(score, 60)
    score -= min(30, 10 * sum(1 for _ in sk["blocked"]))   # each foreign skill costs 10 (max 30)
    score = max(0, min(100, score))
    if score < cfg["min_fit_score"]:
        return {"ok": False, "score": score, "reason": f"fit score {score:.0f} < {cfg['min_fit_score']}", "skills": sk}
    return {"ok": True, "score": score, "reason": "", "skills": sk}
