"""Slim debug/audit traces: store references, not copies.

Used by the Hook writer (``last_recall*.json``) and the process-memory
workspace writer (``relevance_audit``).  Full memory texts stay recoverable
from the Bank by id (``read_source``); the traces keep ids, scores, decisions
and short previews only.

Full-detail sampling (``FULL_TRACE_KEEP_RECENT`` / degraded recalls) is
documented on :func:`prepare_recall_state`.
"""

from __future__ import annotations

import gzip
import json
import os
import re
from typing import Any

SLIM_SCHEMA = "ep.trace-slim.v1"
FULL_TRACE_KEEP_RECENT = 3        # newest N recalls per session keep a full-detail (gzip) trace
PREVIEW_CHARS = 120
REASON_CHARS = 100
STRING_CHARS = 200
SCALAR_LIST_MAX = 50
OBJECT_LIST_MAX = 20
MAX_DEPTH = 6
WORKSPACE_DECISION_REASONS = 3
_BIG_ITEM_KEYS = ("candidate_items", "rejected_items", "deferred_items", "admission_decisions", "pipeline_stages")
_DEGRADED_OUTCOMES = {"failed", "failure", "degraded", "timeout", "error"}


def _clip(value: Any, limit: int) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[: limit - 1] + "…"
    return value


def _item_id(item: dict) -> str:
    return str(item.get("id") or item.get("chunk_id") or item.get("record_id") or item.get("process_memory_id") or "")[:120]


def _item_score(item: dict):
    if isinstance(item.get("score"), (int, float)):
        return item["score"]
    scores = item.get("scores")
    if isinstance(scores, dict):
        for key in ("final", "score", "rerank", "relevance"):
            if isinstance(scores.get(key), (int, float)):
                return scores[key]
    return None


def slim_item(item: dict, *, preview: bool = True) -> dict:
    """Reference form of one memory item: id, score, decision/reason, short preview."""
    admission = item.get("admission") if isinstance(item.get("admission"), dict) else {}
    out: dict = {"id": _item_id(item)}
    for key in ("type", "source", "document_id"):
        if isinstance(item.get(key), str) and item[key]:
            out[key] = _clip(item[key], 80)
    score = _item_score(item)
    if score is not None:
        out["score"] = score
    decision = admission.get("decision") or item.get("decision") or item.get("status")
    reason = admission.get("reason") or item.get("reason") or item.get("rejection_reason")
    if decision is not None:
        out["decision"] = _clip(str(decision), 40)
    if reason:
        out["reason"] = _clip(str(reason), REASON_CHARS)
    if preview:
        text = " ".join(str(item.get("text_preview") or item.get("preview") or item.get("text") or item.get("content") or "").split())
        if text:
            out["preview"] = _clip(text, PREVIEW_CHARS)
    return out


def slim_items(items, *, preview: bool = True) -> list:
    return [slim_item(i, preview=preview) if isinstance(i, dict) else i for i in (items or [])]


