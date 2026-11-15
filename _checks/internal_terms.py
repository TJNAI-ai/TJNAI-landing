"""Hashed denylist for internal teammate names, plus internal-marker literals.

Same approach as sensitive_terms.py: names are stored only as SHA-256 hex digests of their lowercased form,
so this public repository never carries the plaintext list. Matching hashes every capitalized word (names
are proper nouns in prose; lowercase route or file names such as public page slugs are not matched).
Public site pages that present the AI team by name are product copy and are not scanned with this.

The two internal-marker strings are built from parts so that this checker does not trip its own scan.
"""

from __future__ import annotations

import hashlib
import re

# SHA-256 (hex) of each lowercased internal teammate name. Do not add a plaintext name here.
TEAMMATE_NAME_HASHES = frozenset(
    {
        "985c7c158744e7c82cc513417224949db3e77fda92e08714071a5a9a8f536542",
        "cc967443070ab409a57a455dc8c2405a10b6ba96ca75f87bcf18ca368bb090a8",
        "c7f6d322bc205f26de153999e8d923b63b9261ce34bcb0ef1b9c711f843e05d5",
        "7e5d4325a44714fc86a9b6989e41e966a7297bfe7e913c15fb9ab19588e33d61",
        "19e05df6b2e5fb94f3ee7eed2c02d340a1128a00231f5f6949641a143ab3b57a",
        "3910df2fbded45107cc2e45237d1b0270374d7ccc0d48de2c986885aa162e38b",
        "02b15ef87562676a8a42f33af9a0ca6aa11e491abda98cec24e4eded61ffa78d",
        "47612b3175fece07f6c3e91992412c5b16ca88a9068cb72fecbcf653eb5ffcd7",
        "4c0db82499ca9a6d65c396d1fbb8e77078e315bd8db0e9476a88cd244aa76ca6",
        "c512ca0c5c1e71be19f3356821e56732b5fb3ac894b0dbc5846d0b6920106fb3",
        "b1e3c2ec1a80a8ccdbcdb04b26a5896ea2c7ef8b3774dbb004bd3b1aaa195bec",
        "c4ddeffba8c2336a2af52d753c6079645d69db148800e2a79048e28196181b6e",
    }
)

# Internal markers that must never reach the public repo or a published page.
PRIVATE_PATH_MARKER = "/" + "workspace" + "/"
INTERNAL_MARKER = "INTERNAL" + ":"
INTERNAL_COMMENT_RE = re.compile(r"<!--\s*" + re.escape(INTERNAL_MARKER) + r".*?-->", re.S)

_CAP_WORD = re.compile(r"\b[A-Z][a-z]+\b")


def sha256_lower(term: str) -> str:
    return hashlib.sha256(term.lower().encode("utf-8")).hexdigest()


def teammate_name_hits(text: str, extra_hashes: frozenset[str] = frozenset()) -> list[str]:
    """Capitalized words in text whose lowercased SHA-256 is on the denylist (returned as hashes, not names)."""
    blocked = TEAMMATE_NAME_HASHES | extra_hashes
    return sorted({h for h in (sha256_lower(w) for w in _CAP_WORD.findall(text or "")) if h in blocked})


def marker_hits(text: str) -> list[str]:
    """Which internal markers (private path, INTERNAL marker) appear in text."""
    found = []
    if PRIVATE_PATH_MARKER in (text or ""):
        found.append("private path")
    if INTERNAL_MARKER in (text or ""):
        found.append("internal marker")
    return found
