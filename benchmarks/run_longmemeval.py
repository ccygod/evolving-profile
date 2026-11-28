#!/usr/bin/env python3
"""Reproducible LongMemEval-S benchmark runner for Evolving Profile (EP).

Evaluates EP on isolated ephemeral banks (never touching personal banks),
recording per-category accuracy (LLM-as-a-Judge), recall latency p50/p95,
injected context token estimate, and source-attribution coverage.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import statistics
import time
import urllib.error
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
    max_tokens: int = 256,
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


def _format_session(session: list[dict[str, Any]], session_date: str) -> str:
    lines = [f"[Session Date: {session_date}]"]
    for turn in session:
        role = turn.get("role", "user").capitalize()
        content = (turn.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _stratified_sample(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if limit <= 0 or limit >= len(rows):
        return rows
    by_type: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in rows:
        by_type[row.get("question_type", "unknown")].append(row)
    types = sorted(by_type.keys())
    selected: list[dict[str, Any]] = []
    idx = 0
    while len(selected) < limit:
        added = False
        for t in types:
            bucket = by_type[t]
            if idx < len(bucket) and len(selected) < limit:
                selected.append(bucket[idx])
                added = True
        if not added:
            break
        idx += 1
    return selected


def _judge_answer(
    llm_url: str,
    judge_model: str,
    question: str,
    gold: str,
    hypothesis: str,
    question_type: str,
    *,
    api_key: str = "local",
) -> tuple[bool, str]:
    abstention_note = ""
    if "abstention" in question_type.lower() or str(gold).lower().startswith("unanswerable"):
        abstention_note = (
            "Note: This is an abstention question where the required information was never provided. "
            "Mark YES only if the model correctly abstains or states the information is not available."
        )
    prompt = (
        "You are a strict benchmark evaluator. Compare the model's answer against the reference answer.\n"
        f"Question: {question}\n"
        f"Reference Answer: {gold}\n"
        f"Model Answer: {hypothesis}\n"
        f"{abstention_note}\n"
        "Respond with ONLY a JSON object: {\"correct\": true/false, \"reason\": \"short explanation\"}."
    )
    raw = _chat_completion(
        llm_url,
        judge_model,
        [{"role": "user", "content": prompt}],
        api_key=api_key,
        temperature=0.0,
        max_tokens=128,
    )
    try:
        start = raw.find("{")
        end = raw.rfind("}")
        parsed = json.loads(raw[start : end + 1]) if start != -1 and end != -1 else {}
        return bool(parsed.get("correct", False)), str(parsed.get("reason", ""))
    except Exception:
        lowered = raw.lower()
        return ("true" in lowered or "yes" in lowered), raw[:120]


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


def evaluate_question(
    row: dict[str, Any],
    *,
    api_url: str,
    llm_url: str,
    model: str,
    judge_model: str,
    api_key: str,
    budget: str,
    max_tokens: int,
    max_sessions_per_q: int,
    keep_bank: bool,
) -> dict[str, Any]:
    qid = str(row.get("question_id", uuid.uuid4().hex[:8]))
    qtype = str(row.get("question_type", "unknown"))
    question = str(row["question"])
    gold = str(row["answer"])
    qdate = str(row.get("question_date", ""))
    sessions = row.get("haystack_sessions", [])
    dates = row.get("haystack_dates", [])
    session_ids = row.get("haystack_session_ids", [])

    if max_sessions_per_q > 0 and len(sessions) > max_sessions_per_q:
        # Keep answer-bearing sessions + balanced distractors when explicitly capped
        answer_sids = set(row.get("answer_session_ids", []))
        kept_indices = [i for i, sid in enumerate(session_ids) if sid in answer_sids]
        for i in range(len(sessions)):
            if len(kept_indices) >= max_sessions_per_q:
                break
            if i not in kept_indices:
                kept_indices.append(i)
        kept_indices.sort()
        sessions = [sessions[i] for i in kept_indices]
        dates = [dates[i] if i < len(dates) else "" for i in kept_indices]
        session_ids = [session_ids[i] if i < len(session_ids) else f"s-{i}" for i in kept_indices]

    bank_id = f"bench-lme-{uuid.uuid4().hex[:10]}"
    base = f"{api_url.rstrip('/')}/v1/default/banks/{bank_id}"
    recall_ms = 0.0
    recalled_count = 0
    sourced_count = 0
    context_chars = 0
    hypothesis = ""
    correct = False
    reason = ""
    error = None

    try:
        items = []
        for idx, sess in enumerate(sessions):
            sdate = dates[idx] if idx < len(dates) else ""
            sid = session_ids[idx] if idx < len(session_ids) else f"sess-{idx}"
            items.append(
                {
                    "content": _format_session(sess, sdate),
                    "document_id": f"{qid}:{sid}",
                    "context": f"Conversation session {sid} on {sdate}",
                }
            )

        # Ingest in small chunks synchronously to avoid single-worker HTTP timeouts
        for i in range(0, len(items), 2):
            _http_json(
                "POST",
                f"{base}/memories",
                {"items": items[i : i + 2], "async": False},
                timeout=300.0,
            )

        t0 = time.perf_counter()
        recall_resp = _http_json(
            "POST",
            f"{base}/memories/recall",
            {
                "query": question,
                "budget": budget,
                "max_tokens": max_tokens,
            },
            timeout=90.0,
        )
        recall_ms = round((time.perf_counter() - t0) * 1000.0, 1)

        results = recall_resp.get("results", [])
        recalled_count = len(results)
        memory_lines = []
        for r in results:
            txt = (r.get("text") or "").strip()
            doc_id = r.get("document_id") or r.get("source_document_id") or r.get("id")
            if doc_id:
                sourced_count += 1
            if txt:
                occ = r.get("occurred_start") or r.get("mentioned_at") or ""
                prefix = f"[{occ}] " if occ else ""
                memory_lines.append(f"- {prefix}{txt}")

        context_block = "\n".join(memory_lines)
        context_chars = len(context_block)

        sys_prompt = (
            "You are answering a question about a user's past conversations using ONLY the retrieved memories below. "
            "If the memories do not contain enough evidence to answer, explicitly say you do not know / it was not mentioned. "
            "Be concise and accurate."
        )
        user_prompt = (
            f"Current Question Date: {qdate}\n"
            f"Retrieved Memories:\n{context_block or '(none)'}\n\n"
            f"Question: {question}"
        )
        hypothesis = _chat_completion(
            llm_url,
            model,
            [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
            api_key=api_key,
            temperature=0.0,
            max_tokens=200,
        )

        correct, reason = _judge_answer(
            llm_url, judge_model, question, gold, hypothesis, qtype, api_key=api_key
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        if not keep_bank:
            try:
                _http_json("DELETE", base, timeout=30.0)
            except Exception:
                pass

    return {
        "question_id": qid,
        "question_type": qtype,
        "sessions_ingested": len(sessions),
        "recall_ms": recall_ms,
        "recalled_count": recalled_count,
        "source_attribution_ratio": round(sourced_count / recalled_count, 4) if recalled_count else 0.0,
        "est_context_tokens": max(1, context_chars // 4) if context_chars else 0,
        "correct": correct,
        "reason": reason,
        "hypothesis": hypothesis,
        "gold": gold,
        "error": error,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--api-url", default="http://127.0.0.1:12088")
    parser.add_argument("--llm-url", default="http://127.0.0.1:12095/v1")
    parser.add_argument("--model", default="qwen3.7-flash-2026-07-15")
    parser.add_argument("--judge-model", default="qwen3.6-plus")
    parser.add_argument("--api-key", default="local")
    parser.add_argument("--budget", default="mid", choices=["low", "mid", "high"])
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--max-sessions", type=int, default=0, help="0 = all haystack sessions")
    parser.add_argument("--keep-bank", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    rows = json.loads(args.dataset.read_text(encoding="utf-8"))
    sample = _stratified_sample(rows, args.limit)
    print(f"Loaded {len(rows)} questions; evaluating {len(sample)} stratified questions...", flush=True)

    results = []
    t_start = time.perf_counter()
    for i, row in enumerate(sample, 1):
        res = evaluate_question(
            row,
            api_url=args.api_url,
            llm_url=args.llm_url,
            model=args.model,
            judge_model=args.judge_model,
            api_key=args.api_key,
            budget=args.budget,
            max_tokens=args.max_tokens,
            max_sessions_per_q=args.max_sessions,
            keep_bank=args.keep_bank,
        )
        results.append(res)
        ok_mark = "OK" if res["correct"] else ("ERR" if res["error"] else "MISS")
        print(
            f"[{i}/{len(sample)}] {res['question_type']} | {ok_mark} | "
            f"recall={res['recall_ms']}ms | tok~{res['est_context_tokens']} | {res['question_id']}",
            flush=True,
        )

    valid = [r for r in results if not r["error"]]
    by_cat: dict[str, dict[str, Any]] = {}
    for r in valid:
        c = by_cat.setdefault(r["question_type"], {"correct": 0, "total": 0})
        c["total"] += 1
        if r["correct"]:
            c["correct"] += 1
    for c in by_cat.values():
        c["accuracy"] = round(c["correct"] / c["total"], 4) if c["total"] else 0.0

    latencies = [r["recall_ms"] for r in valid if r["recall_ms"] > 0]
    tokens = [r["est_context_tokens"] for r in valid]
    attr = [r["source_attribution_ratio"] for r in valid if r["recalled_count"] > 0]

    summary = {
        "benchmark": "LongMemEval-S",
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "config": {
            "model": args.model,
            "judge_model": args.judge_model,
            "budget": args.budget,
            "max_tokens": args.max_tokens,
            "limit": len(sample),
            "max_sessions": args.max_sessions,
        },
        "overall": {
            "evaluated": len(valid),
            "errors": len(results) - len(valid),
            "correct": sum(1 for r in valid if r["correct"]),
            "accuracy": round(sum(1 for r in valid if r["correct"]) / len(valid), 4) if valid else 0.0,
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
    print("\n=== SUMMARY ===")
    print(json.dumps(summary["overall"], indent=2))
    print(json.dumps(summary["by_category"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
