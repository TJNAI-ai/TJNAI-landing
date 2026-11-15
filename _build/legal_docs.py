#!/usr/bin/env python3
"""The legal lane's documents (Markdown) -> repo source + rendered pages.

    python3 _build/legal_docs.py --import <private legal handoff directory>   # copy stripped sources into _build/legal_src/

Import applies ONLY the stripping rules in the legal lane's handoff (section 1: "Strip all internal material
before freezing a document: drafting notes in italics, internal-note blocks, 'Delete ... before use' source
notes and the change logs"). Nothing else in the text is changed:

  0. First of all, drop every internal-marker HTML comment block (multi-line, non-greedy), wherever it sits.
  1. Drop the trailing "## Drafting notes (internal ...)" / "## v0.2 change log (internal ...)" section
     (and the "---" rule just above it).
  2. Drop blockquote blocks that start with "> **INTERNAL NOTE".
  3. Drop whole-line italic drafting notes ("*Drafting note: ...*", "*(...)*", "*No arbitration ...*").
  4. Drop inline italic source notes "*(Source note, delete before use: ...)*".
  5. Drop the inline " per `<path>`; delete this reference before use" source reference.
  6. Drop whole lines (list items / clause lines) that say "Delete this source note before use".

After stripping, leak_hits() must find nothing: no private paths, no internal markers, no HTML comment
markers and no internal teammate names (checked against the hashed denylist in _checks/internal_terms.py).
The import refuses to write if it does, and _checks runs the same scan over every rendered legal page and
frozen copy.

The committed sources in _build/legal_src/ are what the site renders and hashes. _build is not uploaded to
Pages, but this repo is public on GitHub, so internal notes must never reach legal_src/.

render_main(markdown) turns the small Markdown subset the legal documents use (headings, paragraphs with hard
line breaks, bold, italic, inline code, -/1. lists with one nested level, tables, blockquotes, ---) into the
page body.
"""

from __future__ import annotations

import re
import sys
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "_build/legal_src"
sys.path.insert(0, str(ROOT / "_checks"))
from internal_terms import INTERNAL_COMMENT_RE, INTERNAL_MARKER, PRIVATE_PATH_MARKER, teammate_name_hits  # noqa: E402

# Version being imported and document_id -> source file name (legal lane handoff v1.0 §1, private handoff directory).
# Only these seven public documents are ever imported; the handoff, changelog, risk list and internal notes
# in the same folder are internal and must never be copied into this public repo.
VERSION = "v1.0"
DRAFTS = {
    "TOS": "terms-of-service_v1.0_2026-11-16.md",
    "PRIV": "privacy-policy_v1.0_2026-11-16.md",
    "REFUND": "refund-policy_v1.0_2026-11-16.md",
    "MSA": "MSA_v1.0_2026-11-16.md",
    "SOW-AUDIT": "SOW-AUDIT_v1.0_2026-11-16.md",
    "SOW-BUILD": "SOW-BUILD_v1.0_2026-11-16.md",
    "SOW-MAINTAIN": "SOW-MAINTAIN_v1.0_2026-11-16.md",
}

LEAK_PATTERNS = (
    ("private path", re.compile(re.escape(PRIVATE_PATH_MARKER.rstrip("/")))),
    ("internal marker", re.compile(re.escape(INTERNAL_MARKER))),
    ("<!--", re.compile(re.escape("<!--"))),
    ("-->", re.compile(re.escape("-->"))),
)
INTERNAL_COMMENT = INTERNAL_COMMENT_RE


def leak_hits(text: str, comments_ok: bool = False, extra_name_hashes: frozenset[str] = frozenset()) -> list[str]:
    """Internal-looking strings in text meant for the public: private paths, internal markers, teammate names.

    comments_ok=True is for rendered pages, whose template carries its own (non-internal) HTML comments;
    the whole file, comments included, is still scanned for every other pattern. Hits name the rule and the
    line number only, never the matched text, so CI logs of this public repo don't print a name."""
    hits = []
    for label, pattern in LEAK_PATTERNS:
        if comments_ok and label in ("<!--", "-->"):
            continue
        for m in pattern.finditer(text):
            hits.append(f"{label} at line {text.count(chr(10), 0, m.start()) + 1}")
    for lineno, line in enumerate(text.splitlines(), 1):
        for digest in teammate_name_hits(line, extra_name_hashes):
            hits.append(f"teammate name (sha256 {digest[:12]}) at line {lineno}")
    return hits


INTERNAL_MARKERS = ("INTERNAL NOTE", "INTERNAL TEAM RULE", "Drafting note", "delete before use", "Source note")


def strip_internal(md: str) -> str:
    # 0. internal-marker comment blocks, multi-line, non-greedy (handoff §1)
    md = INTERNAL_COMMENT.sub("", md)
    lines = md.splitlines()
    # 1. trailing internal section
    for i, line in enumerate(lines):
        if re.match(r"^## .*(drafting notes|change log)", line, flags=re.I):
            lines = lines[:i]
            while lines and lines[-1].strip() in ("", "---"):
                lines.pop()
            break
    out: list[str] = []
    skipping_quote = False
    for line in lines:
        # 2. INTERNAL NOTE blockquotes
        if line.startswith("> **INTERNAL NOTE"):
            skipping_quote = True
            continue
        if skipping_quote:
            if line.startswith(">"):
                continue
            skipping_quote = False
        # 3. whole-line italic drafting notes
        s = line.strip()
        if len(s) > 2 and s.startswith("*") and not s.startswith("**") and s.endswith("*") and not s.endswith("**"):
            continue
        # 6. whole source-note lines
        if re.search(r"delete this source note before use", line, flags=re.I):
            continue
        # 4. inline italic source notes
        line = re.sub(r"\s*\*\(Source note, delete before use:.*?\)\*", "", line)
        # 5. inline delete-this-reference source note
        line = re.sub(r" per `[^`]*`; delete this reference before use", "", line)
        out.append(line)
    # collapse runs of blank lines left behind
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip() + "\n"
    return text


