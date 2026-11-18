"""Tailor the resume to a job. Only reword/reorder/emphasize skills Archit already has (core + adjacent).

Two engines:
  * Claude (when ANTHROPIC_API_KEY is set): rewrites headline, summary, skill order, <=2 bullets.
  * Rules (no key / Claude failed / validator rejected): reorders skills by JD relevance and appends a
    truthful 'hands-on with ...' line. Never invents anything.
Every output passes validate() - anything suspicious falls back to the original resume.
"""
import copy
import os
import re
import time
from pydantic import BaseModel
from .config import load_skills, flat, ai_settings
from .jd_parser import extract_skills, norm

PRICE_IN, PRICE_OUT, INR = 4.0, 20.0, 88.0     # claude-opus-5-5 $/MTok, USD->INR


class SkillRow(BaseModel):
    category: str
    skills: list[str]


class BulletRewrite(BaseModel):
    paragraph_id: int
    text: str


class TailoredOut(BaseModel):
    headline: str
    summary: str
    skills: list[SkillRow]
    bullet_rewrites: list[BulletRewrite]
    matched_skills: list[str]


SYSTEM = """You tailor a software developer's resume to a job description. You may ONLY reword, reorder and
emphasize what is already true about the candidate.

HARD RULES
- Never add a technology, tool, framework, language, certification, employer, project, number, or date that is not
  in the ORIGINAL resume or in the ALLOWED ADJACENT SKILLS list.
- Never mention any BLOCKED technology (the candidate has no experience with them), even if the job asks for it.
- Keep the same facts: 2.5+ years experience, 10+ projects, 75Ways Technologies, Jan 2024 - Present, B.Tech 2020-2024.
- headline: same format 'Software Developer | A | B | C | D' - choose the 4-5 core skills most relevant to the job.
- summary: same length (+-15%), same facts; lead with what the job cares about most.
- skills: return ALL 6 categories with the SAME category names. Every original skill must stay. Put the
  skills the job asks for first. You may add skills from the allowed-adjacent list (max 6 additions in total).
- bullet_rewrites: at most 2 bullets, same facts and length (+-15%), reworded to echo the job's keywords
  naturally. Use the paragraph_id keys from the ORIGINAL bullets. No leading bullet symbol.
- matched_skills: the skills from the job description that the final resume genuinely covers."""


# ---------------------------------------------------------------- validation
BLOCK_CACHE = {}


def _allowed(sk):
    return {norm(s) for s in flat(sk["core"]) + flat(sk["adjacent"])}


def _blocked_hits(text: str, sk) -> list[str]:
    hits = []
    for b in sk["blocked"]:
        if re.search(r"(?<![\w.+#])" + re.escape(b) + r"(?![\w+#])", text, re.I):
            hits.append(b)
    return hits


def _nums(text: str) -> set[str]:
    # standalone numbers only: ignore digits glued to words (HTML5, CSS3, 75Ways, archit108)
    return set(re.findall(r"(?<![A-Za-z\d.])\d+(?:\.\d+)?(?![A-Za-z\d])", text))


def _len_ok(new: str, old: str, tol: float) -> bool:
    return abs(len(new) - len(old)) <= tol * len(old)


