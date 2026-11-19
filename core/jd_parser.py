"""Pull known skills out of a job description and detect blocked / off-stack jobs."""
import re
from .config import load_skills, flat

# canonical skill -> extra spellings that appear in job posts
SYNONYMS = {
    "React.js": ["react", "reactjs", "react js", "react.js"],
    "Next.js": ["nextjs", "next js", "next.js"],          # not bare "next": "the next generation of ..." is not Next.js
    "Node.js": ["node", "nodejs", "node js", "node.js"],
    "Express.js": ["express", "expressjs", "express.js"],
    "TypeScript": ["typescript", "ts"],
    "JavaScript": ["javascript", "js", "es6", "es2015", "ecmascript"],
    "MongoDB": ["mongodb", "mongo db", "mongo"],
    "PostgreSQL": ["postgresql", "postgres", "psql"],
    "SQL": ["sql", "mysql"],
    "Redux Toolkit": ["redux", "redux toolkit", "rtk"],
    "Tailwind CSS": ["tailwind", "tailwindcss", "tailwind css"],
    "MUI": ["mui", "material ui", "material-ui"],
    "HTML5": ["html", "html5"],
    "CSS3": ["css", "css3", "scss", "sass"],
    "REST APIs": ["restful", "rest api", "rest apis", "restful api", "restful apis", "rest-api", "rest based"],  # not bare "rest"
    "JWT Authentication": ["jwt"],
    "Shopify Liquid": ["liquid", "shopify liquid"],
    "Mongoose": ["mongoose"],
    "Docker": ["docker", "containerization"],
    "Git": ["git", "version control"],
    "GitHub": ["github"],
    "Stripe": ["stripe"],
    
    
    "Jest": ["jest"],
    "Unit Testing": ["unit testing", "unit tests", "testing"],
    "CI/CD": ["ci/cd", "cicd", "ci cd"],
    "Microservices": ["microservices", "microservice"],
    "Agile": ["agile"],
    "Scrum": ["scrum"],
    "Responsive Design": ["responsive design", "responsive web", "responsive ui", "responsive layouts", "mobile-first"],
    "Webhooks": ["webhook", "webhooks"],
    "Prisma": ["prisma"],
    "Remix": ["remix"],
    "Polaris": ["polaris"],
    "Shopify Apps": ["shopify app", "shopify apps"],
    "Linux": ["linux"],
    "Nginx": ["nginx"],
    "SSR": ["ssr", "server side rendering", "server-side rendering"],
    "OAuth": ["oauth", "oauth2"],
    "RBAC": ["rbac", "role based access", "role-based access"],
}


def _pat(term: str) -> re.Pattern:
    return re.compile(r"(?<![\w.+#])" + re.escape(term.lower()) + r"(?![\w+#])|(?<![\w.+#])" +
                      re.escape(term.lower()) + r"(?=\.(?:\s|$))", re.I)


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9+#]", "", s.lower())


_CACHE: dict = {}


def _vocab():
    if _CACHE:
        return _CACHE
    sk = load_skills()
    core, adj, blocked, soft = flat(sk["core"]), flat(sk["adjacent"]), sk["blocked"], sk.get("soft_blocked", [])
    spell: dict[str, list[str]] = {}
    for s in core + adj:
        spell.setdefault(s, [s.lower()])
        spell[s] = list({*spell[s], *SYNONYMS.get(s, [])})
    for s, syn in SYNONYMS.items():
        spell.setdefault(s, syn)
    _CACHE.update(core=set(core), adj=set(adj), blocked=blocked,
                  pats={c: [_pat(t) for t in terms] for c, terms in spell.items()},
                  bpats={b: _pat(b) for b in blocked},          # HARD: skip / cost fit points
                  spats={b: _pat(b) for b in soft})             # SOFT (cloud tools): reported, never penalised
    return _CACHE


def extract_skills(text: str) -> dict:
    """Return {'core': [...], 'adjacent': [...], 'core_hits': int, 'blocked': {term: count}, 'soft': [...]} found in text.
    core_hits = how many times core skills are mentioned (same unit as the blocked counts, for a like-for-like comparison)."""
    v = _vocab()
    low = text or ""
    core, adj, core_hits = [], [], 0
    for canon, pats in v["pats"].items():
        n = max(len(p.findall(low)) for p in pats)       # max, not sum: "react" and "react.js" overlap on the same words
        if n:
            if canon in v["core"]:
                core.append(canon)
                core_hits += n
            elif canon in v["adj"]:
                adj.append(canon)
    blocked = {}
    for b, p in v["bpats"].items():
        n = len(p.findall(low))
        if n:
            blocked[b] = n
    soft = [b for b, p in v["spats"].items() if p.search(low)]
    return {"core": core, "adjacent": adj, "core_hits": core_hits, "blocked": blocked, "soft": soft}


def is_blocked_stack(title: str, description: str) -> tuple[bool, str]:
    """Skip jobs whose MAIN stack is something we do not have (e.g. 'Django Developer')."""
    v = _vocab()
    for b, p in v["bpats"].items():
        if p.search(title or ""):
            return True, f"title mentions blocked stack: {b}"
    found = extract_skills(f"{title}\n{description}")
    bsum = sum(found["blocked"].values())           # mentions of foreign stacks ...
    csum = found["core_hits"]                       # ... against mentions of our stack (was: distinct skills - not comparable)
    if bsum >= 4 and bsum > csum:
        top = max(found["blocked"], key=found["blocked"].get)
        return True, f"main stack looks like {top}"
    return False, ""


EXP_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:-|to|–)\s*(\d+(?:\.\d+)?)\s*(?:yrs?|years?)", re.I)
EXP_MIN_RE = re.compile(r"(\d+(?:\.\d+)?)\s*\+?\s*(?:yrs?|years?)", re.I)


def parse_experience(text: str) -> tuple[float | None, float | None]:
    if not text:
        return None, None
    m = EXP_RE.search(text)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = EXP_MIN_RE.search(text)
    if m:
        return float(m.group(1)), None
    return None, None
