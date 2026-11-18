from pathlib import Path
import os
import yaml

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
RESUMES = OUT / "resumes"
LOGS = ROOT / "logs"
DB_PATH = ROOT / "applier.db"


def load_env(path: Path | None = None) -> None:
    """Minimal .env reader: KEY=VALUE lines -> os.environ (real environment variables win)."""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        v = v.strip().strip("'\"")
        if v and not os.environ.get(k.strip()):
            os.environ[k.strip()] = v


load_env()


def ai_settings(cfg: dict | None = None) -> dict:
    """Which AI writes the resume text. AI_PROVIDER in .env: gemini | anthropic | none.
    If AI_PROVIDER is empty: anthropic if its key is set, else gemini if its key is set, else none."""
    cfg = cfg or load_config()
    env = os.environ.get
    keys = {"anthropic": bool(env("ANTHROPIC_API_KEY")), "gemini": bool(env("GEMINI_API_KEY") or env("GOOGLE_API_KEY"))}
    prov = (env("AI_PROVIDER") or "").strip().lower()
    if prov not in ("gemini", "anthropic", "none"):
        prov = "anthropic" if keys["anthropic"] else "gemini" if keys["gemini"] else "none"
    if prov == "anthropic":
        model, effort = env("ANTHROPIC_MODEL") or cfg.get("model", "claude-opus-5-5"), env("ANTHROPIC_EFFORT") or "low"
    elif prov == "gemini":
        model, effort = env("GEMINI_MODEL") or "gemini-flash-latest", env("GEMINI_THINKING") or "default"
    else:
        model, effort = "-", "-"
    return {"provider": prov, "model": model, "effort": effort, "has_key": keys.get(prov, False)}


claude_settings = ai_settings       # old name, kept so nothing breaks


def load_config() -> dict:
    p = ROOT / "config.yaml"
    if not p.exists():
        raise SystemExit("config.yaml not found. Run: cp config.example.yaml config.yaml  (then fill in the profile block)")
    return yaml.safe_load(p.read_text())


def load_skills() -> dict:
    return yaml.safe_load((ROOT / "skills_bank.yaml").read_text())


def flat(d: dict) -> list[str]:
    return [s for v in d.values() for s in v]
