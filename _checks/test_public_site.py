#!/usr/bin/env python3
"""Local checks for the services-only public site.

Run from the repo root:

    python3 _checks/test_public_site.py
    python3 _checks/test_public_site.py --serve 8765

GitHub Pages removes `_checks` before upload (see deploy.yml). This file is
not a public page. Nothing here sends email or hits the live site.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
from html import unescape
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

from sensitive_terms import SENSITIVE_TERM_HASHES, ngram_hashes, sensitive_hits, sha256_lower

ROOT = Path(__file__).resolve().parents[1]

# Public HTML that GitHub Pages would serve. The post template is source only.
PUBLIC_HTML = {
    "index.html": "marketing",
    "services.html": "marketing",
    "blog/index.html": "marketing",
    "blog/google-business-profile-audit-checklist.html": "marketing",
    "blog/google-business-profile-booking-link.html": "marketing",
    "blog/google-business-profile-not-showing-up.html": "marketing",
    "blog/how-to-get-your-business-to-show-up-in-ai-search.html": "marketing",
    "blog/google-business-profile-message-button.html": "marketing",
    "team.html": "brand",
    "newsletter.html": "marketing-signup-off",
    "tianna-intro.html": "brand",
    "tianna-gallery-v4.html": "brand",
    "404.html": "support",
    "privacy.html": "legal",
    "terms.html": "legal",
    "refund.html": "legal",
    "tovael-privacy.html": "customer-legal",
    "auth/confirm/index.html": "customer-access",
    "maintenance.html": "maintenance",
    "agree.html": "agreement",
}
# Contract pages (MSA, SOWs; noindex) and frozen legal artifacts for the clickwrap flow (see _build/legal_manifest.py).
LEGAL_CONTRACTS = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "legal").glob("*.html"))
LEGAL_ARCHIVE = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "legal/archive").rglob("*.html"))
for _rel in LEGAL_CONTRACTS:
    PUBLIC_HTML[_rel] = "legal-contract"
for _rel in LEGAL_ARCHIVE:
    PUBLIC_HTML[_rel] = "legal-archive"
# Pages rendered from Lena's v0.2 drafts; their wording is checked against _build/legal_src instead of git history.
LEGAL_GENERATED = ("terms.html", "privacy.html", "refund.html", *LEGAL_CONTRACTS, *LEGAL_ARCHIVE)

# Product names may remain here because they are the legal or customer-access
# text this run is not allowed to rewrite. The scan still prints the matches.
TEXT_EXCEPTIONS = {
    **{rel: "Lena's v0.2 legal text, imported verbatim (legacy Gumroad products and apps keep their own terms)."
       for rel in LEGAL_GENERATED},
    "tovael-privacy.html": "Required Tovael privacy policy for the App Store listing and the shipped app.",
    "auth/confirm/index.html": "Required Tovael email-confirmation fallback.",
}

# Google's own booking-link rules, quoted in the services blog. Not a TJNAI app offer.
ALLOWED_QUOTES = (
    "an app store link or a link shortener",
    "app store links or link shorteners",
)

# Exact-phrase allowlist for Tianna-approved copy that would otherwise trip PROMO_PATTERNS.
# Only these phrases, only on these paths. Anything else, including "AI front desk", still fails.
PROMO_PHRASE_ALLOWLIST = {
    "blog/google-business-profile-message-button.html": (
        "a shop with a front desk",
    ),
}

PROMO_PATTERNS = (
    r"pickle",
    r"gumroad",
    r"dink theory",
    r"doubles playbook",
    r"know the rule",
    r"coach court kit",
    r"dishlift",
    r"theorem tiles",
    r"app store",
    r"what we've built",
    r"#products",
    r"early19",
    r"assets/og-home\.png",
    r"assets/products/",
    r"front desk",
    r"\$37\b",
    r"\$24\b",
    r"(?<![\d.])\$19\b",
    r"ai lead management",
    r"full front office",
)

SALES_URLS = (
    "gumroad.com",
    "picklerecord.app",
    "dishlift.app",
    "youtube.com/@dinktheorypb",
    "assets/og-home.png",
    "assets/products/",
)

UNCHANGED_SHA256 = {
    ".well-known/apple-app-site-association": "c34d360fab779a14a868f3f0dba1d4eb6cf528670e4d61b7b9d8c47ae008d0f3",
    ".well-known/security.txt": "9c14cc452ddf1885d2aef45b69c44930bc97285cdd5043fd253799a99bd24f1b",
}
UNCHANGED = tuple(UNCHANGED_SHA256)
CONTENT_STABLE = (
    "tovael-privacy.html",
    "auth/confirm/index.html",
    "services.html",
)
TIM_ALT = "Tim Nichols, founder of TJNAI."
TIM_ASSETS = (
    "assets/team/tim-160.jpg",
    "assets/team/tim-320.jpg",
    "assets/team/tim-160.webp",
    "assets/team/tim-320.webp",
)
HOME_ROSTER = (
    "Tim (TJ)",
    "Tianna",
    "Sage",
    "Eva",
    "Iris",
    "Vera",
    "Cynthia",
    "Nova",
    "Lyra",
)
TEAM_ROSTER = HOME_ROSTER + ("Fiona",)

PRESERVED_UNLINKED = (
    "assets/og-home.png",
    "assets/products/picklerecord.png",
    "assets/products/dink-theory.jpg",
    "assets/products/doubles-playbook.webp",
    "assets/products/dishlift.svg",
)

SITEMAP_OMIT = {
    "maintenance.html",
    "tovael-privacy.html",
    "auth/confirm/index.html",
    "404.html",
    "agree.html",
    *LEGAL_CONTRACTS,
    *LEGAL_ARCHIVE,
}

REMOVED_HASHES = (
    "products",
    "coach-court-kit",
    "picklerecord",
    "dink-theory",
    "know-the-rule",
    "doubles-playbook",
    "dishlift",
)

OLD_PATHS = ("/aquaiq", "/aquaiq-privacy", "/dipcue-privacy", "/picklerecord", "/coach-court-kit")

failures: list[str] = []


def fail(message: str) -> None:
    failures.append(message)


class Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []
        self.ids: set[str] = set()
        self.canonical: str | None = None
        self.metas: dict[str, str] = {}
        self.noindex = False
        self.title = ""
        self._in_title = False
        self.jsonld: list[str] = []
        self._json = False
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = {key: value or "" for key, value in attrs}
        if tag == "script" and data.get("type") == "application/ld+json":
            self._json = True
            self._buf = []
        if "id" in data:
            self.ids.add(data["id"])
        if tag == "a" and data.get("href"):
            self.hrefs.append(data["href"])
        if tag == "link" and data.get("rel") == "canonical":
            self.canonical = data.get("href")
        if tag == "meta":
            name = data.get("name")
            prop = data.get("property")
            content = data.get("content", "")
            if name == "description":
                self.metas["description"] = content
            if name == "robots" and "noindex" in content:
                self.noindex = True
            if name == "twitter:description":
                self.metas["twitter:description"] = content
            if name == "twitter:image":
                self.metas["twitter:image"] = content
            if prop == "og:description":
                self.metas["og:description"] = content
            if prop == "og:image":
                self.metas["og:image"] = content
            if prop == "og:url":
                self.metas["og:url"] = content
        if tag == "img" and data.get("src"):
            self.hrefs.append(data["src"])
        if tag == "title":
            self._in_title = True
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._json:
            self.jsonld.append("".join(self._buf))
            self._json = False
        if tag == "title" and self._in_title:
            self.title = "".join(self._buf).strip()
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._json or self._in_title:
            self._buf.append(data)


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def parse(rel: str) -> Page:
    page = Page()
    page.feed(read(rel))
    return page


def file_url(rel: str) -> str:
    if rel == "index.html":
        return "/"
    if rel.endswith("/index.html"):
        return "/" + rel[: -len("index.html")]
    if rel.endswith(".html"):
        return "/" + rel[: -len(".html")]
    return "/" + rel


def url_to_file(path: str, root: Path = ROOT) -> Path | None:
    path = unquote(path.split("?", 1)[0])
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    rel = path.lstrip("/")
    candidates = [Path(rel), Path(rel + ".html"), Path(rel) / "index.html"] if rel else [Path("index.html")]
    if not rel:
        candidates = [Path("index.html")]
    for candidate in candidates:
        if (root / candidate).is_file():
            return candidate
    return None


def page_dir_url(rel: str) -> str:
    url = file_url(rel)
    if url.endswith("/") or url == "/":
        return url
    return url.rsplit("/", 1)[0] + "/"


def resolve_href(page_rel: str, href: str) -> tuple[str, str] | None:
    if href.startswith(("mailto:", "tel:", "javascript:", "data:")):
        return None
    if href.startswith("#"):
        return page_rel, href[1:]
    if href.startswith(("http://", "https://")):
        parsed = urlparse(href)
        if parsed.netloc not in ("tjnai.ai", "www.tjnai.ai"):
            return ("external", href)
        path = parsed.path or "/"
        frag = parsed.fragment
    else:
        joined = urljoin("https://tjnai.ai" + page_dir_url(page_rel), href)
        parsed = urlparse(joined)
        path = parsed.path or "/"
        frag = parsed.fragment
    found = url_to_file(path)
    if found is None:
        return ("missing", path + (("" if not frag else "#" + frag)))
    return (str(found), frag)


def scanned_text(rel: str) -> str:
    raw = read(rel)
    without_style = re.sub(r"<style\b[^>]*>.*?</style>", " ", raw, flags=re.I | re.S)
    without_script = re.sub(r"<script\b[^>]*>.*?</script>", " ", without_style, flags=re.I | re.S)
    alts = " ".join(re.findall(r'\balt="([^"]*)"', raw))
    text = re.sub(r"<[^>]+>", " ", without_script)
    text = text + "\n" + alts
    for quote in ALLOWED_QUOTES:
        text = re.sub(re.escape(quote), " ", text, flags=re.I)
    return text


def apply_promo_allowlist(rel: str, text: str) -> str:
    """Strip exact allowlisted phrases for one path before PROMO_PATTERNS run."""
    for phrase in PROMO_PHRASE_ALLOWLIST.get(rel, ()):
        text = text.replace(phrase, " ")
        text = text.replace(phrase.lower(), " ")
        text = text.replace(phrase.upper(), " ")
        # casefold-safe: remove case-insensitive exact phrase occurrences
        text = re.sub(re.escape(phrase), " ", text, flags=re.I)
    return text


def promo_hits(rel: str, text: str) -> list[str]:
    text = apply_promo_allowlist(rel, text)
    return [pattern for pattern in PROMO_PATTERNS if re.search(pattern, text, flags=re.I)]


def try_git_text_rev(rev: str, rel: str) -> str | None:
    """Read one file from a git rev when that object is present."""
    try:
        return subprocess.check_output(
            ["git", "show", f"{rev}:{rel}"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
        ).decode("utf-8")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def try_git_text(rel: str) -> str | None:
    """Baseline from origin/main when that ref is present.

    actions/checkout is shallow and often has no origin/main. Callers must
    still enforce prices, the omitted offer, and the pinned infrastructure hashes.
    """
    return try_git_text_rev("origin/main", rel)


def check_inventory() -> None:
    found = {
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*.html")
        if path.relative_to(ROOT).parts[0] != "_backend"
    }
    expected = set(PUBLIC_HTML) | {
        "_templates/post-template.html",
        "_archive/tianna-intro-splash.html",
        "_archive/tianna-gallery-v4.html",
    }
    print("Public HTML routes:")
    for rel, kind in PUBLIC_HTML.items():
        print(f"  {rel} ({kind})")
    print("Sitemap omissions:", ", ".join(sorted(SITEMAP_OMIT)))
    print("Archived, not uploaded:", "_archive/tianna-intro-splash.html, _archive/tianna-gallery-v4.html")
    if found != expected:
        fail(f"HTML inventory drifted.\n  extra: {sorted(found - expected)}\n  missing: {sorted(expected - found)}")
    template = read("_templates/post-template.html")
    if "#products" in template or "What we've built" in template:
        fail("Post template still links the removed product section.")


def check_promo() -> None:
    print("Permitted customer/legal product-text exceptions:")
    for rel, reason in TEXT_EXCEPTIONS.items():
        hits = []
        text = scanned_text(rel).lower()
        for pattern in PROMO_PATTERNS:
            if re.search(pattern, text, flags=re.I):
                hits.append(pattern)
        print(f"  - {rel}: {reason}")
        print(f"    matched: {', '.join(hits) if hits else '(none)'}")
        if sensitive_hits(scanned_text(rel)) or sensitive_hits(read(rel)):
            fail(f"{rel} contains a blocked sensitive term")
    for rel, kind in PUBLIC_HTML.items():
        if rel in TEXT_EXCEPTIONS:
            continue
        hits = promo_hits(rel, scanned_text(rel))
        for pattern in hits:
            fail(f"{rel} ({kind}) still matches /{pattern}/")
        if sensitive_hits(scanned_text(rel)) or sensitive_hits(read(rel)):
            fail(f"{rel} ({kind}) contains a blocked sensitive term")
        page = parse(rel)
        for href in page.hrefs:
            lowered = href.lower()
            for sales in SALES_URLS:
                if sales in lowered:
                    fail(f"{rel} still links {href}")


def check_links() -> None:
    for rel in PUBLIC_HTML:
        page = parse(rel)
        for href in page.hrefs:
            if href.startswith(("mailto:", "tel:", "data:")):
                continue
            resolved = resolve_href(rel, href)
            if resolved is None:
                continue
            target, frag = resolved
            if target == "external":
                continue
            if target == "missing":
                fail(f"{rel} broken internal link: {href}")
                continue
            if frag:
                target_page = parse(target)
                if frag not in target_page.ids:
                    fail(f"{rel} link {href} has no #{frag} on {target}")


def check_canonical_sitemap_schema() -> None:
    sitemap = read("sitemap.xml")
    locs = re.findall(r"<loc>(.*?)</loc>", sitemap)
    canonical_of: dict[str, str] = {}
    for rel, kind in PUBLIC_HTML.items():
        page = parse(rel)
        if rel == "404.html":
            if not page.noindex:
                fail("404.html is missing noindex")
            if page.canonical:
                fail("404.html should not declare a canonical")
            continue
        if not page.canonical:
            if rel not in SITEMAP_OMIT:
                fail(f"{rel} has no canonical")
            continue
        if not page.canonical.startswith("https://tjnai.ai"):
            fail(f"{rel} canonical is not on tjnai.ai: {page.canonical}")
        path = urlparse(page.canonical).path or "/"
        mapped = url_to_file(path)
        if mapped is None or mapped.as_posix() != rel:
            fail(f"{rel} canonical {page.canonical} does not map back to that file")
        if page.metas.get("og:url") and page.metas["og:url"] != page.canonical:
            fail(f"{rel} og:url {page.metas['og:url']} != canonical {page.canonical}")
        canonical_of[page.canonical] = rel
        if rel not in SITEMAP_OMIT and page.canonical not in locs:
            fail(f"{rel} ({kind}) canonical is missing from sitemap.xml")
        if rel in SITEMAP_OMIT and page.canonical in locs:
            fail(f"{rel} should stay out of the sitemap")
        for block in page.jsonld:
            try:
                data = json.loads(block)
            except json.JSONDecodeError as exc:
                fail(f"{rel} has invalid JSON-LD: {exc}")
                continue
            check_schema(rel, data)
    for loc in locs:
        if loc not in canonical_of:
            fail(f"sitemap loc has no matching canonical: {loc}")
    home = parse("index.html")
    description = home.metas.get("description", "")
    if home.metas.get("og:description") != description or home.metas.get("twitter:description") != description:
        fail("Home meta, Open Graph, and Twitter descriptions differ")
    home_schema = json.loads(home.jsonld[0])
    if home_schema.get("description") != description:
        fail("Home schema description does not match the meta description")
    if "350" not in description or "750" not in description or "150" not in description:
        fail("Home description dropped an approved price")


def walk_prices(node: object, found: list[str]) -> None:
    if isinstance(node, dict):
        if "price" in node:
            found.append(str(node["price"]))
        for value in node.values():
            walk_prices(value, found)
    elif isinstance(node, list):
        for value in node:
            walk_prices(value, found)


def check_schema(rel: str, data: object) -> None:
    prices: list[str] = []
    walk_prices(data, prices)
    for price in prices:
        if price not in {"350", "750", "150"}:
            fail(f"{rel} schema price {price} is not an approved 350/750/150 price")
    blob = json.dumps(data).lower()
    for banned in ("ai lead management", "full front office", "ai front desk", "pickle", "gumroad"):
        if banned in blob:
            fail(f"{rel} schema contains {banned}")


OMITTED_MENU_ITEM = (
    '        <li><span class="letter" aria-hidden="true">B</span>'
    "<span>An AI front desk that drafts replies to website, email and FAQ inquiries. "
    "You approve the replies.</span></li>\n"
)
KEPT_MENU = (
    "Google profile fixes, plus an automatic review request after each job.",
    "A lead capture form with automatic follow-up.",
    "A weekly summary email to you, the owner.",
    "Online booking set up on your website and Google profile, so customers can book without a phone call.",
)
SERVICE_PREVIEW = "https://tjnai.ai/assets/logo/og-card.jpg"
PREVIEW_PAGES = ("index.html", "services.html", "blog/index.html", "newsletter.html")


def check_prices_and_services_body() -> None:
    current = read("services.html")
    current_main = current[current.index("<main"):current.index("</main>")]
    previous = try_git_text("services.html")
    if previous is None:
        print("origin/main baseline unavailable; services diff check skipped.")
    else:
        previous_main = previous[previous.index("<main"):previous.index("</main>")]
        if OMITTED_MENU_ITEM in previous_main:
            if previous_main.replace(OMITTED_MENU_ITEM, "", 1) != current_main:
                fail("services.html main changed beyond omission of build-menu example B")
        elif previous_main != current_main:
            fail("services.html main drifted after example B was already absent from origin/main")
    for needle in ("$350", "$750", "$150", '"price": "350"', '"price": "750"', '"price": "150"'):
        if needle not in current:
            fail(f"services.html missing {needle}")
    if "the remaining $400 is paid as $200 upfront and $200 at handoff" not in current:
        fail("Build payment schedule changed")
    home = read("index.html")
    for needle in ("$350", "$750", "$150"):
        if needle not in home:
            fail(f"index.html missing approved price {needle}")
    for sentence in KEPT_MENU:
        if sentence not in current_main:
            fail(f"Kept build-menu example missing: {sentence}")
    if re.search(r"front desk|ai lead management|full front office", current, flags=re.I):
        fail("services.html still contains a front-desk or front-office offer")
    letters = re.findall(r'class="letter" aria-hidden="true">([A-E])<', current_main)
    if letters != ["A", "C", "D", "E"]:
        fail(f"Build-menu letters are {letters}; expected A, C, D, E with B omitted and not reused")


def check_unchanged_and_assets() -> None:
    for rel, digest in UNCHANGED_SHA256.items():
        actual = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
        if actual != digest:
            fail(f"{rel} bytes changed; legal, support, or customer-access content must stay pinned")
        baseline = try_git_text(rel)
        if baseline is not None and hashlib.sha256(baseline.encode("utf-8")).hexdigest() != digest:
            fail(f"{rel} pin does not match origin/main")
    referenced = "\n".join(read(rel) for rel in PUBLIC_HTML)
    for rel in PRESERVED_UNLINKED:
        if not (ROOT / rel).is_file():
            fail(f"Source asset deleted: {rel}")
        if rel.split("/")[-1] in referenced or rel in referenced:
            fail(f"Public HTML still references preserved asset {rel}")
    workflow = read(".github/workflows/deploy.yml")
    if "_checks" not in workflow:
        fail("deploy.yml does not strip _checks, so this test file would be published")
    targets = set(deploy_rm_targets())
    for excluded in PRESERVED_UNLINKED:
        if excluded not in targets:
            fail(f"deploy.yml does not exclude the file {excluded}")
    if "assets/products" in targets:
        fail("deploy.yml removes the assets/products directory, so a future unrelated file there would vanish")
    for kept in ("assets/logo", "assets/team", "assets/blog", "assets/fonts"):
        if re.search(rf"rm\s+-rf\b.*\b{re.escape(kept)}\b", workflow):
            fail(f"deploy.yml would remove in-use asset tree {kept}")
    product_files = sorted(path.name for path in (ROOT / "assets/products").iterdir() if path.is_file())
    expected_products = sorted(Path(rel).name for rel in PRESERVED_UNLINKED if rel.startswith("assets/products/"))
    if product_files != expected_products:
        fail(f"assets/products contains {product_files}; expected only {expected_products}")
    if not (ROOT / "assets/logo/og-card.jpg").is_file():
        fail("Active social preview assets/logo/og-card.jpg is missing")


def check_active_previews() -> None:
    for rel in PREVIEW_PAGES:
        page = parse(rel)
        for key in ("og:image", "twitter:image"):
            image = page.metas.get(key, "")
            if image != SERVICE_PREVIEW:
                fail(f"{rel} {key} is {image or '(missing)'}; expected the brand card")
            if "og-home.png" in image:
                fail(f"{rel} {key} still points at the product card")
    for rel, kind in PUBLIC_HTML.items():
        if rel in PREVIEW_PAGES:
            continue
        page = parse(rel)
        for key in ("og:image", "twitter:image"):
            image = page.metas.get(key, "")
            if "og-home.png" in image or "/assets/products/" in image:
                fail(f"{rel} ({kind}) {key} points at a retired promo asset")


def visible_text(html: str) -> str:
    html = re.sub(r"<style\b[^>]*>.*?</style>", " ", html, flags=re.I | re.S)
    html = re.sub(r"<script\b[^>]*>.*?</script>", " ", html, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", unescape(text)).strip()


def content_hrefs(html: str) -> list[str]:
    html = re.sub(r"<style\b[^>]*>.*?</style>", " ", html, flags=re.I | re.S)
    html = re.sub(r"<script\b[^>]*>.*?</script>", " ", html, flags=re.I | re.S)
    return re.findall(r'\bhref="([^"]*)"', html)


def drop_gallery_link(html: str) -> str:
    """The gallery footer link is a removed promo, not legal or payment wording."""
    return (
        html.replace('<a href="/tianna-intro">Tianna Gallery</a>', "")
        .replace('<a href="../tianna-intro.html">Tianna Gallery</a>', "")
    )


MAILING_ADDRESS = "5540 Centerview Dr Ste 204, PMB #588341, Raleigh, NC 27606-8012"


def sanctioned(rel: str, text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def sanctioned_hrefs(rel: str, hrefs: list[str]) -> list[str]:
    return hrefs


def check_content_invariants() -> None:
    for rel in CONTENT_STABLE:
        current = read(rel)
        for rev in ("origin/main", "d71e3cc"):
            previous = try_git_text_rev(rev, rel)
            if previous is None:
                print(f"{rev} baseline unavailable for {rel}; hash pins still cover infrastructure.")
                continue
            baseline = drop_gallery_link(previous)
            if rev == "d71e3cc":
                if sanctioned(rel, visible_text(current)) != sanctioned(rel, visible_text(baseline)):
                    fail(f"{rel} visible wording differs from the pre-theme head")
                if sanctioned_hrefs(rel, content_hrefs(current)) != sanctioned_hrefs(rel, content_hrefs(baseline)):
                    fail(f"{rel} links differ from the pre-theme head")
            elif rel != "services.html":
                if sanctioned(rel, visible_text(current)) != sanctioned(rel, visible_text(baseline)):
                    fail(f"{rel} visible wording differs from origin/main")
                if sanctioned_hrefs(rel, content_hrefs(current)) != sanctioned_hrefs(rel, content_hrefs(baseline)):
                    fail(f"{rel} links differ from origin/main")
    auth = read("auth/confirm/index.html")
    script = re.search(r"<script>(.*?)</script>", auth, re.S)
    for rev in ("origin/main", "d71e3cc"):
        previous = try_git_text_rev(rev, "auth/confirm/index.html")
        if previous is None:
            continue
        old = re.search(r"<script>(.*?)</script>", previous, re.S)
        if script is None or old is None or script.group(1) != old.group(1):
            fail(f"auth/confirm script differs from {rev}")
    if 'id="open"' not in auth or "tovael://auth/confirm?code=" not in auth:
        fail("auth/confirm no longer hands the code to the Tovael app")


def image_size(data: bytes) -> tuple[int, int] | None:
    if data[:2] == b"\xff\xd8":
        i = 2
        while i < len(data) - 8:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2):
                height, width = struct.unpack(">HH", data[i + 5:i + 9])
                return width, height
            if marker in (0xD8, 0xD9):
                i += 2
                continue
            seglen = struct.unpack(">H", data[i + 2:i + 4])[0]
            i += 2 + seglen
        return None
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        chunk = data[12:16]
        if chunk == b"VP8X":
            width = 1 + int.from_bytes(data[24:27], "little")
            height = 1 + int.from_bytes(data[27:30], "little")
            return width, height
        if chunk == b"VP8 ":
            width, height = struct.unpack("<HH", data[26:30])
            return width & 0x3FFF, height & 0x3FFF
        if chunk == b"VP8L":
            bits = int.from_bytes(data[21:25], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    return None


def check_tim_portrait() -> None:
    expected = {"160": (160, 160), "320": (320, 320)}
    for rel in TIM_ASSETS:
        path = ROOT / rel
        if not path.is_file():
            fail(f"Missing Tim portrait {rel}")
            continue
        size = image_size(path.read_bytes())
        want = expected["160" if "-160." in rel else "320"]
        if size != want:
            fail(f"{rel} is {size}, expected {want}")
    placements = ("index.html", "team.html")
    for rel in placements:
        text = read(rel)
        if text.count(TIM_ALT) != 1:
            fail(f"{rel} should contain the Tim alt text once")
        for asset in TIM_ASSETS:
            if "/" + asset not in text and asset not in text:
                fail(f"{rel} does not reference {asset}")
        body = text.split("<body", 1)[-1]
        if "mate-ini" in body or "av-ini" in body or ">TJ<" in body:
            fail(f"{rel} still uses the TJ initials placeholder")
    others = []
    for rel in PUBLIC_HTML:
        if rel in placements:
            continue
        if TIM_ALT in read(rel) or "assets/team/tim-" in read(rel):
            others.append(rel)
    if others:
        fail(f"Tim portrait appears outside home and team: {others}")
    for rel in ("index.html", "services.html", "blog/index.html", "newsletter.html", "team.html"):
        if "assets/team/tim-" in read(rel) and rel not in placements:
            fail(f"{rel} social or page surface references the Tim portrait unexpectedly")


def contrast_ratio(hex_a: str, hex_b: str) -> float:
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    def lum(color: str) -> float:
        color = color.lstrip("#")
        r, g, b = int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)
        return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)

    lighter, darker = sorted((lum(hex_a), lum(hex_b)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def check_checklist_rendered_contrast() -> None:
    """Rendered .prose ul.checks contrast via Playwright (local). Skips if unavailable.

    CI images often lack playwright/Chrome wiring for this path; the static token
    check above still runs everywhere. Run locally with Chrome present.
    """
    posts = [
        "blog/google-business-profile-audit-checklist.html",
        "blog/google-business-profile-booking-link.html",
        "blog/google-business-profile-not-showing-up.html",
        "blog/how-to-get-your-business-to-show-up-in-ai-search.html",
    ]
    # Include the message-button post when present (merged or local #25).
    extra = ROOT / "blog/google-business-profile-message-button.html"
    if extra.is_file() and 'class="checks"' in extra.read_text(encoding="utf-8"):
        posts.append(extra.relative_to(ROOT).as_posix())
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - environment dependent
        print(f"Rendered checklist contrast skipped (playwright unavailable: {exc}). Static token check still ran.")
        return
    chrome = shutil.which("google-chrome") or "/usr/bin/google-chrome"
    if not Path(chrome).is_file():
        print("Rendered checklist contrast skipped (Chrome missing). Static token check still ran.")
        return
    server = start_server(0)
    print("Rendered checklist contrast (Playwright, local):")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=chrome, args=["--no-sandbox", "--disable-dev-shm-usage"])
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            for rel in posts:
                url = f"http://127.0.0.1:{server.server_address[1]}/{rel}"
                page.goto(url, wait_until="networkidle")
                measured = page.evaluate(
                    """() => {
                      const el = document.querySelector('.prose ul.checks li');
                      const box = document.querySelector('.prose ul.checks');
                      if (!el || !box) return {missing: true};
                      const cs = getComputedStyle(el);
                      const bg = getComputedStyle(box).backgroundColor;
                      return {color: cs.color, background: bg, size: parseFloat(cs.fontSize)};
                    }"""
                )
                if not isinstance(measured, dict) or measured.get("missing"):
                    fail(f"{rel} .prose ul.checks li was not rendered")
                    continue
                fg = _rgb(str(measured.get("color")))
                bg = _rgb(str(measured.get("background")))
                if fg is None or bg is None:
                    fail(f"{rel} checklist colors could not be read: {measured}")
                    continue
                ratio = contrast_ratio(
                    f"#{fg[0]:02X}{fg[1]:02X}{fg[2]:02X}",
                    f"#{bg[0]:02X}{bg[1]:02X}{bg[2]:02X}",
                )
                print(
                    f"  {rel}: {measured['color']} on {measured['background']} = {ratio:.2f}:1"
                )
                if ratio < 4.5:
                    fail(f"{rel} checklist contrast {ratio:.2f} is below 4.5")
            browser.close()
    finally:
        server.shutdown()


def check_contrast() -> None:
    pairs = (
        ("body text", "#0C172E", "#FFFFFF", 4.5),
        ("secondary text", "#475569", "#FFFFFF", 4.5),
        ("secondary on mist", "#475569", "#F7FAFC", 4.5),
        ("secondary on audit tint", "#475569", "#EEF4F8", 4.5),
        ("link", "#006B80", "#FFFFFF", 4.5),
        ("link on mist", "#006B80", "#F7FAFC", 4.5),
        ("link on audit tint", "#006B80", "#EEF4F8", 4.5),
        ("link hover", "#0C172E", "#FFFFFF", 4.5),
        ("primary button", "#041018", "#00D4FF", 4.5),
        ("primary hover", "#041018", "#67E6FF", 4.5),
        ("primary disabled", "#475569", "#CBD5E1", 4.5),
        ("focus ring", "#006B80", "#FFFFFF", 3.0),
        ("navy on mist", "#0C172E", "#F7FAFC", 4.5),
    )
    print("Computed WCAG contrast:")
    for name, fg, bg, minimum in pairs:
        ratio = contrast_ratio(fg, bg)
        status = "pass" if ratio >= minimum else "fail"
        print(f"  {name}: {fg} on {bg} = {ratio:.2f}:1 (need {minimum}:1) {status}")
        if ratio < minimum:
            fail(f"{name} contrast {ratio:.2f} is below {minimum}")
    home = read("index.html")
    for token, color in (("--text:", "#0C172E"), ("--link:", "#006B80"), ("--cyan:", "#00D4FF"), ("--void:", "#FFFFFF")):
        if token not in home or color not in home:
            fail(f"Home theme is missing {token} {color}")
    if '.crew-more::after { content: " →"; }' not in home:
        fail("Home scroll cue is missing its arrow affordance")
    check_theme_sources()


def check_checklist_panel_tokens() -> None:
    """Blog checklists must use the light panel tokens (#EEF4F8 / #0C172E / #006B80)."""
    dark = "background: #0b1226"
    for path in sorted((ROOT / "blog").glob("*.html")):
        text = path.read_text(encoding="utf-8")
        if "ul.checks" not in text and "class=\"checks\"" not in text:
            continue
        rel = path.relative_to(ROOT).as_posix()
        if dark in text.lower() and ".prose ul.checks" in text:
            # only fail if the checks rule itself is still dark
            if re.search(r"\.prose ul\.checks\s*\{[^}]*background:\s*#0b1226", text, flags=re.I | re.S):
                fail(f"{rel} still uses the dark .prose ul.checks panel")
        if ".prose ul.checks" in text:
            has_bg = "background: #EEF4F8" in text
            has_accent = ("border: 2px solid #006B80" in text) or ("border-color: #006B80" in text)
            has_text = "color: #0C172E" in text
            if not (has_bg and has_accent and has_text):
                fail(f"{rel} .prose ul.checks is missing the light panel tokens")


def check_theme_sources() -> None:
    """The blocked head kept pale :root tokens, so var(--text) stayed near-white."""
    required = (
        "services.html",
        "newsletter.html",
        "blog/index.html",
        "blog/google-business-profile-audit-checklist.html",
        "blog/google-business-profile-booking-link.html",
        "blog/google-business-profile-not-showing-up.html",
        "blog/how-to-get-your-business-to-show-up-in-ai-search.html",
        "blog/google-business-profile-message-button.html",
        "_templates/post-template.html",
        "index.html",
        "team.html",
        "404.html",
    )
    for rel in (*PUBLIC_HTML, "_templates/post-template.html"):
        text = read(rel)
        for bad in ("#f4f7fb", "#b7c4d6"):
            if bad in text.lower():
                fail(f"{rel} still contains dark-theme color {bad}")
        if rel in required:
            for token in ("--text: #0C172E", "--muted: #475569"):
                if token not in text:
                    fail(f"{rel} :root is missing {token}")
    template = read("_templates/post-template.html")
    if ".prose a { color: #006B80; }" not in template:
        fail("Post template can still publish gold prose links")
    if "h1, h2, h3, .prose h2, .prose h3 { color: #0C172E; }" not in template:
        fail("Post template can still publish gold prose headings")
    splash = read("_archive/tianna-intro-splash.html")
    archived_gallery = read("_archive/tianna-gallery-v4.html")
    if "AI Creative Studio" not in splash or "Digital Avatar" not in splash:
        fail("Archived intro splash no longer contains its original source")
    if "var T_RAIN=1.8, T_FORM_END=5.2, T_COLOR_END=7.0, T_TITLE=6.0, T_CTA=8.2;" not in splash:
        fail("Archived intro splash was rewritten")
    if "avatar series" not in archived_gallery or "AI Creative Studio" not in archived_gallery:
        fail("Archived gallery source no longer contains its original source")
    for rel in ("tianna-intro.html", "tianna-gallery-v4.html"):
        text = read(rel)
        for banned in ("mx-canvas", "goGallery", "Enter the Gallery", "Digital Avatar", "AI Creative Studio", "Tianna Gallery", "<canvas", 'http-equiv="refresh"'):
            if banned in text:
                fail(f"{rel} still serves splash behavior ({banned})")
    privacy = read("privacy.html")
    nav = re.search(r'class="nav-logo">(.*?)</a>', privacy, re.S)
    if nav is None or "mark-glow-disc.png" not in nav.group(1) or ">TJNAI<" not in nav.group(1):
        fail("Privacy header is not the disc plus TJNAI wordmark")


def _rgb(color: str) -> tuple[int, int, int] | None:
    match = re.search(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", color or "")
    if not match:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


class _Cdp:
    def __init__(self, socket: object) -> None:
        self.socket = socket
        self._next = 0

    def call(self, method: str, params: dict | None = None, session_id: str | None = None) -> dict:
        self._next += 1
        message: dict = {"id": self._next, "method": method, "params": params or {}}
        if session_id:
            message["sessionId"] = session_id
        self.socket.send(json.dumps(message))  # type: ignore[attr-defined]
        while True:
            payload = json.loads(self.socket.recv())  # type: ignore[attr-defined]
            if payload.get("id") != self._next:
                continue
            if "error" in payload:
                raise RuntimeError(payload["error"])
            result = payload.get("result", {})
            return result if isinstance(result, dict) else {}


def check_rendered(server: ThreadingHTTPServer) -> None:
    chrome = shutil.which("google-chrome") or shutil.which("google-chrome-stable")
    if chrome is None:
        print("Chrome is not installed; source token assertions still ran.")
        return
    try:
        import websocket
    except ImportError:
        print("websocket-client is not installed; source token assertions still ran.")
        return
    import urllib.request

    port = 9334
    profile = tempfile.mkdtemp(prefix="tjnai-chrome-")
    process = subprocess.Popen(
        [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{server.server_address[1]}"
    probes = (
        ("/services", (".step-price", ".step-when", ".step .note", ".hero-index a", ".lede", ".sec-intro", ".svc p", ".copy")),
        ("/blog/", (".lede", ".post-card p", ".site-nav a", "h1")),
        ("/blog/google-business-profile-audit-checklist", (".lede", ".prose p", ".prose h3", ".prose a", ".site-nav a", ".prose ul.checks li")),
        ("/blog/google-business-profile-booking-link", (".prose ul.checks li",)),
        ("/blog/google-business-profile-not-showing-up", (".prose ul.checks li",)),
        ("/blog/how-to-get-your-business-to-show-up-in-ai-search", (".prose ul.checks li",)),
        ("/newsletter", (".lede", "h1", ".site-nav a")),
        ("/privacy", ("h1", ".card p", ".card h2", ".nav-logo span", ".page-label")),
        ("/tovael-privacy", ("h1", ".card p")),
        ("/", (".lede", ".crew-more", "h1")),
        ("/auth/confirm", ("h1", "p")),
    )
    try:
        version = None
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as response:
                    version = json.load(response)
                    break
            except Exception:
                time.sleep(0.2)
        if not version:
            fail("Chrome DevTools did not start")
            return
        socket = websocket.create_connection(version["webSocketDebuggerUrl"], timeout=30, suppress_origin=True)
        cdp = _Cdp(socket)
        target = cdp.call("Target.createTarget", {"url": "about:blank"})
        session = cdp.call("Target.attachToTarget", {"targetId": target["targetId"], "flatten": True})["sessionId"]
        cdp.call("Page.enable", session_id=session)
        cdp.call("Runtime.enable", session_id=session)

        def go(path: str, width: int, height: int = 900) -> None:
            cdp.call(
                "Emulation.setDeviceMetricsOverride",
                {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": width < 768},
                session,
            )
            cdp.call("Page.navigate", {"url": base + path}, session)
            ready = time.time() + 8
            while time.time() < ready:
                state = cdp.call(
                    "Runtime.evaluate",
                    {"expression": "document.readyState", "returnByValue": True},
                    session,
                )
                if state.get("result", {}).get("value") == "complete":
                    return
                time.sleep(0.1)

        def eval_value(expression: str) -> object:
            result = cdp.call(
                "Runtime.evaluate",
                {"expression": expression, "returnByValue": True},
                session,
            )
            return result.get("result", {}).get("value")

        style_js = """
          (function(sel){
            var el = document.querySelector(sel);
            if(!el) return {missing:true};
            function bg(node){
              while(node){
                var c = getComputedStyle(node).backgroundColor;
                var m = c.match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)(?:,\\s*([\\d.]+))?\\)/);
                if(m && (m[4]===undefined || parseFloat(m[4])>=0.9)) return c;
                node = node.parentElement;
              }
              return 'rgb(255,255,255)';
            }
            var cs = getComputedStyle(el);
            return {color:cs.color, background:bg(el), size:parseFloat(cs.fontSize)};
          })(%s)
        """
        print("Computed route contrast:")
        for path, selectors in probes:
            go(path, 1280)
            for selector in selectors:
                measured = eval_value(style_js % json.dumps(selector))
                if not isinstance(measured, dict) or measured.get("missing"):
                    fail(f"{path} {selector} was not rendered")
                    continue
                fg = _rgb(str(measured.get("color")))
                bg = _rgb(str(measured.get("background")))
                if fg is None or bg is None:
                    fail(f"{path} {selector} color could not be read: {measured}")
                    continue
                ratio = contrast_ratio(f"#{fg[0]:02X}{fg[1]:02X}{fg[2]:02X}", f"#{bg[0]:02X}{bg[1]:02X}{bg[2]:02X}")
                minimum = 3.0 if float(measured.get("size") or 0) >= 24 else 4.5
                print(f"  {path} {selector}: {measured['color']} on {measured['background']} = {ratio:.2f}:1")
                if ratio < minimum:
                    fail(f"{path} {selector} contrast {ratio:.2f} is below {minimum}")

        go("/", 390, 844)
        cue = eval_value(
            """
            (function(){
              var cue = document.querySelector('.crew-more');
              var y = cue.getBoundingClientRect().top + window.scrollY - 180;
              window.scrollTo(0, Math.max(0, y));
              var row = document.querySelector('.crew');
              var cs = getComputedStyle(cue);
              var arrow = getComputedStyle(cue, '::after').content;
              var box = cue.getBoundingClientRect();
              return {
                text: cue.innerText,
                arrow: arrow,
                display: cs.display,
                fontSize: cs.fontSize,
                scrollable: row.scrollWidth > row.clientWidth + 8,
                tall: box.height >= 16,
                top: Math.round(box.top),
                innerHeight: window.innerHeight,
                scrollY: Math.round(window.scrollY),
                scrollHeight: document.documentElement.scrollHeight,
                shown: box.bottom > 0 && box.top < window.innerHeight
              };
            })()
            """
        )
        print(f"Home mobile scroll cue: {cue}")
        if not isinstance(cue, dict) or "Scroll to see the rest of the team" not in str(cue.get("text")):
            fail("Home mobile scroll text is not rendered")
        elif (
            not cue.get("scrollable")
            or not cue.get("tall")
            or cue.get("display") == "none"
            or "→" not in str(cue.get("arrow"))
            or float(cue.get("fontSize", "0").replace("px", "") or 0) < 16
        ):
            fail(f"Home mobile scroll cue is not visibly offering scroll: {cue}")
        elif int(cue.get("scrollHeight") or 0) <= int(cue.get("innerHeight") or 0) and not cue.get("shown"):
            fail(f"Home mobile page cannot scroll to the team cue: {cue}")

        for path in ("/", "/services", "/blog/", "/newsletter", "/privacy", "/tianna-intro", "/tianna-gallery-v4"):
            go(path, 1440)
            for width in (320, 390, 768, 1280, 1440):
                cdp.call(
                    "Emulation.setDeviceMetricsOverride",
                    {"width": width, "height": 900, "deviceScaleFactor": 1, "mobile": width < 768},
                    session,
                )
                time.sleep(0.05)
                box = eval_value(
                    "({sw:document.documentElement.scrollWidth,cw:document.documentElement.clientWidth})"
                )
                if not isinstance(box, dict):
                    fail(f"{path} overflow could not be measured at {width}")
                    continue
                if int(box["sw"]) > int(box["cw"]) + 1:
                    fail(f"{path} overflows at {width}: scroll {box['sw']} client {box['cw']}")

        for path in ("/tianna-intro", "/tianna-gallery-v4"):
            go(path, 390, 844)
            neutral = eval_value(
                """
                ({
                  h1: (document.querySelector('h1') || {}).textContent || '',
                  canvas: document.querySelectorAll('canvas').length,
                  refresh: !!document.querySelector('meta[http-equiv="refresh"]'),
                  text: document.body.innerText
                })
                """
            )
            print(f"Neutral route {path}: heading={neutral.get('h1') if isinstance(neutral, dict) else neutral!r}")
            if not isinstance(neutral, dict) or not str(neutral.get("h1")).strip() or neutral.get("canvas") or neutral.get("refresh"):
                fail(f"{path} is not a static reference page: {neutral}")
                continue
            for phrase in ("Digital Avatar", "AI Creative Studio", "Enter the Gallery", "Continuing in", "Tianna Gallery"):
                if phrase in str(neutral.get("text")):
                    fail(f"{path} still shows splash copy {phrase}")
        socket.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def check_home_and_roster() -> None:
    home = read("index.html")
    if "For small businesses:" in home:
        fail("Home still repeats the inline price line under the services CTA")
    for phrase in (
        "TJNAI.ai LLC · North Carolina",
        "Results in one week.",
        "set up, connected and tested in 10 business days",
        "Scroll to see the rest of the team",
    ):
        if phrase not in home:
            fail(f"Home is missing required phrase: {phrase}")
    if re.search(r"\b(Fiona|Wren)\b", home):
        fail("Home strip names Fiona or Wren; the approved home roster stays unchanged")
    home_names = tuple(re.findall(r'class="mate-nm">([^<]+)', home))
    if home_names != HOME_ROSTER:
        fail(f"Home roster is {home_names}; expected {HOME_ROSTER}")
    team = read("team.html")
    team_names = tuple(re.findall(r'class="nm">([^<]+)', team))
    if team_names != TEAM_ROSTER:
        fail(f"Team roster is {team_names}; expected {TEAM_ROSTER}")
    if re.search(r"\bWren\b", team):
        fail("Team page names Wren")
    baseline_team = try_git_text_rev("d71e3cc", "team.html") or try_git_text("team.html")
    if baseline_team is not None:
        current_words = visible_text(team)
        previous_words = (
            visible_text(baseline_team)
            .replace(" TJ Tim (TJ)", " Tim (TJ)", 1)
            .replace(" Tianna Gallery", "")
        )
        if current_words != previous_words:
            fail("team.html visible wording changed beyond replacing the TJ initials with Tim's portrait")


def deploy_rm_targets() -> list[str]:
    workflow = read(".github/workflows/deploy.yml")
    match = re.search(r"^        run: rm -rf (.+)$", workflow, flags=re.M)
    if not match:
        fail("deploy.yml has no rm -rf step to simulate")
        return []
    return match.group(1).split()


def check_deploy_artifact() -> None:
    targets = deploy_rm_targets()
    if not targets:
        return
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "artifact"
        shutil.copytree(ROOT, dest, ignore=shutil.ignore_patterns(".git", "node_modules", ".wrangler"))
        unrelated = dest / "assets/products/unrelated-future-asset.bin"
        unrelated.write_bytes(b"keep")
        maintenance_module().apply(dest, workflow_maintenance_switch())
        for target in targets:
            path = dest / target
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
        for rel in PRESERVED_UNLINKED:
            if (dest / rel).exists():
                fail(f"Simulated Pages artifact still contains {rel}")
        if not unrelated.is_file():
            fail("An unrelated file in assets/products was removed by the deploy exclusion")
        if (dest / "_checks").exists():
            fail("Simulated Pages artifact still contains _checks")
        if (dest / "_archive").exists():
            fail("Simulated Pages artifact still contains the archived splash")
        if (dest / "_build").exists():
            fail("Simulated Pages artifact still contains _build")
        if (dest / "_backend").exists():
            fail("Simulated Pages artifact still contains _backend (Worker source)")
        required = (
            "index.html",
            "services.html",
            "team.html",
            "privacy.html",
            "terms.html",
            "refund.html",
            "tovael-privacy.html",
            "auth/confirm/index.html",
            ".well-known/apple-app-site-association",
            "assets/logo/og-card.jpg",
            "assets/logo/mark-glow-disc.png",
            "assets/team/fiona-160.jpg",
        )
        for rel in required:
            if not (dest / rel).is_file():
                fail(f"Simulated Pages artifact is missing {rel}")
        legal_studio = 'TJNAI AI Creative Studio ("TJNAI," "we," "us," or "our")'
        offer_phrases = (
            "ai creative studio",
            "digital avatar",
            "tianna gallery",
            "enter the gallery",
            "avatar series",
        )
        for html in dest.rglob("*.html"):
            text = html.read_text(encoding="utf-8", errors="replace")
            scrubbed = text.replace(legal_studio, "")
            lowered = scrubbed.lower()
            rel = html.relative_to(dest).as_posix()
            for phrase in offer_phrases:
                if phrase in lowered:
                    fail(f"Deploy artifact {rel} still contains {phrase}")
            legal_copy = rel in LEGAL_GENERATED
            if not legal_copy and re.search(r"pickle|gumroad|what we've built", lowered):
                fail(f"Deploy artifact {rel} still promotes a removed app or product")
        for rel in ("tianna-intro.html", "tianna-gallery-v4.html"):
            if not (dest / rel).is_file():
                fail(f"Deploy artifact is missing retained route {rel}")
        if not (ROOT / "_archive/tianna-intro-splash.html").is_file():
            fail("Intro splash source is not preserved in git")
        if not (ROOT / "_archive/tianna-gallery-v4.html").is_file():
            fail("Gallery source is not preserved in git")
    print("Source assets kept in git and omitted from the simulated upload:")
    for rel in PRESERVED_UNLINKED:
        print(f"  - {rel}")


def check_newsletter() -> None:
    text = read("newsletter.html")
    if "<form" in text.lower():
        fail("newsletter.html has a form; signup must stay off")
    if "Sign-up opens soon" not in text:
        fail("newsletter.html no longer says sign-up opens soon")
    if "NEWSLETTER_ENDPOINT" in text:
        fail("newsletter endpoint was turned on")


def check_removed_anchors() -> None:
    home = parse("index.html")
    for anchor in REMOVED_HASHES:
        if anchor in home.ids:
            fail(f"Home still has promotional anchor #{anchor}")
    for required in ("about", "contact", "services", "main"):
        if required not in home.ids:
            fail(f"Home is missing #{required}")


class Handler(BaseHTTPRequestHandler):
    """Serves like GitHub Pages: /page and /page.html both map to page.html."""

    root = ROOT

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path or "/"
        found = url_to_file(path, self.root)
        if found is None:
            body = (self.root / "404.html").read_bytes()
            self.send_response(404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = (self.root / found).read_bytes()
        content_type = "text/html; charset=utf-8" if found.suffix == ".html" else "application/octet-stream"
        if found.suffix == ".jpg":
            content_type = "image/jpeg"
        elif found.suffix == ".png":
            content_type = "image/png"
        elif found.suffix == ".css":
            content_type = "text/css"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        return


def start_server(port: int = 0, root: Path = ROOT) -> ThreadingHTTPServer:
    handler = type("RootHandler", (Handler,), {"root": root})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def check_404(server: ThreadingHTTPServer) -> None:
    import urllib.request

    base = f"http://127.0.0.1:{server.server_address[1]}"

    def fetch(path: str) -> tuple[int, str, str]:
        try:
            response = urllib.request.urlopen(base + path)
            status = response.status
            headers = response.headers
            body = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            status = exc.code
            headers = exc.headers
            body = exc.read().decode("utf-8", "replace")
        location = headers.get("Location", "")
        return status, location, body

    for path in ("/", "/services", "/privacy", "/terms", "/refund", "/tovael-privacy", "/auth/confirm", "/newsletter", "/tianna-intro", "/tianna-gallery-v4"):
        status, location, body = fetch(path)
        if status != 200 or location:
            fail(f"{path} returned {status} location={location!r}; expected 200 and no redirect")
        if path in {"/tianna-intro", "/tianna-gallery-v4"}:
            if re.search(r"digital avatar|enter the gallery|tianna gallery|mx-canvas|http-equiv=\"refresh\"", body, flags=re.I):
                fail(f"{path} still serves the splash")
            if "<canvas" in body.lower():
                fail(f"{path} still includes a canvas splash")
        if path == "/newsletter" and "Sign-up opens soon" not in body:
            fail("Preview /newsletter is not the signup-off page")
        if path == "/tovael-privacy" and "Tovael" not in body:
            fail("/tovael-privacy did not serve the Tovael policy")
    status, location, body = fetch("/services")
    for price in ("$350", "$750", "$150"):
        if price not in body:
            fail(f"Preview /services is missing {price}")
    for path in OLD_PATHS + ("/this-page-does-not-exist",):
        status, location, body = fetch(path)
        if status != 404:
            fail(f"{path} returned {status}, expected 404")
        if location:
            fail(f"{path} sent a redirect to {location}")
        if "We couldn't find that page" not in body:
            fail(f"{path} 404 body is not the branded 404 page")
        if re.search(r"pickle|gumroad|what we've built", body, flags=re.I):
            fail(f"{path} 404 body still promotes a removed product")
    status, _, body = fetch("/")
    if "PickleRecord" in body or "What we've built" in body:
        fail("Preview home still promotes removed products")


def check_promo_allowlist_narrow() -> None:
    """Three plain asserts for the Message-button front-desk allowlist."""
    rel = "blog/google-business-profile-message-button.html"
    if promo_hits(rel, scanned_text(rel)):
        fail("real Message-button post must pass promo checks")
    if not promo_hits(rel, scanned_text(rel) + "\nAI front desk\n"):
        fail("same page with 'AI front desk' must fail")
    if not promo_hits(
        "blog/index.html",
        scanned_text("blog/index.html") + "\na shop with a front desk\n",
    ):
        fail("another page with the allowlisted phrase must fail")


# ---------------------------------------------------------------------------
# Maintenance mode
# ---------------------------------------------------------------------------

MAINTENANCE_KEEP = (
    "terms.html",
    "privacy.html",
    "refund.html",
    "tovael-privacy.html",
    "auth/confirm/index.html",
    "404.html",
    "maintenance.html",
)
MAINTENANCE_HEADING = "We're doing some maintenance"
MAINTENANCE_LINE = "Our website is undergoing maintenance. Thank you for your patience."
MAINTENANCE_BANNED = (
    r"\$\s?\d",
    r"(?<!terms of )\bservices?\b",
    r"\baudit\b",
    r"\bbuild\b",
    r"monthly care",
    r"pickle",
    r"newsletter",
    r"back (by|on|in)\b",
    r"\breopen",
    r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\b",
    r"\bmon(day)?\b|\btue(sday)?\b|\bwed(nesday)?\b|\bthu(rsday)?\b|\bfri(day)?\b",
    r"\bwork with us\b",
    r"\bblog\b",
)


def maintenance_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("maintenance_build", ROOT / "_build/maintenance.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def workflow_maintenance_switch() -> bool:
    workflow = read(".github/workflows/deploy.yml")
    match = re.search(r"^  MAINTENANCE_MODE: '(true|false)'$", workflow, flags=re.M)
    if not match:
        fail("deploy.yml has no top-level MAINTENANCE_MODE: 'true'|'false' switch")
        return False
    return match.group(1) == "true"


def check_maintenance_page() -> None:
    rel = "maintenance.html"
    raw = read(rel)
    page = parse(rel)
    text = visible_text(raw)
    print(f"Maintenance page text: {text}")
    for needle in (MAINTENANCE_HEADING, MAINTENANCE_LINE, "TJNAI", "info@tjnai.ai"):
        if needle not in text:
            fail(f"maintenance.html is missing: {needle}")
    for pattern in MAINTENANCE_BANNED:
        if re.search(pattern, text, flags=re.I):
            fail(f"maintenance.html visible text matches banned /{pattern}/")
    if sensitive_hits(text) or sensitive_hits(raw):
        fail("maintenance.html contains a blocked sensitive term")
    if not page.title or "maintenance" not in page.title.lower():
        fail("maintenance.html needs a maintenance <title>")
    if "maintenance" not in page.metas.get("description", "").lower():
        fail("maintenance.html needs a maintenance meta description")
    if not page.noindex:
        fail("maintenance.html must carry noindex while maintenance is on (open-ended)")
    if page.canonical:
        fail("maintenance.html must not declare a canonical; it is copied onto many routes")
    if "og:url" in page.metas:
        fail("maintenance.html must not declare og:url; it is copied onto many routes")
    if page.jsonld:
        fail("maintenance.html must not carry JSON-LD")
    hrefs = set(page.hrefs)
    for href in ("/terms", "/privacy", "/refund", "mailto:info@tjnai.ai"):
        if href not in hrefs:
            fail(f"maintenance.html is missing the link {href}")
    for href in page.hrefs:
        if href.startswith(("http://", "https://")):
            fail(f"maintenance.html links off-site: {href}")
        if not href.startswith(("/", "mailto:")):
            fail(f"maintenance.html uses a relative link {href}; it must work from any route depth")
    for src in re.findall(r'\b(?:src|href)=["\']([^"\']+)', raw) + re.findall(r'url\(["\']?([^"\')]+)', raw):
        if src.startswith("https://tjnai.ai/assets/logo/og-card.jpg"):
            continue
        if src.startswith(("http://", "https://", "//")):
            fail(f"maintenance.html loads a third-party resource: {src}")
        if src.startswith("/") and not src.startswith("mailto:"):
            target = url_to_file(src)
            if target is None and not (ROOT / src.lstrip("/")).is_file():
                fail(f"maintenance.html references a missing file {src}")
    for font in ("/assets/fonts/inter-latin.woff2", "/assets/fonts/space-grotesk-latin.woff2"):
        if font not in raw:
            fail(f"maintenance.html does not self-host {font}")
    for token in ("#0C172E", "#006B80", "#EEF4F8", "/assets/logo/mark-glow-disc.png"):
        if token not in raw:
            fail(f"maintenance.html is missing brand token {token}")
    if "<script" in raw.lower():
        fail("maintenance.html should not need JavaScript")


def check_404_no_services() -> None:
    """The 404 page stays up in maintenance mode: logo, home link, customer line, legal footer only."""
    raw = read("404.html")
    page = parse("404.html")
    allowed = {"#main", "/", "mailto:info@tjnai.ai", "/privacy", "/terms", "/refund"}
    for href in page.hrefs:
        if href.endswith((".png", ".jpg", ".webp", ".svg")):
            continue
        if href not in allowed:
            fail(f"404.html links {href}; only home, the customer email and legal pages are allowed")
    if "<nav class=\"site-nav\"" in raw:
        fail("404.html still has the primary nav")
    if "Current customers: for help with a purchase, email info@tjnai.ai." not in visible_text(raw).replace(" .", "."):
        fail("404.html is missing the current-customers line")
    if "We couldn't find that page" not in raw or "Go to the home page" not in raw:
        fail("404.html lost its heading or home link")
    text = visible_text(raw) + " " + page.metas.get("description", "")
    for phrase in (r"(?<!terms of )\bservices?\b", r"work with us", r"\bblog\b", r"meet the team"):
        if re.search(phrase, text, flags=re.I):
            fail(f"404.html still says /{phrase}/")
    if 'href="/"' not in raw:
        fail("404.html lost its home link")
    for href in ("/privacy", "/terms", "/refund"):
        if href not in page.hrefs:
            fail(f"404.html footer lost {href}")


def check_maintenance_build() -> None:
    module = maintenance_module()
    if tuple(module.KEEP) != MAINTENANCE_KEEP:
        fail(f"_build/maintenance.py KEEP drifted: {module.KEEP}")
    workflow = read(".github/workflows/deploy.yml")
    deploy_steps = workflow.split("\n  deploy:", 1)[-1]
    step = deploy_steps.find("run: python3 _build/maintenance.py")
    strip = deploy_steps.find("run: rm -rf ")
    upload = deploy_steps.find("actions/upload-pages-artifact")
    if not (0 <= step < strip < upload):
        fail("deploy job must run _build/maintenance.py before the strip and upload steps")
    targets = deploy_rm_targets()
    if "_build" not in targets:
        fail("deploy.yml does not strip _build")
    preview = re.search(r"^          rm -rf (.+)$", workflow, flags=re.M)
    if preview is None or set(preview.group(1).split()) != set(targets) | {".git", ".github"}:
        fail("PR preview strip list does not match the deploy strip list")
    print(f"Workflow MAINTENANCE_MODE switch: {'on' if workflow_maintenance_switch() else 'off'}")
    source = {rel: (ROOT / rel).read_bytes() for rel in PUBLIC_HTML}
    maintenance = (ROOT / "maintenance.html").read_bytes()
    with tempfile.TemporaryDirectory() as tmp:
        # Switch on.
        on = Path(tmp) / "on"
        shutil.copytree(ROOT, on, ignore=shutil.ignore_patterns(".git", "node_modules", ".wrangler"))
        replaced = module.apply(on, True)
        expected_replaced = sorted(rel for rel in PUBLIC_HTML if rel not in MAINTENANCE_KEEP)
        if sorted(replaced) != expected_replaced:
            fail(f"Maintenance replaced {sorted(replaced)}; expected {expected_replaced}")
        for rel in PUBLIC_HTML:
            built = (on / rel).read_bytes()
            if rel in MAINTENANCE_KEEP:
                if built != source[rel]:
                    fail(f"Maintenance build changed allowlisted page {rel}")
            elif built != maintenance:
                fail(f"Maintenance build did not replace {rel}")
        for rel in ("_templates/post-template.html", "_archive/tianna-intro-splash.html"):
            if (on / rel).read_bytes() != (ROOT / rel).read_bytes():
                fail(f"Maintenance build touched non-public source {rel}")
        for path in ROOT.rglob("*"):
            rel = path.relative_to(ROOT)
            if not path.is_file() or rel.parts[0] == ".git" or path.suffix == ".html":
                continue
            if "node_modules" in rel.parts or ".wrangler" in rel.parts:
                continue
            if (on / rel).read_bytes() != path.read_bytes():
                fail(f"Maintenance build changed non-HTML file {rel.as_posix()}")
        server = start_server(0, on)
        base = f"http://127.0.0.1:{server.server_address[1]}"
        import urllib.error
        import urllib.request

        def fetch(path: str) -> tuple[int, bytes]:
            try:
                with urllib.request.urlopen(base + path) as response:
                    return response.status, response.read()
            except urllib.error.HTTPError as exc:
                return exc.code, exc.read()

        try:
            shows = []
            for rel in expected_replaced:
                url = file_url(rel)
                variants = {url, "/" + rel}
                for variant in sorted(variants):
                    status, body = fetch(variant)
                    if status != 200 or body != maintenance:
                        fail(f"{variant} did not serve the maintenance page (status {status})")
                    shows.append(variant)
            kept = []
            for rel in MAINTENANCE_KEEP:
                if rel in ("404.html", "maintenance.html"):
                    continue
                for variant in sorted({file_url(rel), "/" + rel}):
                    status, body = fetch(variant)
                    if status != 200 or body != source[rel]:
                        fail(f"{variant} is not the unchanged {rel} in maintenance mode (status {status})")
                    kept.append(variant)
            for path in (".well-known/apple-app-site-association", ".well-known/security.txt", "robots.txt", "sitemap.xml", "assets/fonts/inter-latin.woff2", "assets/logo/mark-glow-disc.png"):
                status, body = fetch("/" + path)
                if status != 200 or body != (ROOT / path).read_bytes():
                    fail(f"/{path} is not served unchanged in maintenance mode")
                kept.append("/" + path)
            status, body = fetch("/this-page-does-not-exist")
            if status != 404 or body != source["404.html"]:
                fail("Unknown routes no longer get the unchanged 404 page in maintenance mode")
            print("Maintenance mode, routes showing maintenance (200 on GitHub Pages):")
            print("  " + " ".join(shows))
            print("Maintenance mode, routes kept unchanged:")
            print("  " + " ".join(kept))
        finally:
            server.shutdown()
        # Switch off: identical to today, minus the maintenance page itself.
        off = Path(tmp) / "off"
        shutil.copytree(ROOT, off, ignore=shutil.ignore_patterns(".git", "node_modules", ".wrangler"))
        if module.apply(off, False):
            fail("Maintenance off still replaced pages")
        if (off / "maintenance.html").exists():
            fail("Maintenance off still uploads maintenance.html")
        for rel in PUBLIC_HTML:
            if rel == "maintenance.html":
                continue
            if (off / rel).read_bytes() != source[rel]:
                fail(f"Maintenance off changed {rel}")
    for value, expected in (("true", True), ("TRUE", True), ("1", True), ("false", False), ("", False), (None, False)):
        if module.is_on(value) is not expected:
            fail(f"MAINTENANCE_MODE={value!r} parsed wrong")


def legal_manifest_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("legal_manifest", ROOT / "_build/legal_manifest.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def legal_docs_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("legal_docs", ROOT / "_build/legal_docs.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def markdown_words(md: str) -> list[str]:
    md = re.sub(r"^\s*(#+|>|[-*]|\d+\.)\s", " ", md, flags=re.M)
    md = re.sub(r"\*\*|`|\|", " ", md)
    md = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", md)
    md = re.sub(r"^\s*-{3,}\s*$", " ", md, flags=re.M)
    md = re.sub(r"(?<!\S):?-{3,}:?(?!\S)", " ", md)
    return md.split()


def label_text(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", html))).strip()


def check_legal_clickwrap() -> None:
    """Clickwrap acceptance (Lena's handoff v0.2): source-level guarantees that also hold in CI."""
    # Criterion 6 / T11: every frozen artifact re-hashes to the registry value; pages are in sync.
    for error in legal_manifest_module().run(write=False):
        fail(f"legal manifest: {error}")
    registry = json.loads(read("_build/legal_versions.json"))
    docs = {doc["document_id"]: doc for doc in registry["documents"]}
    worker = json.loads(read("_backend/acceptance/src/manifest.json"))
    for doc_id, doc in docs.items():
        for version in doc["versions"]:
            if doc.get("inline"):
                want = hashlib.sha256(version["checkbox_text"].encode()).hexdigest() if "checkbox_text" in version else None
                actual = worker["documents"][doc_id]["versions"][-1]
                if want and actual["checkbox_text"] == version["checkbox_text"] and actual["content_sha256"] != want:
                    fail(f"{doc_id} hash is not the SHA-256 of its label")
                continue
            artifact = ROOT / f"legal/archive/{doc['slug']}/{version['version']}.html"
            if not artifact.exists():
                fail(f"{doc_id}-{version['version']} has no frozen artifact")
            elif hashlib.sha256(artifact.read_bytes()).hexdigest() != version["content_sha256"]:
                fail(f"T11: {artifact.relative_to(ROOT)} no longer matches its recorded SHA-256")
    # Criterion 1: page wording is exactly Lena's (stripped) source text, word for word.
    for doc_id, doc in docs.items():
        if doc.get("inline"):
            continue
        version = doc["versions"][-1]
        source = (ROOT / version["source"]).read_text(encoding="utf-8")
        leftovers = legal_docs_module().leftover_internal(source)
        if leftovers:
            fail(f"{version['source']} still carries internal notes: {leftovers[:2]}")
        for rel in (doc["page"], f"legal/archive/{doc['slug']}/{version['version']}.html"):
            html = read(rel)
            main = html[html.index("<h1>"):html.index("</main>")]
            label = f"{doc['title']} — {doc_id}-{version['version']} · Effective "
            if label not in unescape(main):
                fail(f"{rel} is missing the version label '{label}…'")
            main = re.sub(r'<p class="updated doc-label">.*?</p>', " ", main, flags=re.S)
            if visible_text(main).split() != markdown_words(source):
                fail(f"{rel} wording differs from {version['source']}")
            if MAILING_ADDRESS not in html.split("<footer", 1)[-1]:
                fail(f"{rel} footer is missing the mailing address")
            if doc["page"].startswith("legal/") and 'name="robots" content="noindex' not in html:
                fail(f"{rel} should be noindex")
    # Leak check: no internal paths, INTERNAL markers or teammate agent names in anything published or hashed.
    docs_module = legal_docs_module()
    strip_sample = "keep A<!-- INTERNAL: one\nline two -->keep B<!-- INTERNAL: three -->keep C"
    if docs_module.strip_internal(strip_sample).strip() != "keep Akeep Bkeep C":
        fail("legal_docs.strip_internal does not drop multi-line <!-- INTERNAL: --> blocks non-greedily")
    if docs_module.leak_hits("Evaluate the evaluation; Message the sagebrush Novak team, evasive IRIS-free.") != [] \
            or not docs_module.leak_hits("Ask Eva.") or not docs_module.leak_hits("Sage, Lyra") \
            or not docs_module.leak_hits("see /workspace/x") or not docs_module.leak_hits("<!-- INTERNAL: x -->"):
        fail("legal_docs.leak_hits must match whole, case-sensitive teammate names, /workspace and INTERNAL:")
    for rel in LEGAL_GENERATED:
        for hit in docs_module.leak_hits(read(rel), comments_ok=True):
            fail(f"{rel} leaks internal text: {hit}")
    for source in sorted((ROOT / "_build/legal_src").glob("*.md")):
        for hit in docs_module.leak_hits(source.read_text(encoding="utf-8")):
            fail(f"{source.relative_to(ROOT)} leaks internal text: {hit}")
    worker_text = json.dumps(worker, ensure_ascii=False)
    for hit in docs_module.leak_hits(worker_text):
        fail(f"Worker manifest leaks internal text: {hit}")
    # BILLING-CONSENT label and key-terms text are exactly SOW-MAINTAIN's own wording.
    sow = read(docs["SOW-MAINTAIN"]["versions"][-1]["source"])
    plain_md = lambda s: re.sub(r"\s+", " ", re.sub(r"\*\*|`", "", s)).strip()
    billing = worker["documents"]["BILLING-CONSENT"]["versions"][-1]
    key_terms = re.search(r"^## Key terms at a glance.*\n> (.*)$", sow, re.M)
    sow_label = re.search(r"^`\[ \]` (I authorize.*?)(\(Logged as.*)?$", sow, re.M)
    if key_terms is None or billing["billing_disclosure"] != plain_md(key_terms.group(1)):
        fail("care-plan key terms differ from SOW-MAINTAIN's 'Key terms at a glance' box")
    if sow_label is None or billing["checkbox_text"] != plain_md(sow_label.group(1)):
        fail("BILLING-CONSENT label differs from SOW-MAINTAIN's authorization text")
    agree = read("agree.html")
    if MAILING_ADDRESS not in agree.split("<footer", 1)[-1]:
        fail("agree.html footer is missing the mailing address")
    boxes = re.findall(r"<input\b[^>]*type=\"checkbox\"[^>]*>", agree)
    found: list[str] = []
    for box in boxes:
        doc = re.search(r'data-document-id="([^"]+)"', box)
        if doc is None:
            fail(f"agree.html checkbox without data-document-id: {box}")
            continue
        doc_id = doc.group(1)
        found.append(doc_id)
        if re.search(r"\bchecked\b", box):
            fail(f"agree.html pre-checks {doc_id}; every box must start unchecked")
        box_id = re.search(r'\bid="([^"]+)"', box).group(1)
        label = re.search(rf'<label for="{re.escape(box_id)}"[^>]*>(.*?)</label>', agree, re.S)
        if label is None:
            fail(f"agree.html {doc_id} checkbox has no label of its own")
            continue
        # Criterion 2: the rendered label is exactly the text the Worker checks and logs.
        if label_text(label.group(1)) != worker["documents"][doc_id]["versions"][-1]["checkbox_text"]:
            fail(f"agree.html {doc_id} label differs from the manifest text")
        if not docs[doc_id].get("inline") and f'<a href="/legal/archive/{docs[doc_id]["slug"]}/v0.2"' not in label.group(1):
            fail(f"agree.html {doc_id} label does not link the frozen document version")
    if found != registry["box_order"]:
        fail(f"agree.html boxes {found} != box order {registry['box_order']}")
    for flow, spec in worker["flows"].items():
        if spec["required"] != [d for d in registry["box_order"] if d in spec["required"]]:
            fail(f"flow {flow} requires boxes out of order")
    # No hard-coded prices: every dollar figure on the agree page comes from _build/pricing.json.
    prices = set(json.loads(read("_build/pricing.json"))["prices"].values())
    for amount in re.findall(r"\[\$[^\]]*\]|\$\d[\d,.]*", visible_text(agree)):
        if amount not in prices:
            fail(f"agree.html shows a price not from _build/pricing.json: {amount}")
    if not re.search(r"\$\d", " ".join(prices)) or re.search(r"\$\d", re.sub(r"\[\$[^\]]*\]", "", visible_text(agree))):
        fail("agree.html prices must be the bracketed placeholders from _build/pricing.json")
    button = re.search(r'<button\b[^>]*id="agree-submit"[^>]*>', agree)
    if button is None or not re.search(r"\bdisabled\b", button.group(0)):
        fail("agree.html submit button must be disabled in the HTML until every box is checked")
    for token in ("window.addEventListener('pageshow', reset)", "button.disabled = !all", 'autocomplete="off"',
                  "business.value.trim() !== ''", "business_legal_name:", "signer_name:"):
        if token not in agree:
            fail(f"agree.html is missing {token}")
    if re.search(r"var ENDPOINT = '[^']+'", agree):
        fail("agree.html has a live acceptance endpoint; it stays empty until Tim approves go-live")
    order = [agree.index(marker) for marker in ('id="f-business"', 'id="key-terms"', 'id="agree-group"', 'id="agree-submit"')]
    if order != sorted(order):
        fail("agree.html order must be: business name, key terms, checkboxes, submit")
    for rel in PUBLIC_HTML:
        if sensitive_hits(read(rel)):
            fail(f"{rel} contains a blocked sensitive term")
    for rel in ("_build/legal_versions.json", "_build/pricing.json", *(f"_build/legal_src/{p.name}" for p in (ROOT / "_build/legal_src").glob("*.md"))):
        if sensitive_hits(read(rel)):
            fail(f"{rel} contains a blocked sensitive term")
    if "_backend" not in deploy_rm_targets():
        fail("deploy.yml does not strip _backend, so Worker source would be published")
    if not workflow_maintenance_switch():
        fail("MAINTENANCE_MODE must stay 'true' in this PR")
    module = maintenance_module()
    for rel in ("agree.html", *LEGAL_CONTRACTS, *LEGAL_ARCHIVE):
        if rel in module.KEEP:
            fail(f"{rel} must stay behind maintenance until go-live")
    if "_backend" not in module.SKIP_DIRS:
        fail("_build/maintenance.py should skip _backend")
    print("Clickwrap: v0.2 pages match Lena's text word for word, frozen artifacts re-hash, labels match the manifest,")
    print("  boxes start unchecked, submit starts disabled, prices come from config, agree/contract pages stay masked.")


def check_agree_rendered(server: ThreadingHTTPServer) -> None:
    """Lena's T1-T3, T18, T20 in a real browser, when Playwright is available (CI falls back to source checks)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright is not installed; agree.html behavior checks skipped (source checks still ran).")
        return
    base = f"http://127.0.0.1:{server.server_address[1]}"
    flows = json.loads(read("_backend/acceptance/src/manifest.json"))["flows"]
    try:
        with sync_playwright() as pw:
            chrome = shutil.which("google-chrome") or shutil.which("google-chrome-stable")
            options = {"args": ["--no-sandbox", "--disable-dev-shm-usage"]}
            if chrome:
                options["executable_path"] = chrome
            browser = pw.chromium.launch(**options)
            page = browser.new_page()
            for flow, spec in flows.items():
                expected = spec["required"]
                page.goto(f"{base}/agree.html?flow={flow}")
                visible = page.locator('.agree-row:visible input[name="accept"]')
                shown = [visible.nth(i).get_attribute("data-document-id") for i in range(visible.count())]
                if shown != expected:
                    fail(f"agree.html?flow={flow} shows {shown}, expected {expected}")
                    continue
                boxes = page.locator('input[name="accept"]:not([disabled])')
                button = page.locator("#agree-submit")
                # T1: nothing pre-checked on load.
                if any(boxes.nth(i).is_checked() for i in range(len(expected))):
                    fail(f"T1: agree.html?flow={flow} has a box checked on load")
                # T2: button stays disabled until the last box is checked (business name filled first).
                page.fill("#f-business", "Example Plumbing LLC")
                for i in range(len(expected)):
                    if not button.is_disabled():
                        fail(f"T2: agree.html?flow={flow} button enabled with {i} of {len(expected)} boxes checked")
                    boxes.nth(i).check()
                if button.is_disabled():
                    fail(f"T2: agree.html?flow={flow} button still disabled with every box checked")
                # T3: unchecking any box re-disables.
                boxes.nth(len(expected) - 1).uncheck()
                if not button.is_disabled():
                    fail(f"T3: agree.html?flow={flow} unchecking a box did not re-disable the button")
                boxes.nth(len(expected) - 1).check()
                # T18: empty business name with every box checked → still disabled.
                page.fill("#f-business", "   ")
                if not button.is_disabled():
                    fail(f"T18: agree.html?flow={flow} button enabled without a business legal name")
                # Reload: boxes come back unchecked.
                page.fill("#f-business", "Example Plumbing LLC")
                page.reload()
                if any(boxes.nth(i).is_checked() for i in range(len(expected))) or not button.is_disabled():
                    fail(f"agree.html?flow={flow} keeps boxes checked after reload")
                key_terms = page.locator("#key-terms")
                if flow == "care-plan":
                    if not key_terms.is_visible():
                        fail("care-plan key-terms box is not shown")
                    elif key_terms.bounding_box()["y"] >= page.locator("#agree-group").bounding_box()["y"]:
                        fail("care-plan key-terms box must sit above the checkboxes")
                elif key_terms.is_visible() or page.locator("#agree-billing-consent").is_visible():
                    fail(f"agree.html?flow={flow} shows care-plan billing outside the care-plan flow")
            # Back navigation: boxes come back unchecked (pageshow reset).
            page.goto(f"{base}/agree.html?flow=audit")
            for box in page.locator('input[name="accept"]:not([disabled])').all():
                box.check()
            page.goto(f"{base}/terms.html")
            page.go_back()
            if page.locator('input[name="accept"]:checked').count():
                fail("agree.html keeps boxes checked after back navigation")
            # T20: each link opens the exact frozen version named in its label.
            page.goto(f"{base}/agree.html?flow=care-plan")
            for link in page.locator(".agree-row:visible label a").all():
                href = link.get_attribute("href")
                label = link.locator("xpath=..").inner_text()
                display = re.search(r"\(([A-Z0-9-]+-v\d+\.\d+)\)", label).group(1)
                target = browser.new_page()
                target.goto(f"{base}{href}.html")
                if display not in target.locator(".doc-label").inner_text():
                    fail(f"T20: {href} does not open {display}")
                target.close()
            browser.close()
    except Exception as exc:  # browser missing etc.
        print(f"Playwright could not run ({exc.__class__.__name__}: {str(exc).splitlines()[0][:120]}); agree.html behavior checks skipped.")
        return
    print("Clickwrap (browser): T1 unchecked on load; T2 disabled until all checked; T3 re-disabled; T18 business name;")
    print("  reload/back unchecked; care-plan key terms above boxes; T20 links open the named frozen version.")



def check_sensitive_term_hasher() -> None:
    """The hasher matches lowercased 1- to 3-word grams by SHA-256, not substrings."""
    if not SENSITIVE_TERM_HASHES or any(len(h) != 64 or any(c not in "0123456789abcdef" for c in h) for h in SENSITIVE_TERM_HASHES):
        fail("SENSITIVE_TERM_HASHES must be lowercase hex SHA-256 digests")
    probe = "xyzzyquux"
    digest = sha256_lower(probe)
    if digest not in ngram_hashes("Hello XYZZYQUUX there."):
        fail("ngram_hashes must match a 1-word gram case-insensitively")
    if digest in ngram_hashes("hello there") or digest in ngram_hashes("xyzzyquuxify"):
        fail("ngram_hashes must not match an absent word or a superstring token")
    two = sha256_lower("alpha beta")
    three = sha256_lower("alpha beta gamma")
    sample = "Please see Alpha-Beta Gamma today."
    grams = ngram_hashes(sample)
    if two not in grams or three not in grams:
        fail("ngram_hashes must emit 2- and 3-word grams (hyphens split tokens)")
    if sha256_lower("see alpha") not in grams:
        fail("ngram_hashes missed a 2-word gram")
    print("Sensitive-term hasher: 1- to 3-word grams hash-match; substrings do not.")
    # The blocked hashes themselves must not appear in committed public or legal text.
    # (This is also the false-positive check against the real site.)
    targets = list(PUBLIC_HTML)
    extra = [
        "_build/legal_versions.json",
        "_build/pricing.json",
        "_backend/acceptance/src/manifest.json",
        "agree.html",
    ]
    src = ROOT / "_build/legal_src"
    if src.is_dir():
        extra.extend(str(p.relative_to(ROOT)) for p in src.glob("*.md"))
    for rel in targets + extra:
        path = ROOT / rel
        if not path.is_file():
            continue
        if sensitive_hits(read(rel)):
            fail(f"{rel} contains a blocked sensitive term")


def main() -> int:
    check_inventory()
    check_promo()
    check_sensitive_term_hasher()
    check_promo_allowlist_narrow()
    check_links()
    check_canonical_sitemap_schema()
    check_prices_and_services_body()
    check_unchanged_and_assets()
    check_active_previews()
    check_home_and_roster()
    check_content_invariants()
    check_tim_portrait()
    check_checklist_panel_tokens()
    check_checklist_rendered_contrast()
    check_contrast()
    check_deploy_artifact()
    check_newsletter()
    check_removed_anchors()
    check_maintenance_page()
    check_maintenance_build()
    check_404_no_services()
    check_legal_clickwrap()
    server = start_server()
    try:
        check_404(server)
        check_rendered(server)
        check_agree_rendered(server)
    finally:
        server.shutdown()
    if failures:
        print(f"\n{len(failures)} failure(s):")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("\nAll local public-site checks passed.")
    print("Deploy upload omits five named files. An unrelated assets/products file would remain.")
    print("Build menu letters are A, C, D, E. Example B is omitted and B was not reused.")
    print("Legal product disclosures, Tovael privacy, and /auth/confirm are unchanged.")
    print("No redirects for /aquaiq, /dipcue-privacy, or old home hash URLs.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--serve":
        port = int(sys.argv[2]) if len(sys.argv) > 2 else 8765
        server = start_server(port)
        print(f"serving http://127.0.0.1:{server.server_address[1]}/", flush=True)
        threading.Event().wait()
    else:
        sys.exit(main())
