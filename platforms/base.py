"""Browser plumbing shared by all platforms: persistent logged-in profile, human-like pacing, screenshots."""
import random
import time
from contextlib import contextmanager
from pathlib import Path
from core.config import ROOT, LOGS, load_config

BROWSER_DIR = ROOT / ".browser"
LOGIN_URLS = {
    "naukri": "https://www.naukri.com/nlogin/login",
    "linkedin": "https://www.linkedin.com/login",
    "instahyre": "https://www.instahyre.com/login/",
}


def human_sleep(lo_hi=None, cfg=None):
    lo, hi = lo_hi or (cfg or load_config())["pacing"]["action_delay"]
    time.sleep(random.uniform(lo, hi))


@contextmanager
def session(name: str, headless: bool = False):
    """Open the persistent profile for a platform (cookies from your manual login live here)."""
    from playwright.sync_api import sync_playwright
    d = BROWSER_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(d), headless=headless, viewport={"width": 1366, "height": 850}, locale="en-IN",
            timezone_id="Asia/Kolkata", accept_downloads=False)
        ctx.set_default_timeout(15000)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            yield page
        finally:
            ctx.close()


def interactive_login(name: str):
    print(f"A browser will open on {name}. Log in normally (OTP / password / 2FA yourself), "
          f"land on the logged-in home page, then come back here and press Enter.")
    with session(name) as page:
        page.goto(LOGIN_URLS[name])
        input("Press Enter once you are logged in... ")
    print(f"Saved. {name} is ready - your session is stored in .browser/{name} (no password is saved by this tool).")


def snap(page, tag: str):
    LOGS.mkdir(exist_ok=True)
    try:
        page.screenshot(path=str(LOGS / f"{int(time.time())}_{tag}.png"))
    except Exception:
        pass


BODY_JS = "document.body ? document.body.innerText : ''"


def page_text(page, selectors: list[str], limit: int = 9000) -> str:
    """Longest text among selectors; falls back to the whole page body."""
    best = ""
    for s in selectors:
        try:
            for el in page.query_selector_all(s)[:3]:
                t = (el.inner_text() or "").strip()
                if len(t) > len(best):
                    best = t
        except Exception:
            pass
    if len(best) < 150:
        try:
            best = page.evaluate(BODY_JS)
        except Exception:
            pass
    return best[:limit]


class Blocked(Exception):
    """Captcha / daily limit / unusual-activity warning: stop this platform for the run."""


def check_blocked(page):
    try:
        txt = page.evaluate(BODY_JS).lower()
    except Exception:
        return
    for needle in ("captcha", "unusual activity", "verify you are human", "daily limit", "limit reached",
                   "you've reached the limit", "temporarily restricted", "security check"):
        if needle in txt:
            raise Blocked(needle)
