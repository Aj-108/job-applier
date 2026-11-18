"""jd text -> validated tailored resume files (docx + pdf)."""
import re
import shutil
from pathlib import Path
from .config import ROOT, RESUMES, load_config
from .resume_writer import load_resume, write_tailored, to_pdf, page_count, baseline_pages
from .tailor import tailor

_STATE = {}


def _orig():
    if "orig" not in _STATE:
        cfg = load_config()
        src = ROOT / cfg["resume_docx"]
        _STATE.update(orig=load_resume(src), src=src, pages=baseline_pages(src), cfg=cfg)
    return _STATE


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:50] or "job"


def build_resume(jd_text: str, name: str, use_claude: bool | None = None) -> dict:
    """Returns {'pdf': Path|None, 'docx': Path, 'matched': [...], 'engine': str, 'cost_inr': float, 'notes': str}."""
    st = _orig()
    t = tailor(st["orig"], jd_text, st["cfg"]["model"], use_claude)
    out = RESUMES / f"Archit_Jain_{slug(name)}.docx"
    write_tailored(st["src"], out, t)
    pdf = to_pdf(out)
    if pdf and page_count(pdf) > st["pages"]:       # overflowed to an extra page -> use original text
        t["notes"] = (t.get("notes", "") + " | tailored resume overflowed a page; used original").strip(" |")
        t.update(engine="original", matched_skills=[])
        shutil.copy(st["src"], out)
        pdf = to_pdf(out)
    return {"pdf": pdf, "docx": out, "matched": t["matched_skills"], "engine": t["engine"],
            "cost_inr": t.get("cost_inr", 0.0), "notes": t.get("notes", ""), "tailored": t}
