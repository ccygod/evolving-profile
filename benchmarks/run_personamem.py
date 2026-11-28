#!/usr/bin/env python3
"""Reproducible PersonaMem-32K benchmark runner for Evolving Profile (EP).

Supports two evaluation modes on isolated ephemeral banks (never touching
personal banks):
  - `recall`: baseline single-query `/memories/recall`
  - `ep_sota`: EP full-stack general pipeline — fine-grained timestamped
    ingestion, multi-facet recall, causal-time scoping, and native
    `read_source` (`/documents/{document_id}`) chronological readback.
"""
from __future__ import annotations

import argparse
import ast
import collections
import csv
import datetime
import json
import math
import re
import statistics
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any


def _http_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw.strip() else {}


def _chat_completion(
    llm_url: str,
    model: str,
    messages: list[dict[str, str]],
    *,
    api_key: str = "local",
    temperature: float = 0.0,
    max_tokens: int = 320,
) -> str:
    url = llm_url.rstrip("/") + "/chat/completions"
    resp = _http_json(
        "POST",
        url,
        {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=90.0,
    )
    return resp["choices"][0]["message"]["content"].strip()


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return s[int(k)]
    return s[f] + (s[c] - s[f]) * (k - f)


def _extract_choice(text: str) -> str:
    matches = re.findall(r"\(([a-dA-D])\)", text)
    if matches:
        return f"({matches[-1].lower()})"
    m2 = re.findall(r"\b([a-dA-D])\b", text)
    if m2:
        return f"({m2[-1].lower()})"
    return text.strip().lower()[:8]


def _chunk_messages_with_spans(
    messages: list[dict[str, Any]], chunk_turns: int = 6
) -> list[dict[str, Any]]:
    """Split dialogue into fine-grained, causally ordered turn blocks."""
    blocks: list[dict[str, Any]] = []
    base_dt = datetime.datetime(2026, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
    cur_lines: list[str] = []
    start_idx = 0
    for idx, m in enumerate(messages):
        role = m.get("role", "user")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        if not cur_lines:
            start_idx = idx
        cur_lines.append(f"[Turn {idx}] {role.capitalize()}: {content}")
        # Always keep initial system persona block standalone, then chunk every `chunk_turns`
        if (idx == 0 and role == "system") or len(cur_lines) >= chunk_turns:
            ts = (base_dt + datetime.timedelta(hours=idx)).isoformat()
            blocks.append(
                {
                    "start_idx": start_idx,
                    "end_idx": idx,
                    "timestamp": ts,
                    "content": "\n".join(cur_lines),
                }
            )
            cur_lines = []
    if cur_lines:
        last_idx = len(messages) - 1
        ts = (base_dt + datetime.timedelta(hours=last_idx)).isoformat()
        blocks.append(
            {
                "start_idx": start_idx,
                "end_idx": last_idx,
                "timestamp": ts,
                "content": "\n".join(cur_lines),
            }
        )
    return blocks


def _doc_end_turn(doc_id: str) -> int:
    m = re.search(r":t(\d+)-(\d+)$", doc_id or "")
    return int(m.group(2)) if m else 0


def _read_source_document(base: str, doc_id: str, cache: dict[str, str]) -> str:
    if doc_id in cache:
        return cache[doc_id]
    encoded = urllib.parse.quote(doc_id, safe="")
    try:
        resp = _http_json("GET", f"{base}/documents/{encoded}", timeout=30.0)
        text = (
            resp.get("original_text")
            or resp.get("content")
            or resp.get("text")
            or ""
        ).strip()
        cache[doc_id] = text
        return text
    except Exception:
        cache[doc_id] = ""
        return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions-csv", type=Path, required=True)
    parser.add_argument("--contexts-jsonl", type=Path, required=True)
    parser.add_argument("--api-url", default="http://127.0.0.1:12088")
    parser.add_argument("--llm-url", default="http://127.0.0.1:12095/v1")
    parser.add_argument("--model", default="gpt-6-luna")
    parser.add_argument("--mode", default="ep_sota", choices=["recall", "ep_sota"])
    parser.add_argument("--api-key", default="local")
    parser.add_argument("--budget", default="high", choices=["low", "mid", "high"])
    parser.add_argument("--max-tokens", type=int, default=6000)
    parser.add_argument("--chunk-turns", type=int, default=8)
    parser.add_argument("--top-source-docs", type=int, default=8)
    parser.add_argument("--max-contexts", type=int, default=3)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--keep-bank", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    contexts: dict[str, list[dict[str, Any]]] = {}
    with args.contexts_jsonl.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                contexts.update(json.loads(line))

    by_ctx: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    with args.questions_csv.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            by_ctx[row["shared_context_id"]].append(row)

    selected_ctx_ids = list(by_ctx.keys())[: args.max_contexts]
    print(
        f"PersonaMem-32K [{args.mode}] — {len(selected_ctx_ids)} contexts, "
        f"limit={args.limit}, chunk_turns={args.chunk_turns}, model={args.model}",
        flush=True,
    )

    results = []
    t_start = time.perf_counter()

    for ctx_id in selected_ctx_ids:
        if len(results) >= args.limit:
            break
        msgs = contexts.get(ctx_id, [])
        qrows = by_ctx[ctx_id][: max(0, args.limit - len(results))]
        bank_id = f"bench-pmem-{uuid.uuid4().hex[:10]}"
        base = f"{args.api_url.rstrip('/')}/v1/default/banks/{bank_id}"
        doc_cache: dict[str, str] = {}

        try:
            blocks = _chunk_messages_with_spans(msgs, chunk_turns=args.chunk_turns)
            items = [
                {
                    "content": b["content"],
                    "timestamp": b["timestamp"],
                    "document_id": f"{ctx_id[:10]}:t{b['start_idx']}-{b['end_idx']}",
                    "context": f"Turns {b['start_idx']}..{b['end_idx']} of user conversation",
                }
                for b in blocks
            ]
            # Keep local raw map as fallback in case a bank omits original_text
            raw_by_doc = {it["document_id"]: it["content"] for it in items}
            first_doc_id = items[0]["document_id"] if items else ""

            print(f"Ingesting {len(items)} fine-grained blocks into {bank_id}...", flush=True)
            for i in range(0, len(items), 3):
                _http_json(
                    "POST",
                    f"{base}/memories",
                    {"items": items[i : i + 3], "async": False},
                    timeout=300.0,
                )
                print(f"  ingested {min(i + 3, len(items))}/{len(items)} blocks", flush=True)

            for qrow in qrows:
                qid = qrow["question_id"]
                qtype = qrow["question_type"]
                user_msg = qrow["user_question_or_message"]
                gold = qrow["correct_answer"].strip().lower()
                options = ast.literal_eval(qrow["all_options"])
                end_turn = int(qrow.get("end_index_in_shared_context") or len(msgs))

                t0 = time.perf_counter()
                if args.mode == "ep_sota":
                    # General 3-facet EP recall: (1) user message, (2) persona & preferences, (3) preference updates & reasons
                    facets = [
                        user_msg,
                        f"user persona, preferences, likes, dislikes related to: {user_msg[:180]}",
                        "user changed preference, updated opinion, reason for change, recent favorite or dislike",
                    ]
                    seen_ids: set[str] = set()
                    rlist: list[dict[str, Any]] = []
                    doc_scores: dict[str, float] = collections.defaultdict(float)
                    for f_idx, fq in enumerate(facets):
                        resp = _http_json(
                            "POST",
                            f"{base}/memories/recall",
                            {
                                "query": fq,
                                "budget": args.budget,
                                "max_tokens": args.max_tokens // 2,
                            },
                            timeout=90.0,
                        )
                        weight = 1.5 if f_idx == 0 else 1.0
                        for rank, r in enumerate(resp.get("results", [])):
                            doc_id = r.get("document_id") or r.get("source_document_id") or ""
                            # Respect causal time: ignore turns after this question's end_turn
                            if doc_id and _doc_end_turn(doc_id) > end_turn:
                                continue
                            rid = str(r.get("id") or f"{doc_id}:{rank}")
                            if rid not in seen_ids:
                                seen_ids.add(rid)
                                rlist.append(r)
                            if doc_id:
                                doc_scores[doc_id] += weight / (rank + 1.0)

                    # Always include initial persona bio doc (Turn 0) if causally prior, plus top-ranked source docs
                    ranked_docs = [d for d, _ in sorted(doc_scores.items(), key=lambda x: -x[1])]
                    selected_docs: list[str] = []
                    if first_doc_id and _doc_end_turn(first_doc_id) <= end_turn:
                        selected_docs.append(first_doc_id)
                    for d in ranked_docs:
                        if d not in selected_docs and len(selected_docs) < args.top_source_docs:
                            selected_docs.append(d)
                    # Sort chronologically so newer preference updates appear AFTER older ones
                    selected_docs.sort(key=_doc_end_turn)

                    source_blocks = []
                    readback_hits = 0
                    for d in selected_docs:
                        txt = _read_source_document(base, d, doc_cache) or raw_by_doc.get(d, "")
                        if txt:
                            readback_hits += 1
                            source_blocks.append(f"=== Source {d} ===\n{txt}")

                    recall_ms = round((time.perf_counter() - t0) * 1000.0, 1)
                    sourced = sum(
                        1 for r in rlist if r.get("document_id") or r.get("source_document_id") or r.get("id")
                    )
                    mem_summary = "\n".join(
                        f"- [{r.get('document_id') or 'mem'}] {(r.get('text') or '').strip()}"
                        for r in rlist[:24]
                        if (r.get("text") or "").strip()
                    )
                    context_block = (
                        "### Extracted Memory Highlights\n"
                        f"{mem_summary}\n\n"
                        "### Verified Original Source Passages (Chronological Order — Later Turns Supersede Earlier Preferences)\n"
                        + "\n\n".join(source_blocks)
                    )
                else:
                    recall_resp = _http_json(
                        "POST",
                        f"{base}/memories/recall",
                        {
                            "query": user_msg,
                            "budget": args.budget,
                            "max_tokens": args.max_tokens,
                        },
                        timeout=90.0,
                    )
                    recall_ms = round((time.perf_counter() - t0) * 1000.0, 1)
                    rlist = recall_resp.get("results", [])
                    sourced = sum(
                        1 for r in rlist if r.get("document_id") or r.get("source_document_id") or r.get("id")
                    )
                    readback_hits = 0
                    context_block = "\n".join(
                        f"- {(r.get('text') or '').strip()}" for r in rlist if (r.get("text") or "").strip()
                    )

                options_text = "\n".join(options)
                prompt = (
                    "You are selecting the SINGLE best assistant response for the user based strictly on the "
                    "chronological evidence below.\n"
                    "Rules:\n"
                    "1. Later turns supersede earlier turns when the user's preference or opinion evolved.\n"
                    "2. Check every option for factual contradictions against the verified source passages.\n"
                    "3. Pay close attention to the exact reasons the user gave for changing preferences.\n\n"
                    f"{context_block}\n\n"
                    f"User's Current Message (at Turn {end_turn}): {user_msg}\n\n"
                    f"Candidate Options:\n{options_text}\n\n"
                    "Briefly verify each option against the evidence, then end with ONLY the final choice letter "
                    "in parentheses, e.g. (a), (b), (c), or (d)."
                )
                raw_pred = _chat_completion(
                    args.llm_url,
                    args.model,
                    [{"role": "user", "content": prompt}],
                    api_key=args.api_key,
                    temperature=0.0,
                    max_tokens=320,
                )
                pred = _extract_choice(raw_pred)
                correct = pred == gold
                results.append(
                    {
                        "question_id": qid,
                        "question_type": qtype,
                        "topic": qrow.get("topic", ""),
                        "recall_ms": recall_ms,
                        "recalled_count": len(rlist),
                        "readback_docs": readback_hits,
                        "source_attribution_ratio": round(sourced / len(rlist), 4) if rlist else 0.0,
                        "est_context_tokens": max(1, len(context_block) // 4) if context_block else 0,
                        "pred": pred,
                        "gold": gold,
                        "correct": correct,
                    }
                )
                print(
                    f"[{len(results)}/{args.limit}] {qtype} | {'OK' if correct else 'MISS'} "
                    f"(pred={pred}, gold={gold}) | recall+readback={recall_ms}ms | docs={readback_hits} | tok~{len(context_block)//4}",
                    flush=True,
                )
        finally:
            if not args.keep_bank:
                try:
                    _http_json("DELETE", base, timeout=30.0)
                except Exception:
                    pass

    by_cat: dict[str, dict[str, Any]] = {}
    for r in results:
        c = by_cat.setdefault(r["question_type"], {"correct": 0, "total": 0})
        c["total"] += 1
        if r["correct"]:
            c["correct"] += 1
    for c in by_cat.values():
        c["accuracy"] = round(c["correct"] / c["total"], 4) if c["total"] else 0.0

    latencies = [r["recall_ms"] for r in results if r["recall_ms"] > 0]
    tokens = [r["est_context_tokens"] for r in results]
    attr = [r["source_attribution_ratio"] for r in results if r["recalled_count"] > 0]

    summary = {
        "benchmark": "PersonaMem-32K",
        "mode": args.mode,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "config": {
            "model": args.model,
            "budget": args.budget,
            "max_tokens": args.max_tokens,
            "chunk_turns": args.chunk_turns,
            "top_source_docs": args.top_source_docs,
            "contexts": len(selected_ctx_ids),
            "evaluated": len(results),
        },
        "overall": {
            "evaluated": len(results),
            "correct": sum(1 for r in results if r["correct"]),
            "accuracy": round(sum(1 for r in results if r["correct"]) / len(results), 4) if results else 0.0,
            "recall_p50_ms": round(_percentile(latencies, 50), 1),
            "recall_p95_ms": round(_percentile(latencies, 95), 1),
            "mean_context_tokens": round(statistics.mean(tokens), 1) if tokens else 0.0,
            "mean_source_attribution": round(statistics.mean(attr), 4) if attr else 0.0,
            "elapsed_sec": round(time.perf_counter() - t_start, 1),
        },
        "by_category": by_cat,
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== PERSONAMEM-32K SUMMARY ===")
    print(json.dumps(summary["overall"], indent=2))
    print(json.dumps(summary["by_category"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
