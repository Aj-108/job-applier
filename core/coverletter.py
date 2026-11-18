"""Cover letters / application emails are written by the AI agent doing the application (Claude, Haiku, Antigravity...)
from the candidate facts + the job description. This module is the safety net: nothing is sent unless `check()` passes."""
import re
from .config import load_config, load_skills, never_claim
from .tailor import _blocked_hits

NUM_RX = re.compile(r"(?<![\w.])\d+(?:\.\d+)?\+?(?![\w])")
MIN_WORDS, MAX_WORDS = 60, 260


def _allowed_numbers(cfg: dict) -> set[str]:
    p = cfg["profile"]
    vals = [p.get("total_experience_years")] + list((p.get("skill_years") or {}).values())
    out = set()
    for v in vals:
        if v in (None, ""):
            continue
        s = str(int(v)) if float(v) == int(float(v)) else str(v)
        out |= {s, s + "+"}
    return out | {"10", "10+", "2020", "2024", "2026"}                     # 10+ projects, B.Tech years, current year


def check(text: str, cfg: dict | None = None) -> list[str]:
    """Problems that must be fixed before this text goes out. [] = fine."""
    cfg = cfg or load_config()
    sk = load_skills()
    problems = []
    words = len(text.split())
    if words < MIN_WORDS:
        problems.append(f"too short ({words} words, at least {MIN_WORDS})")
    if words > MAX_WORDS:
        problems.append(f"too long ({words} words, at most {MAX_WORDS})")
    for b in _blocked_hits(text, {**sk, "blocked": never_claim(sk)}):
        problems.append(f"mentions a skill that is not on the resume: {b}")
    ok = _allowed_numbers(cfg)
    for n in sorted(set(NUM_RX.findall(text))):
        if n not in ok:
            problems.append(f"number not backed by the resume: {n}")
    if re.search(r"\b(placeholder|lorem|\[[^\]]*\]|<[^>]*>|your name|company name)\b", text, re.I):
        problems.append("contains a template placeholder")
    return problems


def guidelines(cfg: dict | None = None) -> str:
    """The rules the agent follows when it writes the letter (also printed into the hand-off brief)."""
    return (f"Write it yourself from the candidate facts + the job description. {MIN_WORDS}-{MAX_WORDS} words, plain and specific: "
            "address the company by name, name the role, connect 2-3 matching skills and one real project from the resume "
            "(Nowfit, Shadowbreeze, StreetSmart), close politely, sign 'Archit Jain'. Use ONLY facts from the resume: no invented "
            "employers, numbers, certificates, years or skills; nothing from the blocked list. Run "
            "`python main.py coverletter --file <letter.txt>` - it must print OK before the letter is used.")
