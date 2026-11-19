import sys, types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from platforms import foundit as F


def test_experience_range_brackets_the_candidate():
    assert F.experience_range(2.8) == (1, 4)
    assert F.experience_range(None) == (1, 3)


def test_search_url_filters():
    u = F.search_url("react developer", "Delhi", 2.8)
    assert "query=react%20developer" in u and "locations=Delhi" in u and "experienceRanges=1~4" in u and "freshness=7" in u
    r = F.search_url("react developer", "Remote", 2.8)
    assert "workFromHome=true" in r and "locations=" not in r


class Page:
    def __init__(self, labels, form=0, text=""):
        self.labels, self.form, self.text, self.clicked = labels, form, text, False
        self.context = types.SimpleNamespace(pages=[self])

    def evaluate(self, js, arg=None):
        if "'#applyNowBtn')].filter" in js:
            return self.labels
        if "dialog" in js:
            return self.form
        if js == "document.body.innerText":
            return self.text
        self.clicked = True


def test_precheck_outcomes():
    assert F.precheck(Page(["Applied"]), {})[0] == "skipped"
    assert F.precheck(Page(["Apply on company site"]), {})[0] == "external"
    assert F.precheck(Page([]), {})[0] == "failed"
    assert F.precheck(Page(["Quick Apply"]), {}) is None


def test_dry_run_never_clicks_and_questions_are_not_guessed(monkeypatch):
    monkeypatch.setattr(F, "human_sleep", lambda *a, **k: None)
    monkeypatch.setattr(F, "check_blocked", lambda p: None)
    monkeypatch.setattr(F, "snap", lambda *a, **k: None)
    p = Page(["Quick Apply"])
    assert F.apply(p, {}, "r.pdf", True)[0] == "dry_run" and not p.clicked
    q = Page(["Quick Apply"], form=3)
    assert F.apply(q, {}, "r.pdf", False)[0] == "needs_manual"
    ok = Page(["Quick Apply"], text='Applied successfully')
    assert F.apply(ok, {}, "r.pdf", False)[0] == "applied"
    unk = Page(["Quick Apply"], text="something else")
    assert F.apply(unk, {}, "r.pdf", False)[0] == "unconfirmed"
