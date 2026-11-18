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
    "indeed": "https://secure.indeed.com/auth?hl=en_IN&co=IN",
}


def human_sleep(lo_hi=None, cfg=None):
    lo, hi = lo_hi or (cfg or load_config())["pacing"]["action_delay"]
    time.sleep(random.uniform(lo, hi))


# ---- which browser -------------------------------------------------------------------------------------------------
# browser: chrome (default when Google Chrome is installed) = your real Google Chrome with its own profile in
#   .browser/chrome, shared by all sites. You log in there by hand with NO automation attached, so "Sign in with Google"
#   works. During a run the tool starts that same Chrome with a local debugging port and drives it (connect_over_cdp).
# browser: playwright = Playwright's bundled Chromium, one profile per site in .browser/<site> (Google refuses to sign
#   in there: "This browser or app may not be secure").
CHROME_PATHS = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                str(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable"]
CHROME_PROFILE = BROWSER_DIR / "chrome"


def _browser_cfg() -> tuple[str, str | None, int]:
    cfg = load_config()
    path = cfg.get("chrome_path") or next((p for p in CHROME_PATHS if Path(p).exists()), None)
    mode = cfg.get("browser") or ("chrome" if path else "playwright")
    if mode == "chrome" and not path:
        raise SystemExit("browser: chrome, but Google Chrome was not found. Set chrome_path in config.yaml "
                         "or use browser: playwright")
    return mode, path, int(cfg.get("chrome_debug_port") or 9333)


def _start_chrome(path: str, urls: list[str], port: int | None = None):
    import subprocess
    CHROME_PROFILE.mkdir(parents=True, exist_ok=True)
    args = [path, f"--user-data-dir={CHROME_PROFILE}", "--no-first-run", "--no-default-browser-check",
            "--window-size=1366,900"]
    if port:
        args += [f"--remote-debugging-port={port}", "--remote-debugging-address=127.0.0.1"]
    return subprocess.Popen(args + urls, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _wait_port(port: int, proc, secs: int = 20) -> bool:
    import urllib.request
    for _ in range(secs * 4):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1).read()
            return True
        except Exception:
            if proc.poll() is not None:            # exited at once: this profile is already open in another window
                return False
            time.sleep(0.25)
    return False


_HOST = {"port": None}       # set while browser_host() keeps one Chrome open for the whole run


@contextmanager
def browser_host():
    """Start the job-applier Chrome ONCE for a run, so several platforms (threads) can each open their own tab on it.
    No-op in playwright mode."""
    mode, path, port = _browser_cfg()
    if mode != "chrome":
        yield
        return
    proc = _start_chrome(path, ["about:blank"], port)
    if not _wait_port(port, proc):
        proc.terminate()
        raise RuntimeError("could not start Chrome for automation. Quit the Chrome window of the job-applier profile "
                           "(the one opened by `python main.py login`) and run again. Your normal Chrome can stay open.")
    _HOST["port"] = port
    try:
        yield
    finally:
        _HOST["port"] = None
        try:                                            # graceful quit: Chrome writes the cookies to disk
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                b = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                b.new_browser_cdp_session().send("Browser.close")
        except Exception:
            pass
        try:
            proc.wait(timeout=15)
        except Exception:
            proc.terminate()


@contextmanager
def session(name: str, headless: bool = False):
    """A logged-in page for a platform (cookies from your manual login live in the profile).
    Inside browser_host(): a new tab on the shared Chrome (one Playwright connection per thread)."""
    from playwright.sync_api import sync_playwright
    mode, path, port = _browser_cfg()
    if mode == "chrome" and _HOST["port"]:
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{_HOST['port']}")
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            ctx.set_default_timeout(15000)
            page = ctx.new_page()
            try:
                yield page
            finally:
                try:
                    page.close()
                except Exception:
                    pass
        return
    if mode == "chrome":
        proc = _start_chrome(path, ["about:blank"], port)
        if not _wait_port(port, proc):
            proc.terminate()
            raise RuntimeError("could not start Chrome for automation. Quit the Chrome window of the job-applier profile "
                               "(the one opened by `python main.py login`) and run again. Your normal Chrome can stay open.")
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                ctx = browser.contexts[0] if browser.contexts else browser.new_context()
                ctx.set_default_timeout(15000)
                page = ctx.new_page()
                try:
                    yield page
                finally:
                    try:                                       # graceful quit: Chrome writes the cookies to disk
                        browser.new_browser_cdp_session().send("Browser.close")
                    except Exception:
                        pass
        finally:
            try:
                proc.wait(timeout=15)
            except Exception:
                proc.terminate()
        return
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
    """name = one site, or 'all' (every site in its own tab)."""
    names = [n for n in LOGIN_URLS if n != "linkedin"] if name == "all" else [name]
    mode, path, _ = _browser_cfg()
    if mode == "chrome":
        proc = _start_chrome(path, [LOGIN_URLS[n] for n in names])
        print(f"Google Chrome opened ({', '.join(names)}), with the job-applier profile and NO automation attached.\n"
              f"Log in normally in each tab (Sign in with Google works here; OTP / password / 2FA yourself).\n"
              f"When done, QUIT that Chrome window with Cmd+Q (this saves the session), then come back here.")
        input("Press Enter after quitting that Chrome window... ")
        try:
            proc.wait(timeout=20)
        except Exception:
            print("That Chrome window still seems open - please quit it (Cmd+Q) before running the applier.")
        print(f"Saved. Sessions are stored in {CHROME_PROFILE.relative_to(ROOT)} (no password is saved by this tool).")
        return
    print(f"A browser will open on {name}. Log in normally (OTP / password / 2FA yourself), "
          f"land on the logged-in home page, then come back here and press Enter.")
    for n in names:
        with session(n) as page:
            page.goto(LOGIN_URLS[n])
            input(f"Press Enter once you are logged in to {n}... ")
    print(f"Saved. Sessions are stored in .browser/ (no password is saved by this tool).")


def snap(page, tag: str):
    LOGS.mkdir(exist_ok=True)
    try:
        page.screenshot(path=str(LOGS / f"{int(time.time())}_{tag}.png"))
    except Exception:
        pass


BODY_JS = "document.body ? document.body.innerText : ''"


import re as _re

# where the job itself ends on a full page: everything after is other jobs (full of Java / Python / PHP) or boilerplate,
# which would wrongly count as "this job asks for a blocked stack"
_NOISE_RX = _re.compile(r"\n\s*(similar jobs|jobs you might|jobs you may|recommended jobs|people also (viewed|applied)|"
                        r"more jobs (like|from)|other jobs|related jobs|beware of imposters|explore more jobs)", _re.I)


def strip_noise(text: str) -> str:
    m = _NOISE_RX.search(text or "")
    return text[:m.start()] if m else (text or "")


def page_text(page, selectors: list[str], limit: int = 9000) -> str:
    """Longest text among selectors; falls back to the page body, cut off where other jobs start."""
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
            body = strip_noise(page.evaluate(BODY_JS))
            if len(body) > len(best):
                best = body
        except Exception:
            pass
    return best[:limit]


class Blocked(Exception):
    """Captcha / daily limit / unusual-activity warning: stop this platform for the run."""


# a VISIBLE challenge widget = a real block. (Only visible ones: login pages keep hidden captcha markup around.)
# Not the invisible reCAPTCHA v3 badge ("This site is protected by reCAPTCHA", .grecaptcha-badge, size=invisible) that
# Indeed's apply form always carries: only a real challenge (image grid "bframe", "I'm not a robot" box, hCaptcha,
# Cloudflare) counts.
CHALLENGE_JS = """() => [...document.querySelectorAll('iframe[src*="recaptcha"], iframe[src*="hcaptcha"],
  iframe[src*="challenges.cloudflare"], #challenge-form, #challenge-stage, .cf-turnstile, [id*="captcha" i], [class*="captcha" i]')]
  .some(e => {
    if (e.offsetParent === null || e.getBoundingClientRect().height <= 20) return false;
    if (e.closest('.grecaptcha-badge') || /grecaptcha-badge|grecaptcha-logo/i.test(e.className || '')) return false;
    const src = e.getAttribute('src') || '';
    if (/recaptcha/.test(src) && /size=invisible/.test(src)) return false;
    return true; })"""
# bot-check wording: only trusted on short pages / the title (a job description may well say "must pass a security check")
BOT_WORDS = ("captcha", "unusual activity", "verify you are human", "additional verification required", "security check",
             "access denied", "just a moment")
# limit wording: specific enough to trust anywhere on the page
LIMIT_WORDS = ("daily limit", "limit reached", "you've reached the limit", "temporarily restricted")
SHORT_PAGE = 2500


def check_blocked(page):
    try:
        txt = page.evaluate(BODY_JS).lower()
        title = (page.title() or "").lower()
    except Exception:
        return
    for needle in LIMIT_WORDS:
        if needle in txt:
            raise Blocked(needle)
    short = len(txt.strip()) < SHORT_PAGE
    # "This site is protected by reCAPTCHA" is a footer on normal forms (Indeed Apply), not a challenge: drop the brand
    # name before looking for the word "captcha". A real reCAPTCHA challenge is caught by CHALLENGE_JS below.
    txt = _re.sub(r"recaptcha", "", txt)
    title = _re.sub(r"recaptcha", "", title)
    for needle in BOT_WORDS:
        if needle in title or (short and needle in txt):
            raise Blocked(needle)
    try:
        if page.evaluate(CHALLENGE_JS):
            raise Blocked("captcha")
    except Blocked:
        raise
    except Exception:
        pass
