"""Answers for application-form questions, taken ONLY from config.yaml. Unknown question -> None (job goes to 'needs_manual')."""
import re
from .config import load_config


def _num(v):
    return None if v in (None, "", "CHANGE_ME") else v


def _fmt(x):
    return str(int(x)) if float(x) == int(float(x)) else str(x)


def custom_answer(l: str, cfg: dict) -> str | None:
    """Your own answers from config.yaml `answers:` (list of {match: regex, answer: text}). First match wins."""
    for item in cfg.get("answers") or []:
        try:
            if re.search(str(item["match"]), l, re.I):
                return str(item["answer"])
        except (re.error, KeyError, TypeError):
            continue
    return None


def answer(label: str, kind: str = "text", cfg: dict | None = None) -> str | None:
    cfg = cfg or load_config()
    p = cfg["profile"]
    l = re.sub(r"\s+", " ", (label or "").lower()).strip()
    if not l:
        return None
    mine = custom_answer(l, cfg)                    # your explicit answers win over the built-in rules
    if mine is not None:
        return mine
    if re.search(r"sponsor", l):
        return "No"
    if re.search(r"relocat|willing to (move|work)|comfortable (working|commut)", l):
        return "Yes" if p.get("willing_to_relocate") else "No"
    if re.search(r"authori[sz]ed|legally|eligible to work|work permit|right to work", l):
        return "Yes" if p.get("work_authorized_india") else "No"
    if "notice" in l:
        v = _num(p.get("notice_period_days"))
        return _fmt(v) if v is not None else None
    if re.search(r"current (ctc|salary|compensation)|present (ctc|salary)|current annual|last drawn|previous (ctc|salary)", l):
        if re.search(r"month", l):
            return None                             # monthly in-hand pay is not CTC / 12: do not guess
        v = _num(p.get("current_ctc_lpa"))
        return None if v is None else (_fmt(v) if re.search(r"lpa|lakh|lac", l) else _fmt(float(v) * 100000))
    if re.search(r"expected (ctc|salary|compensation)|salary expectation|expected annual", l):
        v = _num(p.get("expected_ctc_lpa"))
        return None if v is None else (_fmt(v) if re.search(r"lpa|lakh|lac", l) else _fmt(float(v) * 100000))
    if re.search(r"years?.*experience|experience.*years?|how many years|yrs", l):
        skills = {k.lower(): v for k, v in p.get("skill_years", {}).items()}
        for k, v in sorted(skills.items(), key=lambda kv: -len(kv[0])):
            if re.search(r"(?<![a-z])" + re.escape(k.replace("react", "react")) + r"(?![a-z])", l):
                return _fmt(v)
        if re.search(r"total|overall|relevant|professional|work experience", l) or not re.search(r"in |with |using ", l):
            return _fmt(p["total_experience_years"])
        return None                                 # a skill we have no number for -> do not guess
    if re.search(r"phone|mobile|contact number", l):
        return str(p["phone"])
    if "email" in l:
        return p["email"]
    if re.search(r"full name|^name$|your name", l):
        return p["full_name"]
    if re.search(r"first name", l):
        return p["full_name"].split()[0]
    if re.search(r"last name|surname", l):
        return p["full_name"].split()[-1]
    # current role / employer (Indeed's "relevant experience" step). Anchored: "why do you want to join our company?" is NOT this.
    if re.search(r"^(your )?(current |most recent )?job title\??$|current (job title|designation|role)\b|most recent job title", l):
        return _num(p.get("current_title"))
    if re.search(r"^(your )?(current |most recent )?(company|employer)( name)?\??$|current (company|employer)\b|most recent (company|employer)", l):
        return _num(p.get("current_company"))
    if re.search(r"current (city|location)|where.*(located|based)|current location|city", l):
        v = _num(p.get("current_location"))
        return v
    return None


def _range_of(text: str):
    t = text.lower().replace(" ", "")
    import re as _re
    m = _re.search(r"lessthan(\d+(?:\.\d+)?)", t)
    if m:
        return 0.0, float(m.group(1))
    m = _re.search(r"(\d+(?:\.\d+)?)[-to]+(\d+(?:\.\d+)?)", t)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = _re.search(r"(\d+(?:\.\d+)?)\+|morethan(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)ormore", t)
    if m:
        return float(next(g for g in m.groups() if g)), float("inf")
    return None


def pick_option(question: str, options: list[str], cfg: dict | None = None) -> int | None:
    """Index of the option that truthfully answers the question, or None (-> needs_manual). Never guesses."""
    cfg = cfg or load_config()
    p = cfg["profile"]
    q = (question or "").lower()
    low = [o.strip().lower() for o in options]
    if "notice" in q:
        d = _num(p.get("notice_period_days"))
        if d is None:
            return None
        d = float(d)
        want = ("15 days or less" if d <= 15 else "1 month" if d <= 30 else "2 months" if d <= 60
                else "3 months" if d <= 90 else "more than 3 months")
        for i, o in enumerate(low):
            if want in o.replace("  ", " "):
                return i
        return None
    a = answer(question, "choice", cfg)
    if a is None:
        return None
    if a.lower() in low:                                    # "Yes" / "No"
        return low.index(a.lower())
    try:
        v = float(a)
    except ValueError:
        return None
    hits = [i for i, o in enumerate(options) if (r := _range_of(o)) and r[0] <= v < r[1]]
    return hits[0] if len(hits) == 1 else None
