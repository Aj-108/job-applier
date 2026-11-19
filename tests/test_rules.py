"""Phase 2 skip rules + platform helpers (no browser)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pytest
from core.matcher import evaluate, prescreen
from core.jd_parser import extract_skills, is_blocked_stack
from core.answers import answer

CFG = {"min_fit_score": 55, "max_experience_years": 4, "profile": {"total_experience_years": 2.8}}
MERN = "React.js Node.js Express MongoDB TypeScript REST APIs"


def ok(title, desc=MERN, exp="2-4 Yrs"):
    return evaluate({"title": title, "description": desc, "exp_text": exp}, CFG)


def test_cloud_tools_are_soft():
    assert ok("Full Stack Developer (React, Node, AWS)")["ok"]
    assert not prescreen({"title": "Full Stack Developer (React/Node/AWS)", "exp_text": "2-4 Yrs"}, CFG)
    r = ok("MERN Developer", "React, Node.js and AWS, Kubernetes, Terraform, GCP.")
    assert r["ok"], r
    assert set(r["skills"]["soft"]) >= {"AWS", "Kubernetes"}
    assert "AWS" not in r["skills"]["blocked"]


def test_hard_blocked_still_skipped():
    assert not ok("Java Full Stack Developer")["ok"]
    assert prescreen({"title": "Angular Developer", "exp_text": "2-4 Yrs"}, CFG)
    assert not ok("Full Stack Developer", "Java Spring Boot Java Hibernate Java microservices, a bit of React")["ok"]


@pytest.mark.parametrize("title", ["Software Engineer - International Payments", "Full Stack Developer (Internal Tools)",
                                   "React Developer - Leading Fintech", "Frontend Engineer", "ReactJS / NodeJS",
                                   "Backend Developer (Node.js)", "Web Application Development - MERN"])
def test_titles_no_longer_wrongly_skipped(title):
    assert ok(title)["ok"], (title, ok(title))


@pytest.mark.parametrize("title", ["React Developer Intern", "Team Lead - Node.js", "HR Executive", "QA Engineer",
                                   "Solution Architect", "Shopify Store Manager", "Content Writer", "Internship - MERN"])
def test_titles_still_skipped(title):
    assert not ok(title)["ok"], title


def test_two_matching_skills_pass_for_a_generic_title():
    assert ok("Software Engineer", "We use React and Node.js.")["ok"]
    assert not ok("Software Engineer", "Design, develop, test and maintain software applications.")["ok"]


def test_english_words_are_not_skills():
    sk = extract_skills("Build the next generation of products; the rest of the team is responsive to feedback.")
    assert "Next.js" not in sk["core"] and "REST APIs" not in sk["core"] and "Responsive Design" not in sk["core"]
    assert "Next.js" in extract_skills("Next.js 14 and RESTful APIs")["core"]


def test_blocked_stack_compares_mentions_with_mentions():
    # 4 Python mentions, but our stack is mentioned far more often: not a Python job
    desc = ("React React React Node.js Node.js MongoDB MongoDB Express TypeScript TypeScript. "
            "Python scripts, Python tooling, Python data jobs, Python nice to have.")
    assert not is_blocked_stack("Full Stack Developer", desc)[0]
    assert is_blocked_stack("Developer", "Python Python Django Django Flask. Some React.")[0]


def test_strip_noise():
    from platforms.base import strip_noise
    t = "React Node MongoDB role\nSimilar jobs\nJava Developer\nPython Developer"
    assert strip_noise(t) == "React Node MongoDB role"


def test_current_title_and_company_answers():
    cfg = {"profile": {"full_name": "A B", "email": "a@b.c", "phone": "1", "total_experience_years": 2.8,
                       "current_title": "Software Developer", "current_company": "75Ways Technologies"}}
    assert answer("Job title", cfg=cfg) == "Software Developer"
    assert answer("Company", cfg=cfg) == "75Ways Technologies"
    assert answer("Why do you want to join our company?", cfg=cfg) is None


def test_naukri_queries_and_pages():
    from platforms import naukri
    cfg = {"roles": ["react developer"], "locations": ["Remote", "Noida"], "profile": {"total_experience_years": 2.8}}
    assert list(naukri.queries(cfg)) == [("react developer", "Remote", 2), ("react developer", "Remote", 3),
                                         ("react developer", "Noida", 2), ("react developer", "Noida", 3)]
    u = naukri.search_url("react developer", "Noida", 3, 2)
    assert "/react-developer-jobs-in-noida-2?" in u and "experience=3" in u


class FakePage:
    def __init__(self, body, title="", challenge=False):
        self.body, self._title, self.challenge = body, title, challenge

    def evaluate(self, js):
        return self.challenge if "querySelectorAll" in js else self.body

    def title(self):
        return self._title


def test_block_check_ignores_job_text_but_catches_real_blocks():
    from platforms.base import check_blocked, Blocked
    jd = "Candidates must clear a background security check. " + "React Node " * 400
    check_blocked(FakePage(jd))                                                      # long job page: not a block
    with pytest.raises(Blocked):
        check_blocked(FakePage("Verify you are human"))                              # short challenge page
    with pytest.raises(Blocked):
        check_blocked(FakePage(jd, challenge=True))                                  # visible captcha widget
    with pytest.raises(Blocked):
        check_blocked(FakePage(jd + " You have reached the daily limit"))


def test_instahyre_locations():
    from platforms.instahyre import location_ok
    want = ["Remote", "Delhi", "Gurugram", "Noida", "Mohali", "Chandigarh"]
    assert location_ok("Gurgaon", want) and location_ok("Work From Home", want) and location_ok("Bangalore, Noida", want)
    assert not location_ok("Bangalore", want) and not location_ok("Mumbai", want)


def test_custom_answers_win_and_feed_options():
    from core.answers import answer, pick_option
    cfg = {"profile": {"full_name": "A B", "email": "a@b.c", "phone": "1", "total_experience_years": 2.8},
           "answers": [{"match": "join immediately", "answer": "No"}, {"match": "bad[regex", "answer": "x"}]}
    assert answer("Can you join immediately?", cfg=cfg) == "No"
    assert pick_option("Can you join immediately?", ["Yes", "No"], cfg) == 1
    assert answer("Something else entirely", cfg=cfg) is None                # a broken regex is ignored, never guessed


def test_fresher_skipped_zoho_allowed():
    assert not ok("Software Developer- Fresher")["ok"]
    assert ok("Zoho Developer", "Zoho Creator, Deluge, JavaScript, REST APIs, HTML")["ok"]   # Zoho is allowed


def test_recaptcha_footer_is_not_a_block():
    from platforms.base import check_blocked
    check_blocked(FakePage("Add a resume\nThis site is protected by reCAPTCHA and the Google Privacy Policy"))  # short form page


def test_last_drawn_salary_and_full_stack_years():
    cfg = {"profile": {"full_name": "A B", "email": "a@b.c", "phone": "1", "total_experience_years": 2.8,
                       "current_ctc_lpa": 7.8, "skill_years": {"full stack": 2.8, "mern": 2.8}}}
    assert answer("What was your last drawn salary?", cfg=cfg) == "780000"
    assert answer("Last drawn monthly salary?", cfg=cfg) is None
    assert answer("How many years of experience do you have in Full Stack?", cfg=cfg) == "2.8"


def test_naukri_success_banner_is_recognised():
    from platforms.naukri import SUCCESS_RX
    assert SUCCESS_RX.search('Search jobs here 7 2 Applied to "Full Stack Developer" Send Me Jobs Like This Next step')
    assert SUCCESS_RX.search("Applied to “MERN Stack Developer”")
    assert not SUCCESS_RX.search("Shopify Developer Vayuz Technologies 3.186 Reviews 3 - 8 years Not Disclosed Gurugram Send me jobs like this")
    assert not SUCCESS_RX.search("Candidates who applied to similar roles also viewed")
