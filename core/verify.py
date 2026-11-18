"""Settle 'unconfirmed' applications (clicked Apply, success not proven) by reading the site's own 'Applied jobs' list.

Each platform module offers  applied_list(page) -> [{"title": ..., "company": ...}, ...]  (what the site says you applied to).
Here: match those entries to our unconfirmed rows and update the tracker. Nothing is ever clicked."""
import importlib
import re
import time
from . import db
from .companies import norm_company
from platforms.base import session


def _load(name):
    return importlib.import_module(f"platforms.{name}")


def _tokens(title: str) -> set[str]:
    t = re.sub(r"[^a-z0-9+#.]+", " ", (title or "").lower())
    t = t.replace("reactjs", "react").replace("react.js", "react").replace("nodejs", "node").replace("node.js", "node")
    t = t.replace("nextjs", "next").replace("next.js", "next").replace("fullstack", "full stack").replace("full-stack", "full stack")
    return {w for w in t.split() if w not in {"a", "an", "the", "and", "or", "of", "for", "-", "|", "developer", "engineer"}}


def titles_match(a: str, b: str) -> bool:
    """Same role, allowing the site to shorten / reword it ('Full Stack Developer (MERN)' ~ 'Full Stack Developer')."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return True                      # a title that is only 'Developer': company match is all we have
    return len(ta & tb) / min(len(ta), len(tb)) >= 0.6


def same_company(a: str, b: str) -> bool:
    """Equal after normalising, or one is the other plus extra words ('Mobiloitte' ~ 'Mobiloitte Technologies')."""
    na, nb = norm_company(a), norm_company(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    short, long_ = sorted((na, nb), key=len)
    return len(short) >= 6 and long_.startswith(short)


def match_applied(rows: list[dict], entries: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """rows = our unconfirmed rows. entries = what the site's Applied list shows.
    -> (confirmed rows, rows whose COMPANY is listed but with another title, rows not listed at all)."""
    confirmed, other_title, missing = [], [], []
    for r in rows:
        cands = [e for e in entries if same_company(r["company"], e.get("company", ""))]
        if any(titles_match(r["title"], e.get("title", "")) for e in cands):
            confirmed.append(r)
        elif cands:
            other_title.append(r)
        else:
            missing.append(r)
    return confirmed, other_title, missing


RECENT = 3 * 86400          # 'applied' rows this young are re-checked against the external-site list


def settle(con, platform: str, entries: list[dict], retry_missing: bool = False, external: list[dict] | None = None) -> dict:
    """Update the tracker for one platform from its Applied list (entries) and, when given, its external-site list.
    A job the site files under 'external site' was NOT applied there: it becomes `external` (company-site hand-off),
    even if it showed an 'Applied' banner. Returns the rows in each outcome."""
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM applications WHERE platform=? AND status='unconfirmed'", (platform,))]
    confirmed, other_title, missing = match_applied(rows, entries)
    now = time.time()
    for r in confirmed:
        con.execute("UPDATE applications SET status='applied', reason=?, ts=? WHERE id=?",
                    (f"confirmed on the {platform} Applied jobs list", r["ts"], r["id"]))
    to_external = []
    if external:
        recent = [dict(r) for r in con.execute(
            "SELECT * FROM applications WHERE platform=? AND status='applied' AND ts>?", (platform, now - RECENT))]
        listed = {r["id"] for r in match_applied(recent, entries)[0]}               # really on the site's own list: leave alone
        cand = [r for r in missing + other_title + recent if r["id"] not in listed]
        to_external = match_applied(cand, external)[0]
        for r in to_external:
            con.execute("UPDATE applications SET status='external', reason=? WHERE id=?",
                        (f"{platform} lists this under external-site jobs: apply on the company website", r["id"]))
        ext_ids = {r["id"] for r in to_external}
        missing = [r for r in missing if r["id"] not in ext_ids]
        other_title = [r for r in other_title if r["id"] not in ext_ids]
    if retry_missing:                                  # only when the list was read in full: tried again next run
        for r in missing:
            con.execute("UPDATE applications SET status='failed', reason=?, ts=? WHERE id=?",
                        (f"not on the {platform} Applied jobs list ({time.strftime('%Y-%m-%d', time.localtime(now))}) - retried next run",
                         now, r["id"]))
    con.commit()
    return {"confirmed": confirmed, "other_title": other_title, "missing": missing, "to_external": to_external,
            "listed": len(entries)}


def needs_verify(con, name: str) -> bool:
    """Anything to settle on this platform: unconfirmed jobs, or recent 'applied' ones (may really be company-site jobs)."""
    return bool(con.execute("SELECT 1 FROM applications WHERE platform=? AND (status='unconfirmed' OR (status='applied' AND ts>?)) LIMIT 1",
                            (name, time.time() - RECENT)).fetchone())


def run_verify(con, names: list[str], retry_missing: bool = False, log=print) -> list[dict]:
    """For each platform that has unconfirmed jobs and an Applied-list reader: open the site (a tab on the already
    running Chrome, or a fresh browser), read the list, settle the tracker. Read-only. One platform failing never
    stops the others. Returns [{platform, listed, confirmed, other_title, missing}]."""
    out = []
    for name in names:
        if not needs_verify(con, name):
            continue
        P = _load(name)
        if not hasattr(P, "applied_list"):
            log(f"  [verify] {name}: no Applied-list reader - skipped")
            continue
        try:
            with session(name) as page:
                if not P.logged_in(page):
                    log(f"  [verify] {name}: not logged in - skipped")
                    continue
                entries = P.applied_list(page)
                external = P.external_list(page) if hasattr(P, "external_list") else None
        except Exception as e:
            log(f"  [verify] {name}: could not read the Applied list ({type(e).__name__}: {str(e)[:100]})")
            continue
        if not entries:                                  # an empty read is a failed read, never "nothing applied"
            log(f"  [verify] {name}: the Applied list came back empty - left everything as it was")
            continue
        res = settle(con, name, entries, retry_missing=retry_missing, external=external)
        res["platform"] = name
        out.append(res)
        log(f"  [verify] {name}: site lists {res['listed']} applied jobs -> confirmed {len(res['confirmed'])}, "
            f"other title {len(res['other_title'])}, not listed {len(res['missing'])}, moved to company-site {len(res['to_external'])}")
    return out
