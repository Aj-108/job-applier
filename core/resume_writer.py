"""Read the original .docx into editable sections and write a tailored copy (+ PDF) back."""
import re
import shutil
import subprocess
from pathlib import Path
from docx import Document
from pypdf import PdfReader
from .config import ROOT, RESUMES

SOFFICE = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
HEADLINE_P, SUMMARY_P = 1, 5
SKILL_PS = range(7, 13)
BULLET_PS = [15, 16, 17, 18, 19, 20, 23, 24, 25, 26, 28, 29, 30, 31, 33, 34, 35]


def _strip_bullet(t: str) -> str:
    return re.sub(r"^\s*•\s*", "", t).strip()


def load_resume(path: Path) -> dict:
    d = Document(path)
    ps = d.paragraphs
    skills = {}
    for i in SKILL_PS:
        label, _, rest = ps[i].text.partition(":")
        skills[label.strip()] = [s.strip() for s in rest.split(",") if s.strip()]
    return {
        "headline": ps[HEADLINE_P].text.strip(),
        "summary": ps[SUMMARY_P].text.strip(),
        "skills": skills,
        "bullets": {i: _strip_bullet(ps[i].text) for i in BULLET_PS},
        "full_text": "\n".join(p.text for p in ps),
    }


def _set_first_run(p, text: str, keep_runs: int = 1):
    """Put text in run[keep_runs-1]... others blanked, so run formatting of the first run is kept."""
    p.runs[0].text = text
    for r in p.runs[1:]:
        r.text = ""


def write_tailored(src: Path, out_docx: Path, t: dict) -> Path:
    """t = {'headline', 'summary', 'skills': {label: [..]}, 'bullets': {pid: text}}"""
    d = Document(src)
    ps = d.paragraphs
    if t.get("headline"):
        _set_first_run(ps[HEADLINE_P], t["headline"])
    if t.get("summary"):
        _set_first_run(ps[SUMMARY_P], t["summary"])
    for i in SKILL_PS:
        label = ps[i].text.partition(":")[0].strip()
        if label in t.get("skills", {}):
            runs = ps[i].runs      # run0 = bold "Label: ", run1 = skills text
            runs[1].text = ", ".join(t["skills"][label])
            for r in runs[2:]:
                r.text = ""
    for pid, text in t.get("bullets", {}).items():
        p = ps[int(pid)]
        _set_first_run(p, "• " + text)
    out_docx.parent.mkdir(parents=True, exist_ok=True)
    d.save(out_docx)
    return out_docx


def _lo_pdf(docx: Path) -> Path | None:
    if not Path(SOFFICE).exists():
        return None
    # own profile dir: a LibreOffice GUI that is already open locks the default profile and hangs headless runs
    profile = ROOT / ".lo_profile"
    try:
        subprocess.run([SOFFICE, f"-env:UserInstallation=file://{profile}", "--headless", "--norestore",
                        "--convert-to", "pdf", "--outdir", str(docx.parent), str(docx)],
                       capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        return None
    pdf = docx.with_suffix(".pdf")
    return pdf if pdf.exists() else None


PRINT_CSS = "<style>@page{size:A4;margin:12mm 14mm}body{margin:0!important}p{margin-top:0;margin-bottom:0}</style>"


_PDF_SCRIPT = (
    "import sys\n"
    "from playwright.sync_api import sync_playwright\n"
    "html, pdf = sys.argv[1], sys.argv[2]\n"
    "with sync_playwright() as pw:\n"
    "    b = pw.chromium.launch(); pg = b.new_page()\n"
    "    pg.set_content(open(html, encoding='utf-8', errors='replace').read())\n"
    "    pg.pdf(path=pdf, format='A4', print_background=True); b.close()\n")


def _chromium_pdf(docx: Path) -> Path | None:
    """docx -> html (macOS textutil) -> PDF (Chromium, in a SUBPROCESS so it works even while another Playwright
    browser is open in this process). Used when LibreOffice is unavailable/broken."""
    import sys
    html = docx.with_suffix(".html")
    r = subprocess.run(["textutil", "-convert", "html", "-output", str(html), str(docx)], capture_output=True)
    if r.returncode != 0 or not html.exists():
        return None
    html.write_text(html.read_text(errors="replace").replace("</head>", PRINT_CSS + "</head>"))
    pdf = docx.with_suffix(".pdf")
    try:
        subprocess.run([sys.executable, "-I", "-c", _PDF_SCRIPT, str(html), str(pdf)], capture_output=True, timeout=90)
    finally:
        html.unlink(missing_ok=True)
    return pdf if pdf.exists() else None


def to_pdf(docx: Path) -> Path | None:
    return _lo_pdf(docx) or _chromium_pdf(docx)


def page_count(pdf: Path) -> int:
    return len(PdfReader(str(pdf)).pages)


def baseline_pages(src: Path) -> int:
    tmp = RESUMES / "_baseline.docx"
    RESUMES.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, tmp)
    pdf = to_pdf(tmp)
    return page_count(pdf) if pdf else 1
