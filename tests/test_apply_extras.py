import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pytest
from core import coverletter, db, mailer
from core.answers import answer, pick_option
import core.config as cc

CFG = {"profile": {"full_name": "Archit Jain", "email": "a@b.c", "phone": "1", "total_experience_years": 2.8, "current_ctc_lpa": 7.8,
                   "expected_ctc_lpa": 12, "notice_period_days": 30, "willing_to_relocate": True, "work_authorized_india": True,
                   "skill_years": {"react": 2.8, "shopify": 2}},
       "preferences": {"salary_unit_default": "rupees", "consent_checkbox": True, "cover_letter_ai": True, "email_applications": True},
       "answers": [{"match": "workplace model", "answer": "Yes"}, {"match": "shift", "answer": "Yes"}]}
GOOD = ("Dear Hiring Team at Acme, I am applying for the React Developer role. I have 2.8 years of experience building full-stack "
        "web applications with React.js, Node.js, Express.js, MongoDB and TypeScript at 75Ways Technologies. At Nowfit I built "
        "coach and admin panels and Stripe subscription flows, and at Shadowbreeze I built a Shopify gift flow with webhooks and "
        "JWT-secured links. I enjoy shipping clean, maintainable interfaces and working closely with designers and backend teammates. "
        "I would welcome a conversation about how I can contribute. Thank you for your time. Archit Jain")


def test_salary_units():
    assert answer("Expected CTC", cfg=CFG) == "1200000"                 # saved default: annual rupees
    assert answer("Expected CTC (LPA)", cfg=CFG) == "12"                # a named unit wins
    assert answer("Current monthly salary", cfg=CFG) is None            # monthly: never guessed
    assert answer("Expected CTC", cfg={**CFG, "preferences": {"salary_unit_default": "lpa"}}) == "12"
    assert answer("Expected CTC", cfg={**CFG, "preferences": {"salary_unit_default": "ask"}}) is None


def test_choice_questions_follow_saved_preferences():
    wp = ["Select an answer", "Yes, comfortable with Hybrid", "Prefer onsite", "Prefer hybrid", "Remote only"]
    assert pick_option("Are you comfortable with this role's workplace model?", wp, CFG) == 1
    assert pick_option("Are you available for the shift listed in this job post?", ["Yes", "No", "Needs discussion"], CFG) == 0
    rel = ["Yes, I can make the commute", "Yes, I am planning to relocate", "Yes, but I need relocation assistance", "No"]
    assert pick_option("Will you be able to reliably commute or relocate to Gurugram?", rel, CFG) == 1
    assert pick_option("Will you relocate?", ["Yes, I can make the commute", "No"], CFG) is None      # never claims a commute


def test_consent_box_detection():
    from platforms.indeed import is_consent_box
    assert is_consent_box("I confirm that the information provided is accurate and agree to the Privacy Policy")
    assert not is_consent_box("Send me job alerts and offers on WhatsApp")
    assert not is_consent_box("Subscribe to our newsletter and agree to marketing emails")


def test_cover_letter_guard():
    assert coverletter.check(GOOD, CFG) == []
    assert any("not on the resume" in p for p in coverletter.check(GOOD + " I also know Java and Angular.", CFG))
    assert any("number" in p for p in coverletter.check(GOOD.replace("2.8 years", "7 years"), CFG))
    assert any("placeholder" in p for p in coverletter.check(GOOD + " [Company Name]", CFG))
    assert any("short" in p for p in coverletter.check("Hi, I want the job.", CFG))


class FakeSMTP:
    sent = []
    def __init__(self, host, port, timeout=0): self.host = host
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def starttls(self): pass
    def login(self, u, p): FakeSMTP.login_as = (u, p)
    def send_message(self, m): FakeSMTP.sent.append(m)


@pytest.fixture
def mail_env(monkeypatch, tmp_path):
    monkeypatch.setattr(mailer, "load_config", lambda: CFG)
    monkeypatch.setattr(coverletter, "load_config", lambda: CFG)
    monkeypatch.setattr(mailer, "LOG", tmp_path / "email_log.csv")
    monkeypatch.setattr(mailer, "OUT", tmp_path)
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(mailer, "resume_file", lambda cfg=None: tmp_path / "missing.pdf")
    monkeypatch.setenv("SMTP_USER", "me@gmail.com"); monkeypatch.setenv("SMTP_PASSWORD", "app-password")
    FakeSMTP.sent.clear()
    con = db.connect(tmp_path / "t.db")
    rid = db.start_run(con, False)
    db.log_application(con, rid, {"platform": "naukri", "job_id": "1", "title": "React Developer", "company": "Acme", "location": "x",
                                  "remote": 0, "url": "u", "exp_text": ""}, "external", "redirects")
    return con


def test_email_dry_run_then_send(mail_env):
    con = mail_env
    r = mailer.send_application(con, "naukri:1", "careers@acme.com", GOOD, send=False)
    assert r["ok"] and not r["sent"] and FakeSMTP.sent == []                       # dry run sends nothing
    r = mailer.send_application(con, "naukri:1", "careers@acme.com", GOOD, found_on="https://acme.com/careers", send=True)
    assert r["sent"] and len(FakeSMTP.sent) == 1
    m = FakeSMTP.sent[0]
    assert m["To"] == "careers@acme.com" and "React Developer" in m["Subject"] and "Archit Jain" in m.get_content()
    st = con.execute("SELECT status, reason FROM applications").fetchone()
    assert st["status"] == "applied" and "by email to careers@acme.com" in st["reason"]
    again = mailer.send_application(con, "naukri:1", "careers@acme.com", GOOD, send=True)       # one application per company
    assert not again["ok"] and any("already applied" in p for p in again["problems"]) and len(FakeSMTP.sent) == 1


def test_email_refuses_bad_input(mail_env):
    con = mail_env
    assert not mailer.send_application(con, "naukri:1", "noreply@acme.com", GOOD, send=True)["ok"]
    assert not mailer.send_application(con, "naukri:1", "careers@acme.com", GOOD + " I know Java.", send=True)["ok"]
    assert not mailer.send_application(con, "naukri:99", "careers@acme.com", GOOD, send=True)["ok"]
    assert FakeSMTP.sent == []


def test_brief_has_preferences_and_email_rules(tmp_path, monkeypatch):
    from core import handoff
    monkeypatch.setattr(handoff, "HANDOFF", tmp_path / "h")
    monkeypatch.setattr(handoff, "load_config", lambda: {**cc.load_config(), **{"preferences": CFG["preferences"], "answers": CFG["answers"]}})
    handoff.build(db.connect(tmp_path / "t.db"))
    b = (tmp_path / "h" / "ANTIGRAVITY_BRIEF.md").read_text()
    for needle in ("Saved preferences", "annual, in full rupees", "tick it", "Cover letters", "Applying by email", "python main.py email", "CAPTCHA"):
        assert needle in b, needle
    assert b.index("9. **Every job") < b.index("10. **Cover letter")
