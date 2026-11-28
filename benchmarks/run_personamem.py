#!/usr/bin/env python3
"""Reproducible PersonaMem-32K benchmark runner for Evolving Profile (EP).

Evaluates EP on isolated ephemeral banks per shared_context_id (never touching
personal banks), recording exact-match multiple-choice accuracy by question_type,
recall latency p50/p95, injected context token estimate, and source attribution.
"""
from __future__ import annotations

import ast
import argparse
import collections
import csv
import json
import math
import re
import statistics
import time
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
    max_tokens: int = 32,
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
    m = re.search(r"\(([a-dA-D])\)", text)
    if m:
        return f"({m.group(1).lower()})"
    m2 = re.search(r"\b([a-dA-D])\b", text)
    if m2:
        return f"({m2.group(1).lower()})"
    return text.strip().lower()[:8]


def _chunk_messages(messages: list[dict[str, Any]], chunk_turns: int = 12) -> list[str]:
    chunks = []
    cur = []
    for m in messages:
        role = m.get("role", "user")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        cur.append(f"{role.capitalize()}: {content}")
        if len(cur) >= chunk_turns:
            chunks.append("\n".join(cur))
            cur = []
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions-csv", type=Path, required=True)
    parser.add_argument("--contexts-jsonl", type=Path, required=True)
    parser.add_argument("--api-url", default="http://127.0.0.1:12088")
    parser.add_argument("--llm-url", default="http://127.0.0.1:12095/v1")
    parser.add_argument("--model", default="gpt-6-luna")
    parser.add_argument("--mode", default="recall", choices=["recall", "reflect"])
    parser.add_argument("--api-key", default="local")
    parser.add_argument("--budget", default="high", choices=["low", "mid", "high"])
    parser.add_argument("--max-tokens", type=int, default=6000)
    parser.add_argument("--max-contexts", type=int, default=2, help="Number of shared persona contexts to evaluate")
    parser.add_argument("--limit", type=int, default=20, help="Max total questions across selected contexts")
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
    print(f"Selected {len(selected_ctx_ids)} shared persona contexts (limit={args.limit} questions)...", flush=True)

    results = []
    t_start = time.perf_counter()

    for ctx_id in selected_ctx_ids:
        if len(results) >= args.limit:
            break
        msgs = contexts.get(ctx_id, [])
        qrows = by_ctx[ctx_id][: max(0, args.limit - len(results))]
        bank_id = f"bench-pmem-{uuid.uuid4().hex[:10]}"
        base = f"{args.api_url.rstrip('/')}/v1/default/banks/{bank_id}"

        try:
            chunks = _chunk_messages(msgs, chunk_turns=24)
            items = [
                {
                    "content": c,
                    "document_id": f"{ctx_id[:12]}:chunk-{i}",
                    "context": f"Persona dialogue block {i}",
                }
                for i, c in enumerate(chunks)
            ]
            print(f"Ingesting {len(items)} dialogue blocks into isolated bank {bank_id}...", flush=True)
            for i in range(0, len(items), 2):
                _http_json(
                    "POST",
                    f"{base}/memories",
                    {"items": items[i : i + 2], "async": False},
                    timeout=300.0,
                )
                print(f"  ingested {min(i + 2, len(items))}/{len(items)} blocks", flush=True)

            for qrow in qrows:
                qid = qrow["question_id"]
                qtype = qrow["question_type"]
                user_msg = qrow["user_question_or_message"]
                gold = qrow["correct_answer"].strip().lower()
                options = ast.literal_eval(qrow["all_options"])

                t0 = time.perf_counter()
                if args.mode == "reflect":
                    reflect_resp = _http_json(
                        "POST",
                        f"{base}/reflect",
                        {
                            "query": (
                                f"Given this user message: '{user_msg}', summarize all relevant user preferences, "
                                "persona traits, preference updates, and reasons for any preference changes."
                            ),
                            "budget": args.budget,
                            "max_tokens": args.max_tokens,
                        },
                        timeout=120.0,
                    )
                    recall_ms = round((time.perf_counter() - t0) * 1000.0, 1)
                    based_on = reflect_resp.get("based_on") or []
                    rlist = based_on if isinstance(based_on, list) else []
                    sourced = len(rlist) if rlist else (1 if reflect_resp.get("text") else 0)
                    context_block = (reflect_resp.get("text") or "").strip()
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
                    mem_lines = [
                        f"- [{r.get('occurred_start') or r.get('mentioned_at') or ''}] {(r.get('text') or '').strip()}"
                        for r in rlist
                        if (r.get("text") or "").strip()
                    ]
                    context_block = "\n".join(mem_lines)
                options_text = "\n".join(options)

                prompt = (
                    "You are selecting the single response that best matches the user's known preferences, "
                    "persona, and most recent preference evolution based ONLY on the retrieved memories below. "
                    "Pay close attention to preference updates (newer preferences supersede older ones) and the exact reasons behind updates.\n\n"
                    f"Retrieved Memories:\n{context_block or '(none)'}\n\n"
                    f"User Message: {user_msg}\n\n"
                    f"Options:\n{options_text}\n\n"
                    "Think briefly about which option aligns with the latest user preference and avoids contradictions, "
                    "then end your response with the final choice in parentheses, e.g. (a), (b), (c), or (d)."
                )
                raw_pred = _chat_completion(
                    args.llm_url,
                    args.model,
                    [{"role": "user", "content": prompt}],
                    api_key=args.api_key,
                    temperature=0.0,
                    max_tokens=200,
                )
                # Extract the last parenthesized choice (a)-(d)
                matches = re.findall(r"\(([a-dA-D])\)", raw_pred)
                pred = f"({matches[-1].lower()})" if matches else _extract_choice(raw_pred)
                correct = pred == gold
                results.append(
                    {
                        "question_id": qid,
                        "question_type": qtype,
                        "topic": qrow.get("topic", ""),
                        "recall_ms": recall_ms,
                        "recalled_count": len(rlist),
                        "source_attribution_ratio": round(sourced / len(rlist), 4) if rlist else 0.0,
                        "est_context_tokens": max(1, len(context_block) // 4) if context_block else 0,
                        "pred": pred,
                        "gold": gold,
                        "correct": correct,
                    }
                )
                print(
                    f"[{len(results)}/{args.limit}] {qtype} | {'OK' if correct else 'MISS'} "
                    f"(pred={pred}, gold={gold}) | recall={recall_ms}ms | tok~{len(context_block)//4}",
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
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "config": {
            "model": args.model,
            "budget": args.budget,
            "max_tokens": args.max_tokens,
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
