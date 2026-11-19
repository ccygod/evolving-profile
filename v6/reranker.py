"""EP v6.1 Bi-Temporal & Trust-Aware Retrieval Reranker."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def rerank_memories(
    items: list[dict[str, Any]],
    *,
    project_states: dict[str, dict[str, Any]] | None = None,
    now: datetime | None = None,
    half_life_days: float = 90.0,
) -> list[dict[str, Any]]:
    """Re-score memory candidates using bi-temporal decay, supersession, project status, and trust level."""
    ref_now = now or datetime.now(timezone.utc)
    states = project_states or {}
    scored = []

    for item in items:
        base = float(item.get("score") if item.get("score") is not None else 1.0)
        text = str(item.get("text") or "").lstrip()
        multiplier = 1.0
        reasons: list[str] = []

        # 1. Trust-level adjustment: down-rank unconfirmed assistant statements & demoted fragments
        meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        trust = item.get("trust_level") or meta.get("trust_level")
        if (
            trust == "assistant_unconfirmed"
            or meta.get("recall_demoted") is True
            or text.startswith(("Assistant statement (not user-confirmed)", "【助手·未确认", "Source repetition"))
        ):
            multiplier *= 0.45
            reasons.append("unconfirmed_assistant_downrank")
        elif trust == "user_explicit":
            multiplier *= 1.15
            reasons.append("user_explicit_boost")

        if meta.get("fragment_noise") is True:
            multiplier *= 0.50
            reasons.append("fragment_noise_downrank")

        # 2. Bi-temporal supersession penalty
        if item.get("superseded_by") or item.get("valid_to"):
            multiplier *= 0.35
            reasons.append("superseded_downrank")

        # 3. Project status alignment
        pkey = item.get("project_key")
        if pkey and pkey in states:
            pstatus = states[pkey].get("status")
            if pstatus == "active":
                multiplier *= 1.15
                reasons.append("active_project_boost")
            elif pstatus == "archived":
                multiplier *= 0.70
                reasons.append("archived_project_decay")

        # 4. Time decay (prefer occurred_at / occurred_start over mentioned_at)
        ts = _parse_iso(item.get("occurred_at") or item.get("occurred_start") or item.get("effective_at") or item.get("mentioned_at"))
        if ts is not None and half_life_days > 0:
            age_days = max(0.0, (ref_now - ts).total_seconds() / 86400.0)
            decay = 0.5 + 0.5 * math.exp(-math.log(2.0) * age_days / half_life_days)
            multiplier *= decay

        final_score = round(base * multiplier, 6)
        scored.append({**item, "rerank_score": final_score, "rerank_reasons": reasons})

    scored.sort(key=lambda r: r["rerank_score"], reverse=True)
    return scored
