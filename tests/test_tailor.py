import sys, copy
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.config import ROOT, load_skills
from core.resume_writer import load_resume
from core.tailor import validate, tailor, tailor_rules
from core.jd_parser import extract_skills, is_blocked_stack, parse_experience
from core.matcher import evaluate

_docx = Path(__file__).parent / "data" / "test_resume.docx"
orig = load_resume(_docx)
sk = load_skills()
CFG = {"min_fit_score": 55, "max_experience_years": 4}


def base():
    return {"headline": orig["headline"], "summary": orig["summary"], "skills": copy.deepcopy(orig["skills"]), "bullets": {}}


def test_original_is_valid():
    assert validate(base(), orig, sk) == []


def test_blocked_rejected():
    t = base(); t["summary"] = orig["summary"].replace("Node.js", "Django")
    assert any("Django" in p for p in validate(t, orig, sk))


def test_blocked_in_skills_rejected():
    t = base(); t["skills"]["Backend"].append("Django")
    assert validate(t, orig, sk)


def test_new_number_rejected():
    t = base(); t["summary"] = orig["summary"].replace("2.5+", "5+")
    assert any("numbers" in p for p in validate(t, orig, sk))


def test_removed_skill_rejected():
    t = base(); t["skills"]["Databases"] = ["MongoDB"]
    assert any("removed" in p for p in validate(t, orig, sk))


def test_unknown_skill_rejected():
    t = base(); t["skills"]["Tools"].append("Jenkins")
    assert any("not allowed" in p for p in validate(t, orig, sk))


def test_django_react_job_never_gets_django():
    jd = "Django + React developer. Python, Django REST, PostgreSQL, React.js. 3 years."
    t = tailor(orig, jd, use_claude=False)
    blob = str(t)
    assert "Django" not in blob and "Python" not in blob
    assert validate(t, orig, sk) == []


def test_react_job_reorders_and_matches():
    jd = "React.js developer with Redux Toolkit, TypeScript, Tailwind CSS, REST APIs, Jest, Unit Testing, Git."
    t = tailor(orig, jd, use_claude=False)
    assert t["skills"]["Frontend"][0] in ("React.js", "TypeScript", "Redux Toolkit", "Tailwind CSS")
    assert "React.js" in t["headline"]
    assert validate(t, orig, sk) == []
    assert set(orig["skills"]["Frontend"]) <= set(t["skills"]["Frontend"])


def test_blocked_stack_job_skipped():
    blocked, _ = is_blocked_stack("Django Developer", "python django flask")
    assert blocked
    r = evaluate({"title": "Python Developer", "description": "Python Django Flask FastAPI AWS Kubernetes", "exp_text": "2-4 Yrs"}, CFG)
    assert not r["ok"]


def test_nodejs_with_python_nice_to_have_ok():
    r = evaluate({"title": "Full Stack Developer", "description": "React.js Node.js Express MongoDB TypeScript REST APIs. Python is a plus.", "exp_text": "2-4 Yrs"}, CFG)
    assert r["ok"], r


def test_too_senior_skipped():
    r = evaluate({"title": "Full Stack Developer", "description": "React Node MongoDB TypeScript", "exp_text": "5-8 Yrs"}, CFG)
    assert not r["ok"] and "experience" in r["reason"]


def test_parse_experience():
    assert parse_experience("2-5 Yrs") == (2.0, 5.0)
    assert parse_experience("3+ years") == (3.0, None)


def test_answers():
    from core.answers import answer
    cfg = {"profile": {"full_name": "Archit Jain", "email": "a@b.c", "phone": "123", "total_experience_years": 2.5,
                       "current_ctc_lpa": 6, "expected_ctc_lpa": 10, "notice_period_days": 30, "current_location": "Delhi",
                       "willing_to_relocate": True, "work_authorized_india": True, "skill_years": {"react": 2.5, "next.js": 2}}}
    assert answer("How many years of experience do you have with React?", cfg=cfg) == "2.5"
    assert answer("How many years of experience do you have with Django?", cfg=cfg) is None   # never guess a skill we lack
    assert answer("Total years of experience", cfg=cfg) == "2.5"
    assert answer("Notice period (days)", cfg=cfg) == "30"
    assert answer("Expected CTC in LPA", cfg=cfg) == "10"
    assert answer("Current salary", cfg=cfg) == "600000"
    assert answer("Do you require visa sponsorship?", cfg=cfg) == "No"
    assert answer("Some weird question", cfg=cfg) is None
    cfg["profile"]["notice_period_days"] = "CHANGE_ME"
    assert answer("Notice period", cfg=cfg) is None


