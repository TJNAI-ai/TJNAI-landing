"""Hashed denylist for sensitive terms that must not appear in public content.

Terms are stored only as SHA-256 hex digests of their lowercased form. Matching
tokenizes text into words (letter/digit runs) and 1- to 3-word n-grams, lowercases
each gram, hashes it, and compares. This file is in _checks, which deploy.yml
strips before upload, but the repo is public, so plaintext terms stay out.
"""

from __future__ import annotations

import hashlib
import re

# SHA-256 (hex) of each lowercased sensitive term. Do not add a plaintext term here.
SENSITIVE_TERM_HASHES = frozenset(
    {
        "3c9adf8e28b3b59aa571d7b3f89ca86d9aab99b38d0d9fb79eef609f8416d490",
    }
)

_WORD = re.compile(r"[A-Za-z0-9]+")


def ngram_hashes(text: str, max_n: int = 3) -> set[str]:
    """SHA-256 hex digests of lowercased 1- to max_n-word grams in text."""
    words = [w.lower() for w in _WORD.findall(text)]
    out: set[str] = set()
    for n in range(1, max_n + 1):
        for i in range(0, len(words) - n + 1):
            gram = " ".join(words[i : i + n])
            out.add(hashlib.sha256(gram.encode("utf-8")).hexdigest())
    return out


def sensitive_hits(text: str) -> bool:
    """True if any 1- to 3-word gram in text matches a blocked hash."""
    if not text:
        return False
    return bool(ngram_hashes(text) & SENSITIVE_TERM_HASHES)


def sha256_lower(term: str) -> str:
    return hashlib.sha256(term.lower().encode("utf-8")).hexdigest()
