"""Official Agent Memory Benchmark (AMB / agentmemorybenchmark.ai) provider for Evolving Profile (EP).

Drop-in `MemoryProvider` compatible with `vectorize-io/agent-memory-benchmark`
(`src/memory_bench/memory/evolving_profile.py`).

Uses EP's production architecture:
  1. Per-unit isolated banks (`amb-ep-<unit_id>`)
  2. Timestamped document ingestion (`POST /v1/default/banks/{bank_id}/memories`)
  3. Multi-facet recall + chronological `read_source` original-document readback
     (`GET /v1/default/banks/{bank_id}/documents/{document_id}`)
"""
from __future__ import annotations

import collections
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

try:
    from memory_bench.memory.base import MemoryProvider
    from memory_bench.models import Document
except ImportError:
    # Standalone fallback so the module can also be imported & tested inside ep-v6
    from dataclasses import dataclass, field

    @dataclass
    class Document:  # type: ignore[no-redef]
        id: str
        content: str
        metadata: dict[str, Any] = field(default_factory=dict)

    class MemoryProvider:  # type: ignore[no-redef]
        name: str = ""
        description: str = ""
        kind: str = "local"


def _http_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 180.0,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw.strip() else {}


def _safe_bank_id(prefix: str, unit_id: str | None) -> str:
    raw = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(unit_id or "default")).strip("-")
    return f"{prefix}-{raw[:48]}" if raw else f"{prefix}-default"


class EvolvingProfileProvider(MemoryProvider):
    """Evolving Profile (EP 6.2+) provider with multi-facet recall & chronological read_source."""

    name = "evolving-profile"
    description = (
        "Local-first AI memory, 5D conditional preferences & reusable agent experience "
        "control plane (built on Hindsight) with multi-facet recall and read_source readback."
    )
    kind = "local"
    provider = "evolving-profile"
    variant = "local-readback"
    link = "https://github.com/ccygod/evolving-profile"
    concurrency = 2
    supports_filters = False

    def __init__(
        self,
        *,
        api_url: str | None = None,
        budget: str = "high",
        max_tokens: int = 6000,
        bank_prefix: str = "amb-ep",
        use_readback: bool = True,
    ) -> None:
        self.api_url = (api_url or os.environ.get("EP_API_URL") or "http://127.0.0.1:12088").rstrip("/")
        self.budget = budget
        self.max_tokens = max_tokens
        self.bank_prefix = bank_prefix
        self.use_readback = use_readback
        self._created_banks: set[str] = set()
        self._doc_order: dict[str, dict[str, str]] = collections.defaultdict(dict)

    def _bank_url(self, user_id: str | None) -> tuple[str, str]:
        bid = _safe_bank_id(self.bank_prefix, user_id)
        return bid, f"{self.api_url}/v1/default/banks/{bid}"

    def prepare(self, store_dir: Path, unit_ids: set[str] | None = None, reset: bool = True) -> None:
        if reset and unit_ids:
            for uid in unit_ids:
                bid, base = self._bank_url(uid)
                try:
                    _http_json("DELETE", base, timeout=30.0)
                except Exception:
                    pass
                self._created_banks.discard(bid)
                self._doc_order.pop(bid, None)

    def ingest(self, documents: list[Document]) -> None:
        by_user: dict[str, list[Document]] = collections.defaultdict(list)
        for doc in documents:
            uid = str((doc.metadata or {}).get("user_id") or "default")
            by_user[uid].append(doc)

        for uid, docs in by_user.items():
            bid, base = self._bank_url(uid)
            self._created_banks.add(bid)
            items = []
            for idx, doc in enumerate(docs):
                meta = doc.metadata or {}
                ts = meta.get("timestamp") or meta.get("date")
                item: dict[str, Any] = {
                    "content": doc.content,
                    "document_id": doc.id,
                    "context": str(meta.get("context") or f"Document {doc.id}"),
                }
                if ts:
                    item["timestamp"] = str(ts)
                items.append(item)
                self._doc_order[bid][doc.id] = str(ts or f"{idx:06d}")

            for i in range(0, len(items), 3):
                _http_json(
                    "POST",
                    f"{base}/memories",
                    {"items": items[i : i + 3], "async": False},
                    timeout=300.0,
                )

    def _read_source(self, base: str, doc_id: str) -> str:
        encoded = urllib.parse.quote(doc_id, safe="")
        try:
            resp = _http_json("GET", f"{base}/documents/{encoded}", timeout=30.0)
            return (resp.get("original_text") or resp.get("content") or resp.get("text") or "").strip()
        except Exception:
            return ""

    def retrieve(
        self,
        query: str,
        k: int = 10,
        user_id: str | None = None,
        query_timestamp: str | None = None,
        filters: dict | None = None,
    ) -> tuple[list[Document], dict | None]:
        bid, base = self._bank_url(user_id)
        facets = [
            query,
            f"timeline, preferences, updates, and supporting facts related to: {query[:180]}",
        ]
        seen_ids: set[str] = set()
        recalled: list[dict[str, Any]] = []
        doc_scores: dict[str, float] = collections.defaultdict(float)

        for f_idx, fq in enumerate(facets):
            payload: dict[str, Any] = {
                "query": fq,
                "budget": self.budget,
                "max_tokens": self.max_tokens,
            }
            if query_timestamp:
                payload["query_timestamp"] = query_timestamp
            resp = _http_json("POST", f"{base}/memories/recall", payload, timeout=90.0)
            w = 1.5 if f_idx == 0 else 1.0
            for rank, item in enumerate(resp.get("results", [])):
                rid = str(item.get("id") or f"{f_idx}:{rank}")
                if rid not in seen_ids:
                    seen_ids.add(rid)
                    recalled.append(item)
                did = item.get("document_id") or item.get("source_document_id") or ""
                if did:
                    doc_scores[did] += w / (rank + 1.0)

        out_docs: list[Document] = []
        if self.use_readback and doc_scores:
            top_dids = [d for d, _ in sorted(doc_scores.items(), key=lambda x: -x[1])[: max(1, min(k, 8))]]
            order_map = self._doc_order.get(bid, {})
            top_dids.sort(key=lambda d: order_map.get(d, d))
            for did in top_dids:
                txt = self._read_source(base, did)
                if txt:
                    out_docs.append(
                        Document(
                            id=did,
                            content=txt,
                            metadata={"source": "ep_read_source", "bank_id": bid},
                        )
                    )

        # Append distilled memory highlights up to k
        for item in recalled:
            if len(out_docs) >= k:
                break
            txt = (item.get("text") or "").strip()
            if txt:
                out_docs.append(
                    Document(
                        id=str(item.get("id") or ""),
                        content=txt,
                        metadata={
                            "source": "ep_recall",
                            "document_id": item.get("document_id"),
                            "occurred_start": item.get("occurred_start"),
                        },
                    )
                )

        return out_docs[:k], {"bank_id": bid, "recalled_count": len(recalled), "readback_docs": len(out_docs)}

    def cleanup(self) -> None:
        for bid in list(self._created_banks):
            try:
                _http_json("DELETE", f"{self.api_url}/v1/default/banks/{bid}", timeout=30.0)
            except Exception:
                pass
            self._created_banks.discard(bid)
