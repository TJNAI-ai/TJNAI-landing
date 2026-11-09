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
import subprocess
import sys
import tempfile
import threading
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

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
}

# Product names may remain here because they are the legal or customer-access
# text this run is not allowed to rewrite. The scan still prints the matches.
TEXT_EXCEPTIONS = {
    "terms.html": "Legal substance names Gumroad digital products and apps that keep their own terms.",
    "refund.html": "Legal substance names Gumroad and the PickleRecord Coach Court Kit.",
    "tovael-privacy.html": "Required Tovael privacy policy for the App Store listing and the shipped app.",
    "auth/confirm/index.html": "Required Tovael email-confirmation fallback.",
}

# Google's own booking-link rules, quoted in the services blog. Not a TJNAI app offer.
ALLOWED_QUOTES = (
    "an app store link or a link shortener",
    "app store links or link shorteners",
)

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
    r"nordstrom",
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
    "privacy.html": "5d9515c6a5a04840d86ccb319405b92080d0f2a444fcb3cc168ab3b2d4284314",
    "terms.html": "c3808015d84c6a19e2dd24457ceb3d53339d0e88ec6c5090490fead4e0be0c06",
    "refund.html": "2b9b5cc01393e352f4c1bc86bfc8f2b79b6ba83ea09bf1d490e3194e2a470073",
    "tovael-privacy.html": "a8bbf830bf62a33e1f6f782bf7abbd4c0e26466ad29edfdc896c96c8e74f188c",
    "auth/confirm/index.html": "e9599814996cb87f64e8d019862378ca26c7a68936e074a03e2327454099005f",
    ".well-known/apple-app-site-association": "c34d360fab779a14a868f3f0dba1d4eb6cf528670e4d61b7b9d8c47ae008d0f3",
    ".well-known/security.txt": "9c14cc452ddf1885d2aef45b69c44930bc97285cdd5043fd253799a99bd24f1b",
}
UNCHANGED = tuple(UNCHANGED_SHA256)
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
    "tovael-privacy.html",
    "auth/confirm/index.html",
    "404.html",
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


def url_to_file(path: str) -> Path | None:
    path = unquote(path.split("?", 1)[0])
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    rel = path.lstrip("/")
    candidates = [Path(rel), Path(rel + ".html"), Path(rel) / "index.html"] if rel else [Path("index.html")]
    if not rel:
        candidates = [Path("index.html")]
    for candidate in candidates:
        if (ROOT / candidate).is_file():
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


def try_git_text(rel: str) -> str | None:
    """Baseline from origin/main when that ref is present.

    actions/checkout is shallow and often has no origin/main. Callers must
    still enforce prices, the omitted offer, and the pinned legal hashes.
    """
    try:
        return subprocess.check_output(
            ["git", "show", f"origin/main:{rel}"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
        ).decode("utf-8")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def check_inventory() -> None:
    found = {path.relative_to(ROOT).as_posix() for path in ROOT.rglob("*.html")}
    expected = set(PUBLIC_HTML) | {"_templates/post-template.html"}
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
    for rel, kind in PUBLIC_HTML.items():
        if rel in TEXT_EXCEPTIONS:
            continue
        text = scanned_text(rel)
        for pattern in PROMO_PATTERNS:
            if re.search(pattern, text, flags=re.I):
                fail(f"{rel} ({kind}) still matches /{pattern}/")
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
    baseline_team = try_git_text("team.html")
    if baseline_team is not None:
        current_main = team[team.index("<main"):team.index("</main>")]
        previous_main = baseline_team[baseline_team.index("<main"):baseline_team.index("</main>")]
        if current_main != previous_main:
            fail("team.html main changed; /team people must stay as published")


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
        shutil.copytree(ROOT, dest, ignore=shutil.ignore_patterns(".git"))
        unrelated = dest / "assets/products/unrelated-future-asset.bin"
        unrelated.write_bytes(b"keep")
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
    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path or "/"
        found = url_to_file(path)
        if found is None:
            body = (ROOT / "404.html").read_bytes()
            self.send_response(404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = (ROOT / found).read_bytes()
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


def start_server(port: int = 0) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
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

    for path in ("/", "/services", "/privacy", "/terms", "/refund", "/tovael-privacy", "/auth/confirm", "/newsletter"):
        status, location, body = fetch(path)
        if status != 200 or location:
            fail(f"{path} returned {status} location={location!r}; expected 200 and no redirect")
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


def main() -> int:
    check_inventory()
    check_promo()
    check_links()
    check_canonical_sitemap_schema()
    check_prices_and_services_body()
    check_unchanged_and_assets()
    check_active_previews()
    check_home_and_roster()
    check_deploy_artifact()
    check_newsletter()
    check_removed_anchors()
    server = start_server()
    try:
        check_404(server)
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