def test_gemini_path_mocked(monkeypatch):
    """Gemini returns a valid answer -> used; returns a Django answer -> rejected and rules fallback used."""
    import types
    from google import genai
    from core import tailor as T
    monkeypatch.setenv("AI_PROVIDER", "gemini"); monkeypatch.setenv("GEMINI_API_KEY", "x")
    good = T.TailoredOut(headline=orig["headline"], summary=orig["summary"],
                         skills=[T.SkillRow(category=c, skills=v) for c, v in orig["skills"].items()],
                         bullet_rewrites=[], matched_skills=["React.js"])

    def fake_client(answer):
        class M:
            def generate_content(self, **kw):
                return types.SimpleNamespace(parsed=answer, text=answer.model_dump_json(),
                                             usage_metadata=types.SimpleNamespace(prompt_token_count=3000, candidates_token_count=800, thoughts_token_count=0))
        return types.SimpleNamespace(models=M())

    monkeypatch.setattr(genai, "Client", lambda api_key=None: fake_client(good))
    t = T.tailor(orig, "React Node developer")
    assert t["engine"] == "gemini" and t["cost_inr"] > 0
    bad = good.model_copy(update={"summary": orig["summary"].replace("Node.js", "Django")})
    monkeypatch.setattr(genai, "Client", lambda api_key=None: fake_client(bad))
    t = T.tailor(orig, "React Node developer")
    assert t["engine"] == "rules" and "rejected" in t["notes"] and "Django" not in str(t["summary"])


def test_bad_bullet_dropped_but_rest_kept(monkeypatch):
    import types
    from google import genai
    from core import tailor as T
    monkeypatch.setenv("AI_PROVIDER", "gemini"); monkeypatch.setenv("GEMINI_API_KEY", "x")
    out = T.TailoredOut(headline="Software Developer | React.js | Node.js | Next.js | TypeScript", summary=orig["summary"],
                        skills=[T.SkillRow(category=c, skills=v) for c, v in orig["skills"].items()],
                        bullet_rewrites=[T.BulletRewrite(paragraph_id=17, text="Short.")], matched_skills=["React.js"])
    class M:
        def generate_content(self, **kw):
            return types.SimpleNamespace(parsed=out, text="", usage_metadata=None)
    monkeypatch.setattr(genai, "Client", lambda api_key=None: types.SimpleNamespace(models=M()))
    t = T.tailor(orig, "React Node developer")
    assert t["engine"] == "gemini" and t["bullets"] == {} and "TypeScript" in t["headline"] and "dropped bullet" in t["notes"]


def test_title_gate_and_dotnet():
    ok_desc = "React.js Node.js Express MongoDB TypeScript REST APIs"
    for t in ("AspDotnet Developers", "AI Trainer - Software Engineering", "Engineering Manager", "Sales Executive"):
        assert not evaluate({"title": t, "description": ok_desc, "exp_text": "2-4 Yrs"}, CFG)["ok"], t
    for t in ("Full Stack Developer", "Junior Full Stack Developer", "MERN Stack Developer", "NodeJS Developer"):
        assert evaluate({"title": t, "description": ok_desc, "exp_text": "2-4 Yrs"}, CFG)["ok"], t


def test_short_node_posting_is_a_fit():
    r = evaluate({"title": "Node.js Developer", "description": "Working knowledge of Node.js development. Minimum 2 years in Node.js, JavaScript and Databases.", "exp_text": "2-3 Yrs"}, CFG)
    assert r["ok"], r
    # but a short posting that also asks for a foreign stack is still skipped
    r = evaluate({"title": "Node.js Developer", "description": "Node.js, JavaScript, plus Java and Python.", "exp_text": "2-3 Yrs"}, CFG)
    assert not r["ok"] or r["score"] < 70
    # a vague title with no stack at all stays skipped
    r = evaluate({"title": "Software Engineer", "description": "Design, develop, test and maintain software applications.", "exp_text": "1-3 Yrs"}, CFG)
    assert not r["ok"]


def test_pick_option_ranges_and_notice():
    from core.answers import pick_option
    cfg = {"profile": {"full_name": "A B", "email": "a@b.c", "phone": "1", "total_experience_years": 2.5, "current_ctc_lpa": 6,
                       "expected_ctc_lpa": 10, "notice_period_days": 30, "current_location": "Delhi", "willing_to_relocate": True,
                       "work_authorized_india": True, "skill_years": {"next.js": 2, "react": 2.5, "typescript": 2}}}
    rng = ["Less than 2 years", "2-4 years", "4-6 years", "6-10years", "10 + years"]
    q = "How many years of professional experience with Next.js (App Router), React and TypeScript?"
    assert pick_option(q, rng, cfg) == 1
    assert pick_option("Total years of experience?", rng, cfg) == 1
    assert pick_option("Years of experience with Django?", rng, cfg) is None          # never guess
    assert pick_option("What is your notice period?", ["15 Days or less", "1 Month", "2 Months", "3 Months"], cfg) == 1
    assert pick_option("Are you willing to relocate?", ["Yes", "No"], cfg) == 0


def test_instahyre_title_split_and_confirmation():
    from platforms.instahyre import split_title, CONFIRM_RX
    assert split_title("Swiggy - React.js Developer", "Swiggy - React.js Developer") == ("React.js Developer", "Swiggy")
    assert split_title("Matrice AI - Senior Fullstack Engineer (React / Go)", "") == ("Senior Fullstack Engineer (React / Go)", "Matrice AI")
    assert split_title("Frontend Engineer", "Everest Fleet") == ("Frontend Engineer", "Everest Fleet")      # already separate: untouched
    assert not CONFIRM_RX.search("Interested candidates, thank you! Show interest in more jobs")          # words that used to count as success
    assert CONFIRM_RX.search("You have successfully applied to this job")