def validate(t: dict, orig: dict, sk=None) -> list[str]:
    """Return a list of problems; empty list = safe to use."""
    sk = sk or load_skills()
    problems = []
    allowed = _allowed(sk)
    orig_nums = _nums(orig["full_text"])
    all_text = " ".join([t["headline"], t["summary"], *t["bullets"].values(),
                         *[s for v in t["skills"].values() for s in v]])
    for b in _blocked_hits(all_text, sk):
        problems.append(f"blocked technology present: {b}")
    extra = _nums(all_text) - orig_nums
    if extra:
        problems.append(f"new numbers not in original: {sorted(extra)}")
    if set(t["skills"]) != set(orig["skills"]):
        problems.append("skill categories changed")
    else:
        added = 0
        for cat, items in t["skills"].items():
            have = {norm(s) for s in orig["skills"][cat]}
            new = {norm(s) for s in items}
            if not have <= new:
                problems.append(f"original skill removed in {cat}: {sorted(have - new)}")
            for s in items:
                if norm(s) not in have:
                    added += 1
                    if norm(s) not in allowed:
                        problems.append(f"skill not allowed: {s}")
        if added > 6:
            problems.append(f"too many added skills ({added})")
    if not _len_ok(t["summary"], orig["summary"], 0.15):
        problems.append("summary length out of range")
    if not _len_ok(t["headline"], orig["headline"], 0.25):
        problems.append("headline length out of range")
    if len(t["bullets"]) > 2:
        problems.append("more than 2 bullet rewrites")
    for pid, text in t["bullets"].items():
        if int(pid) not in orig["bullets"]:
            problems.append(f"unknown bullet id {pid}")
        elif not _len_ok(text, orig["bullets"][int(pid)], 0.15):
            problems.append(f"bullet {pid} length out of range")
    return problems


# ---------------------------------------------------------------- engines
def tailor_rules(orig: dict, jd_skills: dict, sk) -> dict:
    wanted = {norm(s) for s in jd_skills["core"] + jd_skills["adjacent"]}
    adj_by_cat = {c: [s for s in sk["adjacent"].get(c.lower().split(" /")[0].lower(), [])] for c in orig["skills"]}
    cat_key = {"Frontend": "frontend", "Backend": "backend", "Databases": "databases",
               "Shopify / Ecommerce": "shopify", "Integrations": "integrations", "Tools": "tools"}
    skills, added = {}, 0
    for cat, items in orig["skills"].items():
        first = [s for s in items if norm(s) in wanted]
        rest = [s for s in items if norm(s) not in wanted]
        extra = []
        for s in sk["adjacent"].get(cat_key.get(cat, ""), []):
            if norm(s) in wanted and norm(s) not in {norm(x) for x in items} and added < 4:
                extra.append(s)
                added += 1
        skills[cat] = first + extra + rest
    core_hits = [s for s in jd_skills["core"]]
    # headline: lead with the job's own core skills, then fill from defaults
    defaults = ["MERN Stack", "React.js", "Node.js", "Next.js", "Shopify"]
    head_skills = []
    for s in core_hits[:4]:
        if s not in head_skills and s not in ("HTML5", "CSS3", "Git", "GitHub"):
            head_skills.append(s)
    for d in defaults:
        if len(head_skills) >= 4:
            break
        if d not in head_skills:
            head_skills.append(d)
    headline = "Software Developer | " + " | ".join(head_skills[:5])
    summary = orig["summary"]
    focus = [s for s in core_hits if s not in ("HTML5", "CSS3", "Git", "GitHub")][:4]
    if len(focus) >= 2:
        sentence = " Hands-on with " + ", ".join(focus[:-1]) + " and " + focus[-1] + "."
        if _len_ok(summary + sentence, summary, 0.12):
            summary += sentence
    return {"headline": headline, "summary": summary, "skills": skills, "bullets": {},
            "matched_skills": core_hits + jd_skills["adjacent"], "engine": "rules", "cost_inr": 0.0}


def _payload(orig, jd_text, sk):
    return ("ORIGINAL RESUME\nheadline: %s\nsummary: %s\nskills: %s\nbullets (paragraph_id: text): %s\n\n"
            "ALLOWED ADJACENT SKILLS: %s\nBLOCKED (never mention): %s\n\nJOB DESCRIPTION:\n%s" % (
                orig["headline"], orig["summary"], orig["skills"], dict(orig["bullets"]),
                flat(sk["adjacent"]), sk["blocked"], jd_text[:6000]))


def _result(o: TailoredOut, engine: str, cost: float) -> dict:
    return {"headline": o.headline.strip(), "summary": o.summary.strip(),
            "skills": {r.category: r.skills for r in o.skills},
            "bullets": {b.paragraph_id: b.text.strip() for b in o.bullet_rewrites},
            "matched_skills": o.matched_skills, "engine": engine, "cost_inr": cost}


