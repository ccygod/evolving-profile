"""Credential redaction applied before any text is retained into the Bank.

Users paste API keys straight into chat ("APIKEY 换成 sk-..."), and every write
path (hook token batches, the ledger bridge, imports) used to forward them
verbatim, so recall could hand a key to any agent.  This module is the single
redaction step shared by those paths.  It is pure and dependency-free; an
identical copy lives in ``host-adapter/lib/secret_redaction.py``.

Two kinds of rules:
- shape rules: tokens whose format alone identifies a credential (``sk-``,
  ``ghp_``, ``AKIA``, Slack, Google, PEM blocks, URL passwords);
- context rules: an opaque value (UUID-like or random) that directly follows a
  key word such as ``api key`` / ``token`` / ``密钥``.  The value must mix
  letters and digits, so ordinary words after "key" are left alone.
"""
from __future__ import annotations

import re

MARK = "[REDACTED]"

_SHAPE = [
    # Requires a digit so prose slugs such as "sk-learn-pipeline-guide" survive.
    ("sk", re.compile(r"(?<![A-Za-z0-9_-])sk-(?=[A-Za-z0-9._\-]*[0-9])[A-Za-z0-9][A-Za-z0-9._\-]{15,}")),
    ("ghp", re.compile(r"(?<![A-Za-z0-9_])gh[pousr]_[A-Za-z0-9]{20,}")),
    ("github_pat", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("akia", re.compile(r"(?<![A-Za-z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}")),
    ("slack", re.compile(r"xox[abposr]-[A-Za-z0-9-]{10,}")),
    ("google", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("pem", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
]
_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]{16,}")
_URL_PASSWORD = re.compile(r"((?:postgres(?:ql)?|mysql|redis|mongodb(?:\+srv)?|amqp)://[^:/@\s]+:)(?!\[REDACTED\]@)[^@\s]+(@)")
_CONTEXT = re.compile(
    r"(?i)(api[\s_-]?key|apikey|access[\s_-]?key|secret(?:[\s_-]?key)?|auth[\s_-]?token|token|密钥|秘钥|令牌|key)"
    r"((?:\s|[\"'\\])*(?:[:=：]|是|为|用|也用|换成|改成|改为)(?:\s|[\"'\\])*)"
    r"([A-Za-z0-9][A-Za-z0-9._\-]{19,})"
)
_NOT_SECRET_PREFIX = ("aHR0c", "file_", "data:", "local_", "toolu_", "call_", "msg_", "req_")


def _opaque(value: str) -> bool:
    """A credential-looking value: mixes digits and letters, and has no natural-word run."""
    if value.startswith(_NOT_SECRET_PREFIX) or "/" in value:
        return False
    digits = len(re.findall(r"[0-9]", value))
    letters = len(re.findall(r"[A-Za-z]", value))
    return digits >= 4 and letters >= 4 and not re.search(r"[a-z]{7,}", value)


def redact(text: str | None) -> tuple[str, dict[str, int]]:
    """Return (redacted_text, counts_by_rule)."""
    if not text:
        return text or "", {}
    counts: dict[str, int] = {}

    def bump(name: str, n: int) -> None:
        if n:
            counts[name] = counts.get(name, 0) + n

    for name, pat in _SHAPE:
        text, n = pat.subn(MARK, text)
        bump(name, n)
    text, n = _BEARER.subn(r"\1" + MARK, text)
    bump("bearer", n)
    text, n = _URL_PASSWORD.subn(r"\1" + MARK + r"\2", text)
    bump("url_password", n)

    def ctx(m: re.Match) -> str:
        if m.group(3) == MARK.strip("[]") or not _opaque(m.group(3)):
            return m.group(0)
        bump("context_key", 1)
        return m.group(1) + m.group(2) + MARK

    text = _CONTEXT.sub(ctx, text)
    return text, counts


def redact_text(text: str | None) -> str:
    return redact(text)[0]