def leftover_internal(md: str) -> list[str]:
    hits = []
    for line in md.splitlines():
        for marker in INTERNAL_MARKERS:
            if marker.lower() in line.lower() and "[Delete drafting notes and internal notes before use.]" not in line:
                hits.append(line[:120])
    return hits


def do_import(src: Path) -> int:
    SRC_DIR.mkdir(parents=True, exist_ok=True)
    for doc_id, name in DRAFTS.items():
        raw = (src / name).read_text(encoding="utf-8")
        clean = strip_internal(raw)
        left = leftover_internal(clean) + leak_hits(clean)
        if left:
            print(f"{doc_id}: internal markers left after stripping:", *left, sep="\n  ")
            return 1
        (SRC_DIR / f"{doc_id}-{VERSION}.md").write_text(clean, encoding="utf-8")
        print(f"{doc_id}: {len(raw.splitlines())} -> {len(clean.splitlines())} lines")
    return 0


# ---------------------------------------------------------------- rendering

def inline(text: str) -> str:
    codes: list[str] = []

    def stash(m: re.Match) -> str:
        codes.append(f"<code>{escape(m.group(1), quote=False)}</code>")
        return f"\x00{len(codes) - 1}\x00"

    text = re.sub(r"`([^`]*)`", stash, text)
    text = escape(text, quote=False)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], text)


def _cells(row: str) -> list[str]:
    row = row.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    return [c.strip() for c in row.split("|")]


def render_blocks(lines: list[str]) -> list[str]:
    html: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        s = line.strip()
        if not s:
            i += 1
            continue
        if s == "---":
            html.append("<hr>")
            i += 1
            continue
        m = re.match(r"^(#{1,3}) (.*)$", s)
        if m:
            level = len(m.group(1))
            html.append(f"<h{level}>{inline(m.group(2))}</h{level}>")
            i += 1
            continue
        if s.startswith(">"):
            quote = []
            while i < n and lines[i].strip().startswith(">"):
                quote.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            html.append('<blockquote>' + "".join(render_blocks(quote)) + "</blockquote>")
            continue
        if s.startswith("|"):
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append(lines[i])
                i += 1
            head = _cells(rows[0])
            body = [r for r in rows[1:] if not re.match(r"^\s*\|?\s*:?-{3,}", r)]
            t = ['<div class="table-wrap"><table>', "<thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr></thead><tbody>"]
            for r in body:
                t.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in _cells(r)) + "</tr>")
            t.append("</tbody></table></div>")
            html.append("".join(t))
            continue
        lm = re.match(r"^(\s*)([-*]|\d+\.) (.*)$", line)
        if lm and not line.startswith("**"):
            ordered = lm.group(2)[0].isdigit()
            tag = "ol" if ordered else "ul"
            start = lm.group(2)[:-1] if ordered else None
            items: list[list[str]] = []
            base = len(lm.group(1))
            while i < n:
                cur = lines[i]
                cm = re.match(r"^(\s*)([-*]|\d+\.) (.*)$", cur)
                if cm and len(cm.group(1)) <= base:
                    if (cm.group(2)[0].isdigit()) != ordered:
                        break
                    items.append([cm.group(3)])
                    i += 1
                elif cur.strip() and (cur.startswith(" ") or cur.startswith("\t")) and items:
                    items[-1].append(cur)
                    i += 1
                else:
                    break
            attr = f' start="{start}"' if ordered and start not in (None, "1") else ""
            parts = [f"<{tag}{attr}>"]
            for item in items:
                first, rest = item[0], item[1:]
                inner = inline(first)
                if rest:
                    sub = [r[min(len(r) - len(r.lstrip()), 4):] if r.strip() else r for r in rest]
                    inner += "".join(render_blocks([x.strip() if re.match(r"^\s*([-*]|\d+\.) ", x) else x for x in sub]))
                parts.append(f"<li>{inner}</li>")
            parts.append(f"</{tag}>")
            html.append("".join(parts))
            continue
        para = []
        while i < n:
            cur = lines[i]
            cs = cur.strip()
            if not cs or cs == "---" or re.match(r"^#{1,3} ", cs) or cs.startswith((">", "|")) or re.match(r"^([-*]|\d+\.) ", cs):
                break
            para.append(inline(cs))
            i += 1
        html.append("<p>" + "<br>\n".join(para) + "</p>")
    return html


def render_main(md: str) -> tuple[str, str]:
    """Returns (h1 html, body html). Each '## ' section is wrapped in a card."""
    lines = md.splitlines()
    h1 = ""
    if lines and lines[0].startswith("# "):
        h1 = inline(lines[0][2:])
        lines = lines[1:]
    sections: list[list[str]] = [[]]
    for line in lines:
        if line.startswith("## "):
            sections.append([line])
        else:
            sections[-1].append(line)
    out = []
    lead = render_blocks(sections[0])
    lead = [b for b in lead if b != "<hr>"]
    if lead:
        out.append('<div class="card doc-head">' + "\n".join(lead) + "</div>")
    for sec in sections[1:]:
        blocks = [b for b in render_blocks(sec) if b != "<hr>"]
        out.append('<div class="card">' + "\n".join(blocks) + "</div>")
    return h1, "\n\n    ".join(out)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--import":
        sys.exit(do_import(Path(sys.argv[2])))
    print(__doc__)
    sys.exit(2)