def tailor_claude(orig: dict, jd_text: str, jd_skills: dict, sk, model: str) -> dict:
    import anthropic
    client = anthropic.Anthropic()
    resp = client.messages.parse(
        model=model, max_tokens=4000, system=SYSTEM, messages=[{"role": "user", "content": _payload(orig, jd_text, sk)}],
        output_format=TailoredOut, output_config={"effort": ai_settings()["effort"]})
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        raise RuntimeError("claude refused / no parsed output")
    cost = (resp.usage.input_tokens * PRICE_IN + resp.usage.output_tokens * PRICE_OUT) / 1e6 * INR
    return _result(resp.parsed_output, "claude", cost)


# rough Gemini Flash list price, USD per million tokens - an ESTIMATE for the report (free tier costs nothing)
G_IN, G_OUT = 0.30, 2.50


def tailor_gemini(orig: dict, jd_text: str, jd_skills: dict, sk, model: str) -> dict:
    import os
    from google import genai
    from google.genai import types
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    client = genai.Client(api_key=key)
    think = os.environ.get("GEMINI_THINKING", "").strip().lower()
    cfg = types.GenerateContentConfig(
        system_instruction=SYSTEM, response_mime_type="application/json", response_schema=TailoredOut,
        max_output_tokens=4000, temperature=0.3,
        **({"thinking_config": types.ThinkingConfig(thinking_level=think)} if think in ("low", "medium", "high") else {}))
    resp = None
    for attempt in range(4):                       # 503 "high demand" / 429 rate limit are usually temporary
        try:
            resp = client.models.generate_content(model=model, contents=_payload(orig, jd_text, sk), config=cfg)
            break
        except Exception as e:
            temp = any(c in str(e) for c in ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "500"))
            if not temp or attempt == 3:
                raise
            time.sleep(4 * (attempt + 1))
    o = resp.parsed if isinstance(resp.parsed, TailoredOut) else TailoredOut.model_validate_json(resp.text)
    u = resp.usage_metadata
    cost = (((u.prompt_token_count or 0) * G_IN) + (((u.candidates_token_count or 0) + (u.thoughts_token_count or 0)) * G_OUT)) / 1e6 * INR if u else 0.0
    return _result(o, "gemini", cost)


def tailor(orig: dict, jd_text: str, model: str | None = None, use_claude: bool | None = None) -> dict:
    """Return a validated tailoring dict. Falls back AI -> rules -> original. Provider/model come from .env."""
    sk = load_skills()
    jd_skills = extract_skills(jd_text)
    st = ai_settings()
    use_ai = (st["provider"] != "none" and st["has_key"]) if use_claude is None else (use_claude and st["provider"] != "none")
    log = []
    if st["provider"] != "none" and not st["has_key"] and use_claude is None:
        log.append(f"{st['provider']} selected but its API key is empty in .env - used rules-based tailoring")
    if use_ai:
        try:
            fn = tailor_gemini if st["provider"] == "gemini" else tailor_claude
            t = fn(orig, jd_text, jd_skills, sk, st["model"])
            probs = validate(t, orig, sk)
            if probs and t["bullets"] and all("bullet" in p for p in probs):
                t["bullets"] = {}                              # keep headline/summary/skills, drop only the bad bullet rewrites
                log.append("dropped bullet rewrites that broke the rules: " + "; ".join(probs))
                probs = validate(t, orig, sk)
            if not probs:
                t["notes"] = " | ".join(log)
                return t
            log.append(f"{st['provider']} output rejected: " + "; ".join(probs))
        except Exception as e:           # network, auth, quota, refusal ...
            log.append(f"{st['provider']} failed: {e}")
    t = tailor_rules(orig, jd_skills, sk)
    probs = validate(t, orig, sk)
    if probs:
        log.append("rules rejected: " + "; ".join(probs))
        t = {"headline": orig["headline"], "summary": orig["summary"], "skills": copy.deepcopy(orig["skills"]),
             "bullets": {}, "matched_skills": [], "engine": "original", "cost_inr": 0.0}
    t["notes"] = " | ".join(log)
    return t
