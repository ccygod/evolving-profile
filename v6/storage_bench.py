#!/usr/bin/env python3
"""EP storage-optimisation benchmark (EP 6.2).

Measures, against the live memory API (direct, no controller cache):
  * latency  - recall p50/p95 over recent real user prompts (3 rounds each)
  * known-item recall - 100 high-value memories; query = first ~30 chars of the
    memory text; hit@10 = the memory itself is in the top 10
  * stability - top-20 ids per prompt, saved so runs can be diffed

Usage (bank from EVOLVING_PROFILE_BANK_ID):
  python -m v6.storage_bench run  <out.json>            # take a measurement
  python -m v6.storage_bench diff <before.json> <after.json>
The prompt and known-item sets are frozen in eval/storage-bench-items.json on
the first run and reused, so before/after runs measure the same questions.
"""
import json
import os
import random
import statistics
import sys
import time
import urllib.request

API = os.environ.get("EP_BENCH_API", "http://127.0.0.1:12088")
BANK = os.environ.get("EVOLVING_PROFILE_BANK_ID", "")
STATE = os.environ.get("EVOLVING_PROFILE_STATE_ROOT") or os.path.expanduser("~/.evolving-profile")
ITEMS = os.path.join(STATE, "eval", "storage-bench-items.json")


def _dsn():
    env = os.environ.get("EVOLVING_PROFILE_API_DATABASE_URL")
    if not env:
        for line in open(os.path.join(STATE, "profiles", "evolving-profile-api.env"), encoding="utf-8"):
            if line.startswith("EVOLVING_PROFILE_API_DATABASE_URL="):
                env = line.split("=", 1)[1]
    return env.strip().strip('"').replace("postgresql+asyncpg", "postgresql")


def sql_json(query):
    import psycopg2
    with psycopg2.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(query)
        row = cur.fetchone()
    return row[0] if row else None


def recall(query, max_tokens=2400):
    body = json.dumps({"query": query, "max_tokens": max_tokens, "budget": "mid"}, ensure_ascii=False).encode()
    req = urllib.request.Request(f"{API}/v1/default/banks/{BANK}/memories/recall", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    return time.perf_counter() - t0, [str(r.get("id")) for r in (data.get("results") or [])]


def prompts():
    path = os.path.join(STATE, "audit", "prompt-ingress.jsonl")
    seen = []
    with open(path, "rb") as fh:
        fh.seek(max(0, os.path.getsize(path) - 3_000_000))
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            t = (d.get("prompt_preview") or "").strip()
            if d.get("prompt_origin") == "user_direct" and 6 < len(t) < 400 and not t.startswith("<") and t not in seen:
                seen.append(t)
    return seen[-30:]


def known_items():
    if os.path.exists(ITEMS):
        return json.load(open(ITEMS))
    rows = sql_json(f"""select json_agg(x) from (
        select id::text, left(regexp_replace(text,'\\s+',' ','g'), 30) q from memory_units
        where bank_id='{BANK}' and fact_type in ('world','experience') and embedding is not null
          and coalesce(metadata->>'recall_demoted','')<>'true' and coalesce(metadata->>'trust_level','')<>'assistant_unconfirmed'
          and coalesce(metadata->>'fragment_noise','')<>'true' and length(text) > 40
          and text not like 'Assistant statement%'
        order by md5(id::text||'bench') limit 100) x""")
    os.makedirs(os.path.dirname(ITEMS), exist_ok=True)
    json.dump({"prompts": prompts(), "items": rows}, open(ITEMS, "w"), ensure_ascii=False, indent=1)
    return json.load(open(ITEMS))


def run(out):
    fixed = known_items()
    lat, tops = [], {}
    for rnd in range(3):
        for p in fixed["prompts"]:
            dt, ids = recall(p)
            lat.append(dt)
            if rnd == 0:
                tops[p] = ids[:20]
    hits = 0
    item_lat = []
    # A known item that was merged into a representative counts as found when
    # its representative is returned (dedupe keeps one copy of the same fact).
    try:
        merged = json.load(open(os.path.join(STATE, "control-plane", "memory-tiering-merged.json")))
    except (OSError, ValueError):
        merged = {}
    for it in fixed["items"]:
        dt, ids = recall(it["q"], max_tokens=1200)
        item_lat.append(dt)
        hits += it["id"] in ids[:10] or merged.get(it["id"]) in ids[:10]
    lat.sort()
    result = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "prompts": len(fixed["prompts"]),
        "latency_p50_s": round(statistics.median(lat), 3),
        "latency_p95_s": round(lat[int(0.95 * (len(lat) - 1))], 3),
        "latency_mean_s": round(statistics.mean(lat), 3),
        "known_item_hit_at_10": f"{hits}/{len(fixed['items'])}",
        "known_item_hit_rate": round(hits / max(len(fixed["items"]), 1), 3),
        "known_item_latency_p50_s": round(statistics.median(item_lat), 3),
        "top20": tops,
    }
    json.dump(result, open(out, "w"), ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in result.items() if k != "top20"}, ensure_ascii=False))


def diff(a, b):
    A, B = json.load(open(a)), json.load(open(b))
    overlap = [len(set(A["top20"][p][:10]) & set(B["top20"].get(p, [])[:10])) / max(len(A["top20"][p][:10]), 1)
               for p in A["top20"] if A["top20"][p]]
    print(json.dumps({
        "latency_p50": [A["latency_p50_s"], B["latency_p50_s"]],
        "latency_p95": [A["latency_p95_s"], B["latency_p95_s"]],
        "known_item_hit_at_10": [A["known_item_hit_at_10"], B["known_item_hit_at_10"]],
        "top10_overlap_mean": round(statistics.mean(overlap), 3) if overlap else None,
    }, ensure_ascii=False))
    dropped = sorted({i for p in A["top20"] for i in A["top20"][p][:10]} - {i for p in B["top20"] for i in B["top20"][p][:10]})
    json.dump(dropped, open(b + ".dropped-ids.json", "w"))


if __name__ == "__main__":
    import re
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", BANK or ""):
        raise SystemExit("set EVOLVING_PROFILE_BANK_ID to a valid bank id")
    if sys.argv[1] == "run":
        run(sys.argv[2])
    else:
        diff(sys.argv[2], sys.argv[3])