def _slim_value(value: Any, depth: int = 0) -> Any:
    """Generic structural slimming for pipeline stage payloads (counts/ids/timings kept)."""
    if isinstance(value, str):
        return _clip(value, STRING_CHARS)
    if isinstance(value, dict):
        if depth >= MAX_DEPTH:
            return {"_omitted_keys": len(value)}
        return {k: _slim_value(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        if value and all(isinstance(v, dict) for v in value):
            if any(_item_id(v) for v in value):
                rows = [slim_item(v) for v in value[:OBJECT_LIST_MAX]]
            else:
                rows = [_slim_value(v, depth + 1) for v in value[:OBJECT_LIST_MAX]]
            if len(value) > OBJECT_LIST_MAX:
                rows.append({"_truncated_count": len(value) - OBJECT_LIST_MAX})
            return rows
        rows = [_slim_value(v, depth + 1) for v in value[:SCALAR_LIST_MAX]]
        if len(value) > SCALAR_LIST_MAX:
            rows.append({"_truncated_count": len(value) - SCALAR_LIST_MAX})
        return rows
    return value


def is_degraded(state: dict) -> bool:
    outcome = str(state.get("outcome") or "").lower()
    execution = state.get("controller_execution") or {}
    return bool(
        outcome in _DEGRADED_OUTCOMES
        or state.get("retrieval_failure")
        or (isinstance(execution, dict) and execution.get("execution_complete") is False)
    )


def slim_recall_state(state: dict) -> dict:
    """Return the reference-only form of a last_recall state (readers' fields untouched)."""
    out = dict(state)
    if "candidate_items" in out:
        out["candidate_items"] = slim_items(out["candidate_items"])
    for key in ("rejected_items", "deferred_items"):
        if key in out:
            out[key] = [
                {k: v for k, v in slim_item(i, preview=False).items() if k in ("id", "decision", "reason", "score")}
                if isinstance(i, dict) else i
                for i in (out[key] or [])
            ]
    if "admission_decisions" in out:
        out["admission_decisions"] = [
            {k: _clip(v, REASON_CHARS) for k, v in d.items()
             if k in ("record_id", "record_type", "decision", "status", "reason", "decision_key")}
            if isinstance(d, dict) else d
            for d in (out["admission_decisions"] or [])
        ]
    if "pipeline_stages" in out:
        out["pipeline_stages"] = _slim_value(out["pipeline_stages"])
    return out


def _ring_paths(state_dir: str, name: str) -> list[str]:
    stem = name[:-5] if name.endswith(".json") else name
    return [os.path.join(state_dir, f"{stem}.trace-{i}.json.gz") for i in range(FULL_TRACE_KEEP_RECENT)]


def _write_ring(state_dir: str, name: str, state: dict) -> str | None:
    paths = _ring_paths(state_dir, name)

    def age_key(path: str):
        try:
            return os.stat(path).st_mtime_ns
        except OSError:
            return -1

    target = min(paths, key=age_key)
    tmp = target + ".tmp"
    try:
        with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=1) as stream:
            json.dump(state, stream, ensure_ascii=False)
        os.replace(tmp, target)
        return os.path.basename(target)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return None


def prepare_recall_state(name: str, state: dict, state_dir: str) -> dict:
    """Decide what a ``last_recall*`` write contains.

    Sampling rule:
      * degraded / failed recall           -> full detail inline (unchanged);
      * every other recall                 -> slim state inline, and (per-session file only)
        the full detail is written to one of ``FULL_TRACE_KEEP_RECENT`` rotating gzip
        slots ``last_recall-<sid>.trace-N.json.gz`` => the newest 3 recalls per session
        keep full detail; older ones are overwritten.
    """
    if not isinstance(state, dict) or not any(k in state for k in _BIG_ITEM_KEYS):
        return state
    if is_degraded(state):
        return {**state, "trace_detail": {"schema": SLIM_SCHEMA, "mode": "full_degraded"}}
    slim = slim_recall_state(state)
    detail = {"schema": SLIM_SCHEMA, "mode": "slim", "full_keep_recent": FULL_TRACE_KEEP_RECENT,
              "recover_texts_by": "read_source(id)"}
    if re.match(r"^last_recall-[0-9a-f]+\.json$", name):
        ref = _write_ring(state_dir, name, state)
        if ref:
            detail["full_trace_file"] = ref
    slim["trace_detail"] = detail
    return slim


# --- workspace relevance_audit -------------------------------------------------

def slim_relevance_audit(audit: Any) -> Any:
    """Per-candidate decision + score + id; drop repeated match_signals bodies.

    Kept decisions (kept=True) retain their full diagnostic record; excluded ones keep
    id, level, score, kept, exclusion_reason and the first reasons.  No reader consumes
    the stored audit (the live audit is recomputed on every page), but counts stay exact.
    """
    if not isinstance(audit, dict) or not isinstance(audit.get("decisions"), list):
        return audit
    if audit.get("trace_slim"):
        return audit
    rows = []
    for d in audit["decisions"]:
        if not isinstance(d, dict) or d.get("kept"):
            rows.append(d)
            continue
        row = {k: d[k] for k in ("id", "level", "score", "kept", "exclusion_reason") if k in d}
        reasons = d.get("reasons")
        if isinstance(reasons, list) and reasons != [d.get("exclusion_reason")]:
            row["reasons"] = reasons[:WORKSPACE_DECISION_REASONS]
        rows.append(row)
    out = dict(audit)
    out["decisions"] = rows
    out["trace_slim"] = {"schema": SLIM_SCHEMA, "decisions_total": len(rows),
                         "dropped": ["match_signals", "relationship", "scope_status", "evidence_role", "candidate_role", "temporal_role", "truth_status"],
                         "kept_decisions_full": True}
    return out
