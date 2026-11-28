#!/usr/bin/env python3
"""Official Agent Memory Leaderboard (AML / agentmemoryleaderboard.ai) HTTP adapter for Evolving Profile (EP).

Implements the standard AML participant contract:
  - GET  /health  -> liveness check
  - POST /add     -> idempotent synchronous memory ingestion (deduped by `request_id`)
  - POST /search  -> multi-facet EP recall + chronological `read_source` evidence return

Zero third-party dependencies (stdlib `http.server`), safe for local smoke testing or
exposing via a tunnel during official AML evaluation cycles.
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import re
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


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


def _bank_id_for_user(prefix: str, user_id: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(user_id or "default")).strip("-")
    return f"{prefix}-{safe[:48]}" if safe else f"{prefix}-default"


def _format_messages(messages: list[dict[str, Any]], session_id: str) -> tuple[str, str | None]:
    lines = [f"[Session: {session_id}]"]
    latest_ts: str | None = None
    for m in messages:
        role = str(m.get("role") or "user").capitalize()
        content = str(m.get("content") or "").strip()
        ts = m.get("timestamp")
        if ts is not None:
            try:
                ms = float(ts)
                if ms > 1e12:
                    ms /= 1000.0
                latest_ts = datetime.datetime.fromtimestamp(ms, tz=datetime.timezone.utc).isoformat()
            except Exception:
                if isinstance(ts, str) and ts.strip():
                    latest_ts = ts.strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines), latest_ts


class AMLState:
    def __init__(self, api_url: str, bank_prefix: str = "aml-ep", max_chars: int = 45000) -> None:
        self.api_url = api_url.rstrip("/")
        self.bank_prefix = bank_prefix
        self.max_chars = max_chars
        self._lock = threading.Lock()
        self._seen_requests: set[str] = set()
        self._doc_order: dict[str, dict[str, str]] = collections.defaultdict(dict)

    def add(self, body: dict[str, Any]) -> dict[str, Any]:
        req_id = str(body.get("request_id") or "")
        user_id = str(body.get("user_id") or "default")
        session_id = str(body.get("session_id") or "default")
        messages = body.get("messages") or []

        with self._lock:
            if req_id and req_id in self._seen_requests:
                return {"status": "ok", "request_id": req_id, "deduplicated": True}

        bid = _bank_id_for_user(self.bank_prefix, user_id)
        base = f"{self.api_url}/v1/default/banks/{bid}"
        content, iso_ts = _format_messages(messages, session_id)
        doc_id = req_id or f"{session_id}:{len(self._doc_order[bid])}"

        item: dict[str, Any] = {
            "content": content,
            "document_id": doc_id,
            "context": f"AML session {session_id}",
        }
        if iso_ts:
            item["timestamp"] = iso_ts

        _http_json("POST", f"{base}/memories", {"items": [item], "async": False}, timeout=240.0)

        with self._lock:
            if req_id:
                self._seen_requests.add(req_id)
            self._doc_order[bid][doc_id] = iso_ts or f"{len(self._doc_order[bid]):06d}"

        return {"status": "ok", "request_id": req_id, "bank_id": bid, "document_id": doc_id}

    def _read_source(self, base: str, doc_id: str) -> str:
        encoded = urllib.parse.quote(doc_id, safe="")
        try:
            resp = _http_json("GET", f"{base}/documents/{encoded}", timeout=30.0)
            return (resp.get("original_text") or resp.get("content") or resp.get("text") or "").strip()
        except Exception:
            return ""

    def search(self, body: dict[str, Any]) -> dict[str, Any]:
        user_id = str(body.get("user_id") or "default")
        query = str(body.get("query") or "")
        top_k = int(body.get("top_k") or body.get("limit") or 10)
        bid = _bank_id_for_user(self.bank_prefix, user_id)
        base = f"{self.api_url}/v1/default/banks/{bid}"

        facets = [
            query,
            f"user preferences, timeline updates, and supporting facts for: {query[:180]}",
        ]
        seen_ids: set[str] = set()
        recalled: list[dict[str, Any]] = []
        doc_scores: dict[str, float] = collections.defaultdict(float)

        for f_idx, fq in enumerate(facets):
            resp = _http_json(
                "POST",
                f"{base}/memories/recall",
                {"query": fq, "budget": "high", "max_tokens": 6000},
                timeout=90.0,
            )
            w = 1.5 if f_idx == 0 else 1.0
            for rank, item in enumerate(resp.get("results", [])):
                rid = str(item.get("id") or f"{f_idx}:{rank}")
                if rid not in seen_ids:
                    seen_ids.add(rid)
                    recalled.append(item)
                did = item.get("document_id") or item.get("source_document_id") or ""
                if did:
                    doc_scores[did] += w / (rank + 1.0)

        # Build bounded evidence list combining chronological read_source + distilled facts
        memories: list[dict[str, Any]] = []
        total_chars = 0

        top_dids = [d for d, _ in sorted(doc_scores.items(), key=lambda x: -x[1])[: max(1, min(top_k, 8))]]
        order_map = self._doc_order.get(bid, {})
        top_dids.sort(key=lambda d: order_map.get(d, d))
        for did in top_dids:
            txt = self._read_source(base, did)
            if txt and total_chars + len(txt) <= self.max_chars:
                memories.append({"id": did, "content": txt, "memory": txt, "score": round(doc_scores[did], 4)})
                total_chars += len(txt)

        for item in recalled:
            if len(memories) >= top_k:
                break
            txt = (item.get("text") or "").strip()
            if txt and total_chars + len(txt) <= self.max_chars:
                memories.append({
                    "id": str(item.get("id") or ""),
                    "content": txt,
                    "memory": txt,
                    "score": float(item.get("score") or 0.5),
                    "document_id": item.get("document_id"),
                })
                total_chars += len(txt)

        return {"status": "ok", "results": memories, "memories": memories, "bank_id": bid}


def make_handler(state: AMLState):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, data: dict[str, Any]) -> None:
            raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            if self.path.rstrip("/") == "/health":
                self._send(200, {"status": "ok", "service": "evolving-profile-aml-adapter"})
            else:
                self._send(404, {"error": "not_found"})

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            path = self.path.rstrip("/")
            try:
                if path in ("/add", "/v1/memories/add"):
                    self._send(200, state.add(body))
                elif path in ("/search", "/search/aml", "/v1/memories/search"):
                    self._send(200, state.search(body))
                else:
                    self._send(404, {"error": "not_found"})
            except Exception as exc:
                self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

        def log_message(self, fmt: str, *args: Any) -> None:
            pass

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=12110)
    parser.add_argument("--api-url", default=os.environ.get("EP_API_URL", "http://127.0.0.1:12088"))
    parser.add_argument("--bank-prefix", default="aml-ep")
    args = parser.parse_args()

    state = AMLState(api_url=args.api_url, bank_prefix=args.bank_prefix)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(state))
    print(f"EP AML Adapter listening on http://{args.host}:{args.port} -> EP API {args.api_url}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
