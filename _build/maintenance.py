#!/usr/bin/env python3
"""Maintenance mode for the GitHub Pages upload.

Run from the root of the directory that is about to be uploaded:

    MAINTENANCE_MODE=true  python3 _build/maintenance.py   # maintenance on
    MAINTENANCE_MODE=false python3 _build/maintenance.py   # normal site

When the switch is on, every public .html file is overwritten with
maintenance.html, except the KEEP allowlist below. Source pages in git are
never edited; only the build output changes. GitHub Pages serves both
/page and /page.html from page.html, so replacing the file covers both.

When the switch is off, maintenance.html is removed from the upload so the
live site is exactly what it was before this step existed.

Non-HTML files (assets/, .well-known/, robots.txt, sitemap.xml) are never
touched. GitHub Pages cannot send HTTP 503 or Retry-After, so maintenance
routes are served with status 200.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

MAINTENANCE_PAGE = "maintenance.html"

# Pages that stay reachable and byte-for-byte unchanged in maintenance mode.
KEEP = (
    "terms.html",               # /terms
    "privacy.html",             # /privacy
    "refund.html",              # /refund
    "tovael-privacy.html",      # /tovael-privacy, App Store privacy URL
    "auth/confirm/index.html",  # /auth/confirm, Tovael email confirmation
    "404.html",                 # GitHub Pages not-found page
    MAINTENANCE_PAGE,
)

# Directories that are not part of the upload (stripped later by deploy.yml).
SKIP_DIRS = {".git", ".github", "_archive", "_build", "_checks", "_templates"}


def is_on(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def public_html(root: Path) -> list[str]:
    found = []
    for path in sorted(root.rglob("*.html")):
        rel = path.relative_to(root)
        if rel.parts and rel.parts[0] in SKIP_DIRS:
            continue
        found.append(rel.as_posix())
    return found


def apply(root: Path, enabled: bool) -> list[str]:
    """Apply the switch to root. Returns the files replaced with maintenance."""
    page = root / MAINTENANCE_PAGE
    if not page.is_file():
        raise SystemExit(f"{MAINTENANCE_PAGE} is missing from {root}")
    if not enabled:
        page.unlink()
        return []
    for rel in KEEP:
        if not (root / rel).is_file():
            raise SystemExit(f"Allowlisted page {rel} is missing; refusing to build")
    body = page.read_bytes()
    replaced = []
    for rel in public_html(root):
        if rel in KEEP:
            continue
        (root / rel).write_bytes(body)
        replaced.append(rel)
    return replaced


def main() -> int:
    enabled = is_on(os.environ.get("MAINTENANCE_MODE"))
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    replaced = apply(root, enabled)
    if not enabled:
        print("MAINTENANCE_MODE is off: normal site, maintenance.html left out of the upload.")
        return 0
    print("MAINTENANCE_MODE is on.")
    print("Kept unchanged:")
    for rel in KEEP:
        print(f"  {rel}")
    print("Now showing the maintenance page (HTTP 200 on GitHub Pages):")
    for rel in replaced:
        print(f"  {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
