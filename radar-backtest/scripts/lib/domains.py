"""Domain normalisation, platform blocklist, GitHub login candidates, ccTLD country.

Deterministic: tldextract uses its bundled public-suffix snapshot (no network fetch).
"""
from __future__ import annotations

import re
import unicodedata

import tldextract

_EXTRACT = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)

# Hosting / social / code platforms: a website on these is not a company domain.
PLATFORM_DOMAINS = {
    "github.com", "github.io", "githubusercontent.com", "gitlab.com", "gitlab.io", "bitbucket.org", "sourceforge.net",
    "medium.com", "substack.com", "blogspot.com", "wordpress.com", "wordpress.org", "tumblr.com", "ghost.io",
    "herokuapp.com", "vercel.app", "netlify.app", "netlify.com", "pages.dev", "firebaseapp.com", "web.app",
    "azurewebsites.net", "appspot.com", "cloudfront.net", "amazonaws.com", "fly.dev", "render.com", "glitch.me",
    "readthedocs.io", "readthedocs.org", "gitbook.io", "notion.site", "notion.so", "webflow.io", "wixsite.com",
    "squarespace.com", "carrd.co", "strikingly.com", "weebly.com", "about.me", "linktr.ee", "bio.link",
    "linkedin.com", "twitter.com", "x.com", "facebook.com", "instagram.com", "youtube.com", "youtu.be",
    "google.com", "goo.gl", "apple.com", "microsoft.com", "discord.gg", "discord.com", "slack.com", "t.me",
    "telegram.org", "reddit.com", "keybase.io", "gravatar.com", "bit.ly", "npmjs.com", "pypi.org", "docker.com",
    "crunchbase.com", "angel.co", "wellfound.com", "producthunt.com", "ycombinator.com", "launchpad.net",
    "gitee.com", "codeberg.org", "huggingface.co", "kaggle.com", "itch.io", "patreon.com", "opencollective.com",
    "sites.google.com", "dev.to", "hashnode.dev", "hashnode.com", "mozilla.org", "apache.org", "eclipse.org",
}
# Generic TLDs carry no country information.
_CC_OVERRIDES = {"uk": "GB", "eu": None, "io": None, "co": None, "ai": None, "me": None, "tv": None, "ly": None,
                 "gg": None, "sh": None, "so": None, "fm": None, "am": None, "to": None, "cc": None, "ws": None,
                 "la": None, "is": None, "vc": None, "app": None, "dev": None, "xyz": None, "tech": None}


def _host(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"^[a-z][a-z0-9+.-]*://", "", s)
    s = s.split("/")[0].split("?")[0].split("#")[0]
    s = s.split("@")[-1].split(":")[0].strip(".")
    return s


def registrable_domain(url_or_host: str | None) -> str | None:
    """'https://www.Acme.co.uk/about' -> 'acme.co.uk'. Returns None if not a valid registrable domain."""
    if not url_or_host or not isinstance(url_or_host, str):
        return None
    host = _host(url_or_host)
    if not host or " " in host:
        return None
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    ext = _EXTRACT(host)
    if not ext.domain or not ext.suffix:
        return None
    return f"{ext.domain}.{ext.suffix}"


def is_platform(domain: str | None) -> bool:
    return domain is None or domain in PLATFORM_DOMAINS


def company_domain(url_or_host: str | None) -> str | None:
    """Registrable domain, or None when missing or a platform domain."""
    d = registrable_domain(url_or_host)
    return None if is_platform(d) else d


def domain_stem(domain: str) -> str:
    return _EXTRACT(domain).domain.lower()


def tld(domain: str) -> str:
    return _EXTRACT(domain).suffix.split(".")[-1].lower()


def country_from_domain(domain: str | None) -> str | None:
    """ISO2 country from a ccTLD ('acme.de' -> 'DE', 'acme.co.uk' -> 'GB'); None for generic TLDs."""
    if not domain:
        return None
    t = tld(domain)
    if t in _CC_OVERRIDES:
        return _CC_OVERRIDES[t]
    if len(t) == 2 and t.isalpha():
        return t.upper()
    return None


_LEGAL = r"\b(inc|incorporated|corp|corporation|co|company|llc|ltd|limited|plc|gmbh|ug|ag|kg|se|sa|sas|sarl|srl|spa|bv|nv|oy|ab|as|aps|pty|sl|kft|sp z o o|haftungsbeschrankt)\b"


def name_slug(name: str | None) -> str:
    """'Acme Robotics, Inc.' -> 'acme-robotics' (ASCII, lowercase, legal suffixes removed)."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[()\[\],.]", " ", s)
    s = re.sub(_LEGAL, " ", s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


_PREFIXES = ["get", "use", "try", "join", "hello", "go"]
_SUFFIXES = ["hq", "app", "io", "labs", "lab", "inc", "dev", "ai", "team", "tech", "co", "oss", "org", "software", "eng"]
_LOGIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9])){0,38}$")


def login_candidates(stem: str | None, name: str | None = None) -> list[str]:
    """Fixed, deterministic set of GitHub org logins that can belong to a company.

    Same rule for every entity (positives and controls). No fuzzy matching.
    """
    bases = set()
    for b in (stem or "", name_slug(name)):
        b = b.lower().strip("-")
        if b:
            bases.add(b)
            bases.add(b.replace("-", ""))
    out = set()
    for b in bases:
        out.add(b)
        for s in _SUFFIXES:
            out.add(b + s)
            out.add(f"{b}-{s}")
        for p in _PREFIXES:
            out.add(p + b)
            out.add(f"{p}-{b}")
    return sorted(c for c in out if _LOGIN_RE.match(c))


def login_rule(login: str, website_url: str | None, entity_domain: str, display_name: str | None = None,
               extra_names: tuple = ()) -> bool:
    """Accept an org for an entity iff websiteUrl domain == entity domain AND login is a candidate."""
    if not login or company_domain(website_url) != entity_domain:
        return False
    stem = domain_stem(entity_domain)
    cands = set(login_candidates(stem, display_name))
    for n in extra_names:
        cands.update(login_candidates(None, n))
    return login.lower() in cands
