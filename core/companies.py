"""One company = one application, ever. Names differ between sites ('Pvt Ltd', URL-encoded, casing), so compare normalised."""
import re
import urllib.parse

_SUFFIX = {"pvt", "ltd", "private", "limited", "llp", "inc", "india", "opc", "co", "corp", "corporation"}


def norm_company(name: str) -> str:
    s = urllib.parse.unquote(name or "").lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return "".join(w for w in s.split() if w not in _SUFFIX)
