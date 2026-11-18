"""Run report: HTML dashboard + CSV + terminal summary, all read from SQLite."""
import csv
import html
import re
import time
from collections import Counter, defaultdict
from datetime import datetime
from .config import OUT

APPLIED = ("applied", "dry_run")      # dry_run = would have applied
BUCKETS = [("Remote", r"remote|work from home|wfh|anywhere"), ("Delhi", r"delhi"),
           ("Gurugram", r"gurugram|gurgaon"), ("Noida", r"noida"),
           ("Mohali", r"mohali|sas nagar|punjab"), ("Chandigarh", r"chandigarh|panchkula"),
           ("Ludhiana", r"ludhiana")]


def bucket(loc: str, remote: int) -> str:
    if remote:
        return "Remote"
    for name, rx in BUCKETS:
        if re.search(rx, loc or "", re.I):
            return name
    return "Other"


def _reason_key(r: str) -> str:
    r = re.sub(r"\d+(\.\d+)?", "N", r or "")
    return r.split(":")[0].strip() or "unspecified"


def gather(con, run_id: int | None = None) -> dict:
    where, args = ("WHERE run_id=?", (run_id,)) if run_id else ("", ())
    rows = [dict(r) for r in con.execute(f"SELECT * FROM applications {where} ORDER BY ts", args)]
    run = dict(con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()) if run_id else None
    st = Counter(r["status"] for r in rows)
    applied = [r for r in rows if r["status"] in APPLIED]
    by_platform = defaultdict(Counter)
    by_loc = defaultdict(Counter)
    for r in rows:
        by_platform[r["platform"]][r["status"]] += 1
        by_loc[bucket(r["location"], r["remote"])][r["status"]] += 1
    skills = Counter(s for r in applied for s in (r["matched_skills"] or "").split(",") if s)
    companies = Counter(r["company"] for r in applied)
    skip_reasons = Counter(_reason_key(r["reason"]) for r in rows if r["status"] == "skipped")
    slots = Counter()
    t0 = rows[0]["ts"] if rows else time.time()
    for r in applied:
        slots[int((r["ts"] - t0) // 600) * 10] += 1
    fits = [r["fit_score"] for r in applied if r["fit_score"] is not None]
    life = {
        "applied": con.execute("SELECT COUNT(*) c FROM applications WHERE status='applied'").fetchone()["c"],
        "runs": con.execute("SELECT COUNT(*) c FROM runs").fetchone()["c"],
        "by_platform": dict(con.execute(
            "SELECT platform, COUNT(*) c FROM applications WHERE status='applied' GROUP BY platform").fetchall()),
        "by_day": [(r["d"], r["c"]) for r in con.execute(
            "SELECT date(ts,'unixepoch','localtime') d, COUNT(*) c FROM applications WHERE status='applied' "
            "GROUP BY d ORDER BY d DESC LIMIT 14")],
    }
    dur = ((run["ended_at"] or time.time()) - run["started_at"]) / 60 if run else None
    return {
        "rows": rows, "run": run, "status": st, "total": len(rows), "applied_n": len(applied),
        "by_platform": by_platform, "by_loc": by_loc, "skills": skills.most_common(15),
        "companies": companies.most_common(10), "skip_reasons": skip_reasons.most_common(),
        "slots": sorted(slots.items()), "avg_fit": (sum(fits) / len(fits)) if fits else 0,
        "success_rate": (100 * st["applied"] / max(1, st["applied"] + st["failed"] + st["external"] + st["needs_manual"])),
        "duration_min": dur, "cost_inr": (run or {}).get("claude_cost_inr", 0) or 0, "life": life,
        "dry": bool(run and run["dry_run"]),
    }


def write_csv(rows, path):
    cols = ["ts", "platform", "title", "company", "location", "remote", "exp_text", "fit_score", "status",
            "reason", "matched_skills", "url", "resume_path"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            r = dict(r)
            r["ts"] = datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M:%S")
            w.writerow(r)


def terminal_summary(g: dict) -> str:
    st = g["status"]
    lines = ["", "=" * 60, "JOB APPLIER RUN SUMMARY" + ("  (DRY RUN - nothing was submitted)" if g["dry"] else ""), "=" * 60]
    if g["duration_min"] is not None:
        lines.append(f"Duration            : {g['duration_min']:.0f} min")
    lines += [f"Jobs reviewed       : {g['total']}",
              f"Applied             : {st['applied']}" + (f"  (+{st['dry_run']} dry-run)" if st["dry_run"] else ""),
              f"Queued (waiting)    : {st['queued']}",
              f"Skipped             : {st['skipped']}",
              f"External / manual   : {st['external']} / {st['needs_manual']}",
              f"Unconfirmed         : {st['unconfirmed']}  (clicked Apply, success not proven - check the site)",
              f"Failed              : {st['failed']}",
              f"Avg fit score       : {g['avg_fit']:.0f}",
              f"Claude cost         : Rs {g['cost_inr']:.1f}", "", "By platform:"]
    for p, c in g["by_platform"].items():
        lines.append(f"  {p:<10} applied {c['applied'] + c['dry_run']:>3} | skipped {c['skipped']:>3} | "
                     f"external {c['external']:>2} | manual {c['needs_manual']:>2} | failed {c['failed']:>2}")
    lines.append("By location (applied):")
    for loc, c in sorted(g["by_loc"].items(), key=lambda kv: -(kv[1]["applied"] + kv[1]["dry_run"])):
        lines.append(f"  {loc:<11} {c['applied'] + c['dry_run']:>3}")
    if g["skip_reasons"]:
        lines.append("Top skip reasons:")
        lines += [f"  {n:>3}  {r}" for r, n in g["skip_reasons"][:5]]
    lines.append(f"Lifetime applied    : {g['life']['applied']} across {g['life']['runs']} runs")
    lines.append("=" * 60)
    return "\n".join(lines)


CSS = """
:root{--bg:#fff;--fg:#1a1a1a;--mut:#667;--card:#f5f6f8;--line:#dde;--acc:#2563eb;--ok:#16a34a;--warn:#d97706;--bad:#dc2626}
@media (prefers-color-scheme:dark){:root{--bg:#15171a;--fg:#e8e8e8;--mut:#9aa;--card:#1f2226;--line:#333;--acc:#60a5fa;--ok:#4ade80;--warn:#fbbf24;--bad:#f87171}}
body{font:14px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--fg);margin:0;padding:16px;max-width:1200px;margin:auto}
h1{font-size:22px}h2{font-size:16px;margin:28px 0 8px}.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px}
.tile{background:var(--card);border-radius:8px;padding:10px 12px}.tile b{display:block;font-size:22px}.tile span{color:var(--mut);font-size:12px}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);font-size:13px;vertical-align:top}
th{color:var(--mut);cursor:pointer;white-space:nowrap}.bar{height:10px;background:var(--acc);border-radius:3px;min-width:2px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px}.st-applied,.st-dry_run{color:var(--ok)}
.st-skipped{color:var(--mut)}.st-unconfirmed{color:var(--warn);font-weight:600}.st-queued{color:var(--acc)}.st-external,.st-needs_manual{color:var(--warn)}.st-failed{color:var(--bad)}
input,select{padding:5px;background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:6px}
.wrap{overflow-x:auto}a{color:var(--acc)}.note{color:var(--mut)}
"""
JS = """
const t=document.getElementById('apps'),q=document.getElementById('q'),sp=document.getElementById('sp'),ss=document.getElementById('ss');
function f(){const a=q.value.toLowerCase(),p=sp.value,s=ss.value;for(const r of t.tBodies[0].rows){
 const ok=(!a||r.textContent.toLowerCase().includes(a))&&(!p||r.dataset.p===p)&&(!s||r.dataset.s===s);r.style.display=ok?'':'none'}}
[q,sp,ss].forEach(e=>e.oninput=f);
t.tHead.onclick=e=>{const i=e.target.cellIndex;if(i==null)return;const rows=[...t.tBodies[0].rows],d=t.dataset.d=t.dataset.d==='1'?'-1':'1';
 rows.sort((a,b)=>{const x=a.cells[i].textContent,y=b.cells[i].textContent,n=parseFloat(x)-parseFloat(y);return (isNaN(n)?x.localeCompare(y):n)*d});rows.forEach(r=>t.tBodies[0].appendChild(r))};
"""


def _bars(pairs, unit=""):
    if not pairs:
        return "<p class=note>No data.</p>"
    mx = max(v for _, v in pairs) or 1
    out = ["<table>"]
    for k, v in pairs:
        out.append(f"<tr><td style='width:34%'>{html.escape(str(k))}</td>"
                   f"<td><div class=bar style='width:{100 * v / mx:.0f}%'></div></td><td style='width:50px'>{v}{unit}</td></tr>")
    out.append("</table>")
    return "".join(out)


def render_html(g: dict, title="Job Applier Report") -> str:
    st = g["status"]
    e = html.escape
    tile = lambda v, l: f"<div class=tile><b>{v}</b><span>{l}</span></div>"
    tiles = "".join([
        tile(g["total"], "jobs reviewed"), tile(st["applied"] + st["dry_run"], "dry-run (would apply)" if g["dry"] else "applied"),
        tile(st["queued"], "queued (waiting)"), tile(st["skipped"], "skipped"), tile(st["unconfirmed"], "unconfirmed - check site"), tile(st["external"], "external site"), tile(st["needs_manual"], "needs you"),
        tile(st["failed"], "failed"), tile(f"{g['success_rate']:.0f}%", "success rate"),
        tile(f"{g['avg_fit']:.0f}", "avg fit score"),
        tile(f"{g['duration_min']:.0f}m" if g["duration_min"] is not None else "-", "duration"),
        tile(f"Rs {g['cost_inr']:.1f}", "Claude cost")])
    plat = "".join(
        f"<tr><td>{e(p)}</td><td>{c['applied'] + c['dry_run']}</td><td>{c['queued']}</td><td>{c['skipped']}</td><td>{c['external']}</td>"
        f"<td>{c['needs_manual']}</td><td>{c['failed']}</td></tr>" for p, c in g["by_platform"].items())
    loc = [(k, c["applied"] + c["dry_run"]) for k, c in sorted(
        g["by_loc"].items(), key=lambda kv: -(kv[1]["applied"] + kv[1]["dry_run"]))]
    manual = [r for r in g["rows"] if r["status"] in ("external", "needs_manual", "unconfirmed")]
    manual_html = "".join(f"<tr><td>{e(r['platform'])}</td><td>{e(r['title'] or '')}</td><td>{e(r['company'] or '')}</td>"
                          f"<td>{e(r['reason'] or '')}</td><td><a href='{e(r['url'] or '#')}'>open</a></td></tr>" for r in manual)
    rows_html = []
    for r in g["rows"]:
        when = datetime.fromtimestamp(r["ts"]).strftime("%H:%M:%S")
        rp = f"<a href='file://{e(r['resume_path'])}'>pdf</a>" if r["resume_path"] else ""
        rows_html.append(
            f"<tr data-p='{e(r['platform'])}' data-s='{r['status']}'><td>{when}</td><td>{e(r['platform'])}</td>"
            f"<td>{e(r['title'] or '')}</td><td>{e(r['company'] or '')}</td><td>{e(r['location'] or '')}</td>"
            f"<td>{'remote' if r['remote'] else 'onsite'}</td><td>{e(r['exp_text'] or '')}</td>"
            f"<td>{'' if r['fit_score'] is None else round(r['fit_score'])}</td><td class='st-{r['status']}'>{r['status']}</td>"
            f"<td>{e(r['reason'] or '')}</td><td>{e((r['matched_skills'] or '').replace(',', ', '))}</td>"
            f"<td><a href='{e(r['url'] or '#')}'>job</a> {rp}</td></tr>")
    plats = sorted({r["platform"] for r in g["rows"]})
    life = g["life"]
    return f"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>{e(title)}</title><style>{CSS}</style></head><body>
<h1>{e(title)}{' - DRY RUN' if g['dry'] else ''}</h1>
<p class=note>Generated {datetime.now().strftime('%d %b %Y %H:%M')}. Lifetime: {life['applied']} applications across {life['runs']} runs.</p>
<div class=tiles>{tiles}</div>
<h2>By platform</h2><div class=wrap><table><tr><th>Platform</th><th>Applied</th><th>Queued</th><th>Skipped</th><th>External</th><th>Needs you</th><th>Failed</th></tr>{plat}</table></div>
<div class=grid>
<div><h2>Applied by location</h2>{_bars(loc)}</div>
<div><h2>Applications per 10 min</h2>{_bars([(f'{m}-{m+10} min', n) for m, n in g['slots']])}</div>
<div><h2>Top matched skills</h2>{_bars(g['skills'])}</div>
<div><h2>Top companies applied</h2>{_bars(g['companies'])}</div>
<div><h2>Why jobs were skipped</h2>{_bars(g['skip_reasons'])}</div>
<div><h2>Lifetime by day</h2>{_bars(life['by_day'])}</div>
</div>
<h2>Needs you ({len(manual)})</h2><p class=note>Finish these by hand - they redirect to a company site or ask questions the tool will not guess.</p>
<div class=wrap><table><tr><th>Platform</th><th>Title</th><th>Company</th><th>Why</th><th>Link</th></tr>{manual_html}</table></div>
<h2>All jobs ({g['total']})</h2>
<p><input id=q placeholder="search"> <select id=sp><option value="">all platforms</option>{''.join(f'<option>{e(p)}</option>' for p in plats)}</select>
<select id=ss><option value="">all statuses</option>{''.join(f'<option>{s}</option>' for s in ['applied','unconfirmed','dry_run','queued','skipped','external','needs_manual','failed'])}</select></p>
<div class=wrap><table id=apps><thead><tr><th>Time</th><th>Platform</th><th>Title</th><th>Company</th><th>Location</th><th>Type</th><th>Exp</th><th>Fit</th><th>Status</th><th>Reason</th><th>Matched skills</th><th>Links</th></tr></thead>
<tbody>{''.join(rows_html)}</tbody></table></div>
<script>{JS}</script></body></html>"""


def build(con, run_id: int | None = None) -> dict:
    OUT.mkdir(exist_ok=True)
    g = gather(con, run_id)
    (OUT / "report.html").write_text(render_html(g))
    write_csv(g["rows"], OUT / "applications.csv")
    return g
