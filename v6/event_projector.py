"""EP v6 Event-Bus Consumer: Semantic Ingestion Gate + Bi-Temporal Entity/Project State Projector."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any


_ACK_ONLY = frozenset({"好", "好的", "可以", "收到", "谢谢", "嗯", "行", "ok", "okay", "yes", "thanks"})
_DECISION_MARKERS = ("以后", "不要", "禁止", "必须", "默认", "改成", "改为", "不要再", "记住", "规则", "决定", "换成")


@dataclass(frozen=True)
class GateDecision:
    plane: str  # memory_high_priority | memory_standard | work_ledger_only | drop
    reason: str
    trust_level: str
    content_sha256: str


def classify_event_for_memory(event: dict[str, Any]) -> GateDecision:
    """Route normalized ep_events rows to the right storage plane before any LLM spend."""
    ev_type = str(event.get("event_type") or "")
    role = str(event.get("role") or "")
    body = str(event.get("body") or "").strip()
    digest = hashlib.sha256(" ".join(body.split()).encode("utf-8")).hexdigest()

    if ev_type in ("tool_call", "tool_result", "session_start", "turn_end", "compaction"):
        return GateDecision("work_ledger_only", "structural_process_event", "system", digest)

    if not body:
        return GateDecision("drop", "empty_body", "none", digest)

    if role == "assistant":
        # Procedural assistant output stays in work_ledger; never inflates memory_units unconfirmed.
        return GateDecision("work_ledger_only", "assistant_output_ledger_only", "assistant_unconfirmed", digest)

    if role == "user":
        normalized = body.lower().strip("。！？!,.，? ")
        if normalized in _ACK_ONLY or body.lstrip().startswith("<external_"):
            return GateDecision("work_ledger_only", "acknowledgment_or_injected_wrapper", "user_non_substantive", digest)
        if any(marker in body for marker in _DECISION_MARKERS):
            return GateDecision("memory_high_priority", "explicit_user_rule_or_decision", "user_explicit", digest)
        return GateDecision("memory_standard", "substantive_user_input", "user_explicit", digest)

    return GateDecision("work_ledger_only", "other_role", "unknown", digest)


def project_bi_temporal_state(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Pure in-memory bi-temporal state projector from ordered event stream (testable without DB)."""
    projects: dict[str, dict[str, Any]] = {}
    for ev in events:
        pkey = str(ev.get("project_key") or "(unassigned)")
        ts = ev.get("occurred_at")
        host = str(ev.get("host") or "unknown")
        sid = str(ev.get("session_id") or "")
        st = projects.setdefault(
            pkey,
            {
                "project_key": pkey,
                "hosts": set(),
                "sessions": set(),
                "event_count": 0,
                "first_seen_at": ts,
                "last_active_at": ts,
                "latest_user_request": "",
                "memory_candidates": 0,
                "valid_from": ts,
                "valid_to": None,
            },
        )
        st["hosts"].add(host)
        if sid:
            st["sessions"].add(sid)
        st["event_count"] += 1
        if ts and (not st["first_seen_at"] or ts < st["first_seen_at"]):
            st["first_seen_at"] = ts
            st["valid_from"] = ts
        if ts and (not st["last_active_at"] or ts > st["last_active_at"]):
            st["last_active_at"] = ts
        decision = classify_event_for_memory(ev)
        if decision.plane in ("memory_high_priority", "memory_standard"):
            st["memory_candidates"] += 1
            st["latest_user_request"] = re.sub(r"\s+", " ", str(ev.get("body") or ""))[:300]

    return {
        k: {
            **v,
            "hosts": sorted(v["hosts"]),
            "session_count": len(v["sessions"]),
        }
        for k, v in projects.items()
    }
