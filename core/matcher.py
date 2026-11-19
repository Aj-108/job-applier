"""Fit score 0-100 and skip decisions, before any money is spent on Claude."""
import re
from .jd_parser import extract_skills, is_blocked_stack, parse_experience

# bump when the rules below change: cached skip decisions (db.skip_cache) from older rules are then re-checked once
RULES_VERSION = "3"

# the title must look like a developer role (or name one of our stacks: "ReactJS / NodeJS") ...
DEV_RX = re.compile(r"\b(developer|developers|development|engineer|engineers|programmer|coder|sde|swe|"
                    r"full[\s-]?stack|mern|front[\s-]?end|back[\s-]?end)\b", re.I)
STACK_RX = re.compile(r"\b(react(\.?js)?|node(\.?js)?|next(\.?js)|mern|shopify|javascript|typescript|express(\.?js)?)\b", re.I)
# ... and must not be one of these non-developer / wrong-level roles. Whole words only: "intern" must not hit
# "International", "hr" must not hit "Thrissur", "lead" must not hit "Leading".
SKIP_TITLE_RX = re.compile(r"\b(trainer|tutor|teacher|faculty|sales|marketing|recruiter|hr|content|designer|analyst|tester|"
                           r"qa|devops|support|intern|internship|freelance|freelancer|part[\s-]?time|lead|principal|"
                           r"architect|manager|director|head of|staff engineer|consultant|fresher|freshers|trainee|graduate trainee)\b", re.I)


def _title_problem(title: str) -> str:
    if not (DEV_RX.search(title) or STACK_RX.search(title)):
        return "title is not a developer role"
    m = SKIP_TITLE_RX.search(title)
    if m:
        return f"title not suitable ('{m.group(1).lower()}')"
    return ""


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
    why = _title_problem(title)
    if why:
        return why
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
    why = _title_problem(title)
    if why:
        return {"ok": False, "score": 0, "reason": why, "skills": {}}
    blocked, why = is_blocked_stack(title, desc)
    if blocked:
        return {"ok": False, "score": 0, "reason": f"blocked stack: {why}", "skills": {}}
    sk = extract_skills(f"{title}\n{desc}")
    n_core, n_adj = len(sk["core"]), len(sk["adjacent"])
    total = max(n_core + n_adj, 1)
    score = 100 * (n_core + 0.5 * n_adj) / total
    # too little signal -> do not trust a high ratio. One skill is noise; two of ours (e.g. React + Node) is a real hint.
    if n_core + n_adj < 2:
        score = min(score, 40)
    elif n_core + n_adj < 3:
        score = min(score, 55)
    t = title.lower()
    if any(k in t for k in ("full stack", "fullstack", "full-stack", "mern", "react", "node", "next", "shopify", "software",
                            "frontend", "front end", "front-end", "javascript", "typescript")):
        score += 10
    # a title that names one of OUR stacks ("Node.js Developer") with a matching skill in the JD and no foreign stack is a
    # real match even when the posting lists only 1-2 skills (the "few skills" cap above would otherwise sink it)
    if STACK_RX.search(title) and n_core >= 1 and not sk["blocked"]:
        score = max(score, 60)
    # each HARD foreign stack asked for costs 10 (max 30). Soft ones (AWS, GCP, Kubernetes...) cost nothing.
    score -= min(30, 10 * len(sk["blocked"]))
    score = max(0, min(100, score))
    if score < cfg["min_fit_score"]:
        return {"ok": False, "score": score, "reason": f"fit score {score:.0f} < {cfg['min_fit_score']}", "skills": sk}
    return {"ok": True, "score": score, "reason": "", "skills": sk}
