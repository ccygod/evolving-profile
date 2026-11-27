"""Retire reviewed preferences that restate one another.

Publication adds a unit each time a rule is observed, so one correction the user
repeats ends up as several active units. They compete for the same slots at
selection and dilute each other's evidence. This pass groups units whose
corpus-centered embeddings are near-identical, keeps one, and marks the rest
superseded by it. Nothing is deleted: history and sources stay readable, and
each change is logged with the audit it replaced so it can be restored.

    python consolidate_units.py            # report only
    python consolidate_units.py --apply    # mark duplicates superseded
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

from semantic_recall import CENTERING_MIN_DOCUMENTS, _cosine, centered_vectors

STATE_ROOT = Path(os.environ.get("EVOLVING_PROFILE_STATE_ROOT", str(Path.home() / ".evolving-profile")))
CONFIG = STATE_ROOT / "guidance-v1" / "guidance-v1.json"
LOG = STATE_ROOT / "guidance-v1" / "consolidation-log.jsonl"
# Centered cosine at which two units are the same rule in different words. On
# the current registry every pair above this is a restatement; pairs around
# 0.6 share a topic but say different things, so the bound stays above them.
DUPLICATE_THRESHOLD = 0.70
_VALIDITY_RANK = {"stable": 2, "context_sensitive": 1}


def duplicate_threshold() -> float:
    try:
        value = json.loads((STATE_ROOT / "config" / "guidance-settings.json").read_text(encoding="utf-8"))
        return max(0.5, min(1.0, float(value.get("duplicate_unit_threshold", DUPLICATE_THRESHOLD))))
    except (OSError, ValueError, TypeError):
        return DUPLICATE_THRESHOLD


def reviewed(units: list[dict]) -> list[dict]:
    return [unit for unit in units if (unit.get("preference_audit") or {}).get("state") in {"approved", "restricted"}]


def duplicate_clusters(units: list[dict], vectors: list[list[float]], threshold: float) -> list[list[dict]]:
    """Connected groups of units whose pairwise centered cosine reaches the threshold.

    Returns no groups when the registry is too small to centre: raw cosine puts
    every pair near 1.0, which would read as "everything duplicates everything".
    """
    if len(vectors) < CENTERING_MIN_DOCUMENTS:
        return []
    centered = centered_vectors(vectors)
    parent = list(range(len(units)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for left in range(len(units)):
        for right in range(left + 1, len(units)):
            if _cosine(centered[left], centered[right]) >= threshold:
                parent[find(right)] = find(left)
    groups: dict[int, list[dict]] = {}
    for index, unit in enumerate(units):
        groups.setdefault(find(index), []).append(unit)
    return [group for group in groups.values() if len(group) > 1]


def choose_canonical(cluster: list[dict]) -> dict:
    """Keep the best-evidenced unit; prefer stable validity, then the newest."""
    def rank(unit: dict):
        audit = unit.get("preference_audit") or {}
        families = {ref.get("document_id") for ref in unit.get("evidence_refs") or [] if ref.get("document_id")}
        return (len(families), len(unit.get("evidence_refs") or []), _VALIDITY_RANK.get(audit.get("validity_kind"), 0),
                str(unit.get("activated_at") or ""), unit["id"])
    return max(cluster, key=rank)


def plan(units: list[dict], vectors: list[list[float]], threshold: float | None = None) -> list[dict]:
    result = []
    for cluster in duplicate_clusters(units, vectors, duplicate_threshold() if threshold is None else threshold):
        canonical = choose_canonical(cluster)
        result.append({"canonical": canonical, "duplicates": [unit for unit in cluster if unit["id"] != canonical["id"]]})
    return result


def apply(repo, plans: list[dict], log_path: Path | None = None) -> list[dict]:
    log_path = log_path or LOG  # resolved per call so a redirected LOG is honoured
    changes = []
    for item in plans:
        canonical = item["canonical"]
        for unit in item["duplicates"]:
            previous = unit.get("preference_audit") or {}
            repo.mark_unit_superseded(unit["id"], unit["revision"], superseded_by=canonical["id"],
                                      reason="semantic_duplicate_of_" + canonical["id"],
                                      authority="consolidation_pass_reviewed_duplicate")
            changes.append({"at": dt.datetime.now(dt.timezone.utc).isoformat(), "unit_id": unit["id"], "revision": unit["revision"],
                            "superseded_by": canonical["id"], "previous_audit": previous.get("audit") or
                            {key: previous.get(key) for key in ("state", "scope_level", "validity_kind", "reason")}})
    if changes:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            for change in changes:
                handle.write(json.dumps(change, ensure_ascii=False) + "\n")
    return changes


def run(*, apply_changes: bool, repo=None, service=None) -> dict:
    if repo is None:
        from mcp_runtime import load_repository
        repo = load_repository(CONFIG)
    if service is None:
        from semantic_recall import LocalGuidanceEmbeddingClient, SemanticRecallService
        base = os.getenv("EVOLVING_PROFILE_GUIDANCE_SEMANTIC_API_BASE", "http://127.0.0.1:12088")
        bank = os.getenv("EVOLVING_PROFILE_GUIDANCE_BANK_ID", "personal-memory")
        service = SemanticRecallService(
            LocalGuidanceEmbeddingClient(f"{base.rstrip('/')}/v1/default/banks/{bank}/internal/guidance-embeddings", timeout_seconds=20),
            os.getenv("EVOLVING_PROFILE_GUIDANCE_SEMANTIC_CACHE", str(STATE_ROOT / "guidance-v1" / "semantic-vectors.json")))
    units = reviewed(repo.active_units())
    plans = plan(units, service.cache.document_vectors(units)) if units else []
    changes = apply(repo, plans) if apply_changes else []
    return {"reviewed_units": len(units), "clusters": len(plans),
            "duplicates": sum(len(item["duplicates"]) for item in plans), "applied": len(changes),
            "plan": [{"keep": {"id": item["canonical"]["id"], "text": item["canonical"].get("text")},
                      "retire": [{"id": unit["id"], "text": unit.get("text")} for unit in item["duplicates"]]} for item in plans]}


def consolidate_if_changed(repo=None, service=None, state_path: Path | None = None) -> dict:
    """Worker entry: consolidate only when the reviewed set differs from last time.

    Best effort by contract. Any failure is reported in the result and never
    raised, so consolidation cannot block preference publication.
    """
    from hashlib import sha256
    state_path = state_path or STATE_ROOT / "guidance-v1" / "consolidation-state.json"
    try:
        if repo is None:
            from mcp_runtime import load_repository
            repo = load_repository(CONFIG)
        units = reviewed(repo.active_units())
        fingerprint = sha256(json.dumps(sorted((unit["id"], unit["revision"]) for unit in units)).encode()).hexdigest()
        try:
            if json.loads(state_path.read_text(encoding="utf-8")).get("fingerprint") == fingerprint:
                return {"status": "unchanged", "reviewed_units": len(units)}
        except (OSError, ValueError):
            pass
        report = run(apply_changes=True, repo=repo, service=service)
        remaining = reviewed(repo.active_units())
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({
            "fingerprint": sha256(json.dumps(sorted((unit["id"], unit["revision"]) for unit in remaining)).encode()).hexdigest(),
            "at": dt.datetime.now(dt.timezone.utc).isoformat(), "applied": report["applied"]}), encoding="utf-8")
        return {"status": "consolidated", **{key: report[key] for key in ("reviewed_units", "clusters", "duplicates", "applied")}}
    except Exception as error:
        return {"status": "degraded", "reason": type(error).__name__}


if __name__ == "__main__":
    print(json.dumps(run(apply_changes="--apply" in sys.argv[1:]), ensure_ascii=False, indent=2))
