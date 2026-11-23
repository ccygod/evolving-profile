"""Host tags for retained batches.

All hosts share one token-batch queue whose configured tags were written for
Codex (``retainTags: ["codex", ...]``), so Claude / WorkBuddy batches were
tagged ``codex`` and lacked ``host_family``.  Tags are now derived from the
batch's ``origin_agent`` values.
"""
from __future__ import annotations

HOST_ALIASES = ("codex", "claude", "workbuddy", "antigravity", "trae", "doubao", "openclaw", "hermes")


def host_family(origin: str) -> str:
    o = (origin or "").strip().lower()
    if o.startswith("claude"):
        return "claude"
    for fam in HOST_ALIASES:
        if o.startswith(fam):
            return fam
    return o or "unknown"


def retain_tags(base_tags: list[str], origin: str) -> list[str]:
    origins = [o.strip() for o in str(origin or "").split(",") if o.strip()] or ["unknown"]
    families = {host_family(o) for o in origins}
    out: list[str] = []
    for t in base_tags:
        # Bare host words in the shared config only apply to their own host.
        if t in HOST_ALIASES and t not in families:
            continue
        out.append(t)
    for o in origins:
        out.append(f"host:{o}")
    for fam in sorted(families):
        out.append(f"host_family:{fam}")
    seen: set[str] = set()
    return [t for t in out if not (t in seen or seen.add(t))]
