#!/usr/bin/env python3
"""Release gate for turning MAINTENANCE_MODE off (run by deploy.yml; informational on pull requests).

Fails unless:
  1. every current legal document version is hashed and its source has no DRAFT banner or [bracket] placeholder;
  2. agree.html sends tjnai.ai acceptances to an https Worker endpoint;
  3. _build/release.json records the go-live sign-offs: paid checkout built and tested, legal review passed,
     and Tim's go-live approval (each with who/when). The file does not exist until those happen.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_SIGNOFFS = ("paid_checkout_tested", "legal_review_passed", "tim_go_live_approved")


def main() -> int:
    problems: list[str] = []
    registry = json.loads((ROOT / "_build/legal_versions.json").read_text(encoding="utf-8"))
    for doc in registry["documents"]:
        if not doc.get("enabled", True):
            continue
        version = doc["versions"][-1]
        label = f"{doc['document_id']}-{version['version']}"
        if not version.get("content_sha256"):
            problems.append(f"{label} has no content_sha256")
        if "source" in version:
            text = (ROOT / version["source"]).read_text(encoding="utf-8")
            if re.search(r"\bDRAFT\b", text):
                problems.append(f"{label} source still carries a DRAFT banner")
            if "[" in text:
                problems.append(f"{label} source still has a [bracket] placeholder")
    agree = (ROOT / "agree.html").read_text(encoding="utf-8")
    if not re.search(r"'tjnai\.ai':\s*'https://[^']+'", agree):
        problems.append("agree.html has no https acceptance endpoint for tjnai.ai")
    release = ROOT / "_build/release.json"
    if not release.is_file():
        problems.append("_build/release.json is missing (paid checkout, legal review and Tim's go-live sign-offs)")
    else:
        signoffs = json.loads(release.read_text(encoding="utf-8"))
        for key in REQUIRED_SIGNOFFS:
            entry = signoffs.get(key) or {}
            if entry.get("approved") is not True or not entry.get("by") or not entry.get("at"):
                problems.append(f"release.json: {key} is not signed off (needs approved, by, at)")
    if problems:
        print("Release gate: BLOCKED")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("Release gate: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
