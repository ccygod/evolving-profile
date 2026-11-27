"""Find the parts of a user-role message that the user supplied but did not say.

A user turn often carries material next to the user's own words: a pasted
document, a spreadsheet dump, command output, another agent's report. Stored as
a "user statement" it becomes independent user testimony, and later stages count
it as evidence of what the user believes or prefers. The spans returned here are
treated like quotations: still stored and retrievable, but not testimony.

Only structure is used, never topic or wording:
  * fenced code blocks, complete ``<tag>...</tag>`` blocks, table and
    tab-separated rows are material wherever they appear;
  * in a long, many-line message the body is a paste, and only a short opening
    and closing paragraph are kept as the user's own words.
"""
from __future__ import annotations

import re

# A typed or dictated message is rarely both this long and this many lines;
# dictation is long but arrives as one or two lines, a paste is long and tall.
LONG_MESSAGE_CHARS = 1500
LONG_MESSAGE_LINES = 15
# In such a message the user's own request is a short paragraph at either end.
OWN_WORDS_PARAGRAPH_CHARS = 300

_FENCE = re.compile(r"(?ms)^[ \t]*(```|~~~).*?^[ \t]*\1[ \t]*$")
_ELEMENT = re.compile(r"(?s)<([A-Za-z][\w.-]*)(?:\s[^<>]*)?>.*?</\1>")
_ROW = re.compile(r"(?m)^(?:[ \t]*\|.*\|[ \t]*|[^\n\t]*\t[^\n]*)$")
_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n")


def _merge(ranges: list[list[int]]) -> list[list[int]]:
    merged: list[list[int]] = []
    for start, end in sorted(r for r in ranges if r[1] > r[0]):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def _paragraphs(text: str) -> list[tuple[int, int]]:
    spans, start = [], 0
    for match in _PARAGRAPH_BREAK.finditer(text):
        spans.append((start, match.start()))
        start = match.end()
    spans.append((start, len(text)))
    return [(a, b) for a, b in spans if text[a:b].strip()]


def supplied_material_ranges(text: str) -> list[list[int]]:
    """Character ranges of ``text`` that are supplied material, merged and sorted."""
    value = str(text or "")
    ranges = [[m.start(), m.end()] for pattern in (_FENCE, _ELEMENT) for m in pattern.finditer(value)]
    ranges += [[m.start(), m.end()] for m in _ROW.finditer(value) if m.group(0).strip()]
    if len(value) >= LONG_MESSAGE_CHARS and value.count("\n") + 1 >= LONG_MESSAGE_LINES:
        paragraphs = _paragraphs(value)
        own = set()
        for index in (0, len(paragraphs) - 1):
            if paragraphs and paragraphs[index][1] - paragraphs[index][0] <= OWN_WORDS_PARAGRAPH_CHARS:
                own.add(index)
        ranges += [[a, b] for index, (a, b) in enumerate(paragraphs) if index not in own]
    return _merge(ranges)
