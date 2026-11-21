"""Cross-platform, domain-agnostic L0/L1/L2 Adaptive Memory Gate for EP v6.

Implements three-tier cognitive memory gating inspired by MemGPT/Mem0 (L0 Core Identity Card),
RF-Mem (L1 fast Familiarity scan), and User-Conditioned Variance (L2 retrieval principle):
- Zero hardcoded user names or industry keywords (fully generalizes to any user and any domain).
- Zero foreground LLM calls; sub-50ms local SQL/SQLite cue generation.
- Fail-open: never blocks or slows down the host agent loop.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any


# Domain-agnostic pragmatic markers indicating the optimal answer depends on the user's
# identity, goals, resources, constraints, or prior decisions (not a user-invariant fact).
USER_CONDITIONED_RE = re.compile(
    r"(我要|我想|帮我|给我|我的|咱们|我们|对我|适合我|怎么(?:跟|向|给|帮|选|挑|写|讲|回|设计|规划|安排|处理|结合|说服|沟通|应对))"
    r"|(\b(?:i want|i need|i am going to|i'm going to|help me|for me|my |our |how should i|what should i|recommend for|advise me)\b)",
    re.I,
)

USER_INVARIANT_TRIVIAL_RE = re.compile(
    r"^(\s*(?:hi|hello|hey|你好|您好|早上好|晚上好|谢谢|thanks|ok|好的)[!！。.?？\s]*$"
    r"|\s*(?:请?计算)?\d+(?:\.\d+)?\s*[+\-*/×÷x]\s*\d+(?:\.\d+)?(?:\s*=|\s*等于多少)?[?？\s]*$)",
    re.I,
)

AGENT_TROUBLESHOOT_RE = re.compile(
    r"(报错|失败|异常|崩溃|超时|卡住|怎么修|踩坑|修复|排查|回滚|\b(?:error|failed|exception|timeout|traceback|bug|fix|debug|crash)\b)",
    re.I,
)

GENERIC_STOPWORDS = {
    "什么", "怎么", "如何", "可以", "应该", "需要", "进行", "通过", "使用", "一个",
    "这个", "那个", "这些", "那些", "他们", "我们", "你们", "自己", "因为", "所以",
    "但是", "如果", "然后", "而且", "或者", "已经", "目前", "现在", "时候", "地方",
    "东西", "问题", "方法", "例子", "介绍", "一下", "一些", "关于", "对于", "以及",
    "结果", "限制", "是否", "是不是", "有没有", "能不能", "看看", "一下", "出来", "里面",
    "the", "and", "for", "with", "that", "this", "from", "what", "how", "why", "are", "you",
}

# Domain-agnostic CJK function characters/particles that should not sit on the
# leading/trailing edge of an n-gram lexical query term (prevents noisy sliding-window
# fragments like '这是不', '的结果', '结果吗' from matching unrelated prose).
_CJK_EDGE_STOPCHARS = set(
    "的了么吗呢吧啊呀哦嗯是在有不这那把被给让从到为与和或也都就会要能可将对于中上下里来去过着"
)

L2_UNIVERSAL_PRINCIPLE = (
    "Memory Trigger Principle (User-Conditioned Variance): "
    "Before answering, ask one question — if the user were replaced by a different person "
    "(different role, goals, resources, prior decisions, or constraints), would the optimal answer change? "
    "If YES, proactively consult EP (`user_preference` + `user_recall`/`user_research`) in the first turn even if EP is never mentioned. "
    "Skip EP only for user-invariant self-contained tasks (pure math, literal translation, generic definitions)."
)


def is_user_invariant_trivial(prompt: str) -> bool:
    text = (prompt or "").strip()
    if not text or len(text) <= 2:
        return True
    return bool(USER_INVARIANT_TRIVIAL_RE.match(text))


def is_user_conditioned_prompt(prompt: str) -> bool:
    text = (prompt or "").strip()
    if not text or is_user_invariant_trivial(text):
        return False
    return bool(USER_CONDITIONED_RE.search(text))


def extract_query_terms(prompt: str, *, max_terms: int = 12) -> list[str]:
    """Extract domain-agnostic lexical terms (alphanumeric tokens + CJK 2-4 char chunks)."""
    text = (prompt or "").strip().lower()
    terms: list[str] = []
    seen: set[str] = set()

    for tok in re.findall(r"[a-z][a-z0-9_-]{2,}", text):
        if tok not in GENERIC_STOPWORDS and tok not in seen:
            seen.add(tok)
            terms.append(tok)

    for seg in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        # Prefer 2-char to 4-char content chunks without boundary function particles
        if (
            2 <= len(seg) <= 6
            and seg not in GENERIC_STOPWORDS
            and seg[0] not in _CJK_EDGE_STOPCHARS
            and seg[-1] not in _CJK_EDGE_STOPCHARS
            and seg not in seen
        ):
            seen.add(seg)
            terms.append(seg)
        for size in (4, 3, 2):
            for i in range(0, max(0, len(seg) - size + 1)):
                sub = seg[i : i + size]
                if (
                    sub not in GENERIC_STOPWORDS
                    and sub[0] not in _CJK_EDGE_STOPCHARS
                    and sub[-1] not in _CJK_EDGE_STOPCHARS
                    and sub not in seen
                ):
                    seen.add(sub)
                    terms.append(sub)
                    if len(terms) >= max_terms * 2:
                        break
            if len(terms) >= max_terms * 2:
                break
    return terms[:max_terms]


def _connect_pg(dsn: str | None = None, *, user: str | None = None, port: int = 15432, dbname: str = "evolving_profile"):
    import psycopg2

    if dsn:
        return psycopg2.connect(dsn, connect_timeout=1, options="-c statement_timeout=150")
    kw: dict[str, Any] = {
        "port": port,
        "dbname": dbname,
        "connect_timeout": 1,
        "options": "-c statement_timeout=150",
    }
    if user:
        kw["user"] = user
    return psycopg2.connect(**kw)


def _load_top_guardrails(guidance_db: Path, *, limit: int = 4) -> list[dict[str, str]]:
    if not guidance_db.is_file():
        return []
    out: list[dict[str, str]] = []
    con = sqlite3.connect(str(guidance_db), timeout=1.0)
    try:
        rows = con.execute("SELECT unit_id, payload_json FROM unit_revisions WHERE active = 1").fetchall()
        for uid, pj in rows:
            try:
                d = json.loads(pj)
            except Exception:
                continue
            if d.get("inject_default") is False:
                continue
            if d.get("enforcement") == "hard_guardrail" and d.get("scope_level", "global") == "global":
                text = str(d.get("text") or "").strip()
                if text:
                    out.append({"id": uid, "category": str(d.get("primary_category") or ""), "text": text[:110]})
                    if len(out) >= limit:
                        break
    except Exception:
        return []
    finally:
        con.close()
    return out


def _match_preferences_fast(guidance_db: Path, terms: list[str], *, limit: int = 2) -> list[dict[str, str]]:
    if not guidance_db.is_file() or not terms:
        return []
    scored: list[tuple[int, dict[str, str]]] = []
    con = sqlite3.connect(str(guidance_db), timeout=1.0)
    try:
        rows = con.execute("SELECT unit_id, payload_json FROM unit_revisions WHERE active = 1").fetchall()
        for uid, pj in rows:
            try:
                d = json.loads(pj)
            except Exception:
                continue
            if d.get("inject_default") is False:
                continue
            corpus = (str(d.get("text") or "") + " " + " ".join(d.get("applies_when") or [])).lower()
            hits = sum(1 for t in terms if t in corpus)
            if hits > 0:
                bonus = 2 if d.get("enforcement") == "hard_guardrail" else 0
                scored.append(
                    (
                        hits + bonus,
                        {
                            "id": uid,
                            "enforcement": str(d.get("enforcement") or "default_policy"),
                            "text": str(d.get("text") or "").strip()[:120],
                        },
                    )
                )
    except Exception:
        return []
    finally:
        con.close()
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:limit]]


def build_l0_core_card(
    data_root: str | Path | None = None,
    *,
    dsn: str | None = None,
    user: str | None = None,
    port: int = 15432,
    dbname: str = "evolving_profile",
    max_models: int = 5,
    max_entities: int = 6,
    max_guardrails: int = 4,
) -> dict[str, Any]:
    """Synthesize a compact (~200-token) L0 Core Identity Card dynamically from the user's own EP store."""
    t0 = time.time()
    root = Path(data_root or os.environ.get("EP_DATA_ROOT") or Path.home() / ".evolving-profile").expanduser()
    pg_user = user or os.environ.get("PGUSER", "evolving_profile_admin")
    pg_port = int(os.environ.get("PGPORT", str(port)))
    pg_db = os.environ.get("PGDATABASE", dbname)

    model_titles: list[str] = []
    active_entities: list[str] = []
    try:
        conn = _connect_pg(dsn, user=pg_user, port=pg_port, dbname=pg_db)
        try:
            with conn.cursor() as cur:
                # Primary bank mental models (largest bank by memory_units, zero hardcoded bank_id)
                cur.execute(
                    """
                    WITH primary_bank AS (
                        SELECT bank_id FROM public.memory_units
                        GROUP BY bank_id ORDER BY count(*) DESC LIMIT 1
                    )
                    SELECT name FROM public.mental_models
                    WHERE bank_id = (SELECT bank_id FROM primary_bank)
                    ORDER BY length(content) DESC NULLS LAST
                    LIMIT %s
                    """,
                    (max_models,),
                )
                model_titles = [str(r[0]).strip() for r in cur.fetchall() if r[0]]

                cur.execute("SELECT to_regclass('ep_events.entity_state') IS NOT NULL")
                if cur.fetchone()[0]:
                    cur.execute(
                        """
                        SELECT entity_key, status FROM ep_events.entity_state
                        WHERE valid_to IS NULL AND status = 'active' AND length(entity_key) >= 3
                        ORDER BY updated_at DESC
                        LIMIT %s
                        """,
                        (max_entities,),
                    )
                    active_entities = [f"{r[0]} ({r[1]})" for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        pass

    guardrails = _load_top_guardrails(root / "guidance-v1" / "guidance.sqlite3", limit=max_guardrails)

    lines = ["<ep_l0_core_memory version=\"v6.2\">"]
    lines.append(L2_UNIVERSAL_PRINCIPLE)
    if model_titles:
        lines.append("User core domains / mental models: " + " | ".join(model_titles))
    if active_entities:
        lines.append("Active projects/entities: " + ", ".join(active_entities))
    if guardrails:
        lines.append("Top guardrails: " + " ; ".join(g["text"] for g in guardrails))
    lines.append("</ep_l0_core_memory>")

    return {
        "status": "ok",
        "latency_ms": int((time.time() - t0) * 1000),
        "model_titles": model_titles,
        "active_entities": active_entities,
        "guardrail_count": len(guardrails),
        "card_text": "\n".join(lines),
    }


def evaluate_l1_familiarity(
    prompt: str,
    data_root: str | Path | None = None,
    *,
    dsn: str | None = None,
    user: str | None = None,
    port: int = 15432,
    dbname: str = "evolving_profile",
) -> dict[str, Any]:
    """Fast (<50ms, zero-LLM) L1 familiarity & user-conditioned variance gate."""
    t0 = time.time()
    text = (prompt or "").strip()
    if is_user_invariant_trivial(text):
        return {
            "decision": "skip",
            "reason": "user_invariant_trivial",
            "user_conditioned": False,
            "familiarity_hits": 0,
            "latency_ms": int((time.time() - t0) * 1000),
            "cue_text": "",
        }

    root = Path(data_root or os.environ.get("EP_DATA_ROOT") or Path.home() / ".evolving-profile").expanduser()
    pg_user = user or os.environ.get("PGUSER", "evolving_profile_admin")
    pg_port = int(os.environ.get("PGPORT", str(port)))
    pg_db = os.environ.get("PGDATABASE", dbname)

    user_cond = is_user_conditioned_prompt(text)
    terms = extract_query_terms(text, max_terms=10)
    matched_entities: list[dict[str, str]] = []
    matched_models: list[str] = []

    if terms:
        try:
            conn = _connect_pg(dsn, user=pg_user, port=pg_port, dbname=pg_db)
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT to_regclass('ep_events.entity_state') IS NOT NULL")
                    if cur.fetchone()[0]:
                        key_patterns = [f"%{t}%" for t in terms[:8]]
                        # Only match long/specific terms against free-form prose summaries
                        # to avoid high-frequency 2-3 char false positives.
                        prose_terms = [
                            t for t in terms[:8]
                            if (len(t) >= 4 if any("\u4e00" <= c <= "\u9fff" for c in t) else len(t) >= 5)
                        ]
                        prose_patterns = [f"%{t}%" for t in prose_terms]
                        if prose_patterns:
                            cur.execute(
                                """
                                SELECT entity_type, entity_key, status, left(COALESCE(summary, ''), 100)
                                FROM ep_events.entity_state
                                WHERE valid_to IS NULL
                                  AND length(entity_key) >= 3
                                  AND (entity_key ILIKE ANY(%s) OR COALESCE(summary, '') ILIKE ANY(%s))
                                ORDER BY (entity_key ILIKE ANY(%s)) DESC, (status = 'active') DESC, updated_at DESC
                                LIMIT 3
                                """,
                                (key_patterns, prose_patterns, key_patterns),
                            )
                        else:
                            cur.execute(
                                """
                                SELECT entity_type, entity_key, status, left(COALESCE(summary, ''), 100)
                                FROM ep_events.entity_state
                                WHERE valid_to IS NULL
                                  AND length(entity_key) >= 3
                                  AND entity_key ILIKE ANY(%s)
                                ORDER BY (status = 'active') DESC, updated_at DESC
                                LIMIT 3
                                """,
                                (key_patterns,),
                            )
                        matched_entities = [
                            {"type": r[0], "key": r[1], "status": r[2], "summary": r[3]}
                            for r in cur.fetchall()
                        ]

                    key_patterns = [f"%{t}%" for t in terms[:8]]
                    prose_terms = [
                        t for t in terms[:8]
                        if (len(t) >= 4 if any("\u4e00" <= c <= "\u9fff" for c in t) else len(t) >= 5)
                    ]
                    prose_patterns = [f"%{t}%" for t in prose_terms]
                    if prose_patterns:
                        cur.execute(
                            """
                            WITH primary_bank AS (
                                SELECT bank_id FROM public.memory_units
                                GROUP BY bank_id ORDER BY count(*) DESC LIMIT 1
                            )
                            SELECT name FROM public.mental_models
                            WHERE bank_id = (SELECT bank_id FROM primary_bank)
                              AND (name ILIKE ANY(%s) OR content ILIKE ANY(%s))
                            ORDER BY (name ILIKE ANY(%s)) DESC
                            LIMIT 3
                            """,
                            (key_patterns, prose_patterns, key_patterns),
                        )
                    else:
                        cur.execute(
                            """
                            WITH primary_bank AS (
                                SELECT bank_id FROM public.memory_units
                                GROUP BY bank_id ORDER BY count(*) DESC LIMIT 1
                            )
                            SELECT name FROM public.mental_models
                            WHERE bank_id = (SELECT bank_id FROM primary_bank)
                              AND name ILIKE ANY(%s)
                            LIMIT 3
                            """,
                            (key_patterns,),
                        )
                    matched_models = [str(r[0]).strip() for r in cur.fetchall() if r[0]]
            finally:
                conn.close()
        except Exception:
            pass

    matched_prefs = _match_preferences_fast(root / "guidance-v1" / "guidance.sqlite3", terms, limit=2)
    fam_hits = len(matched_entities) + len(matched_models) + len(matched_prefs)

    if not user_cond and fam_hits == 0:
        return {
            "decision": "skip",
            "reason": "no_user_variance_or_familiarity",
            "user_conditioned": False,
            "familiarity_hits": 0,
            "latency_ms": int((time.time() - t0) * 1000),
            "cue_text": "",
        }

    reasons = []
    if user_cond:
        reasons.append("user_conditioned_task")
    if fam_hits > 0:
        reasons.append(f"familiarity_hits={fam_hits}")

    lines = [f"<ep_l1_familiarity_cue decision=\"recall_recommended\" reasons=\"{','.join(reasons)}\">"]
    lines.append(
        "This request is user-conditioned or touches known user memory. "
        "Before answering, consult EP (`user_preference` and `user_recall`/`user_research`) in parallel with any external lookup "
        "so your answer aligns with the user's real role, goals, constraints, and prior decisions."
    )
    if matched_models:
        lines.append("Relevant user mental models: " + " | ".join(matched_models))
    if matched_entities:
        lines.append(
            "Relevant entity states: "
            + " ; ".join(f"{e['key']} [{e['status']}]" for e in matched_entities)
        )
    if matched_prefs:
        lines.append(
            "Relevant preference hints: "
            + " ; ".join(f"[{p['enforcement']}] {p['text']}" for p in matched_prefs)
        )
    if AGENT_TROUBLESHOOT_RE.search(text):
        lines.append(
            "Process memory hint: troubleshooting/failure keywords detected — also consider `agent_recall` to check prior root causes & verified fixes."
        )
    lines.append("</ep_l1_familiarity_cue>")

    return {
        "decision": "recall_recommended",
        "reasons": reasons,
        "user_conditioned": user_cond,
        "familiarity_hits": fam_hits,
        "matched_models": matched_models,
        "matched_entities": matched_entities,
        "matched_prefs": matched_prefs,
        "latency_ms": int((time.time() - t0) * 1000),
        "cue_text": "\n".join(lines),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("l0", help="Build L0 Core Identity Card")
    p_l1 = sub.add_parser("l1", help="Evaluate L1 Familiarity Gate for a prompt")
    p_l1.add_argument("--prompt", required=True)
    args = parser.parse_args()

    if args.cmd == "l0":
        print(json.dumps(build_l0_core_card(), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(evaluate_l1_familiarity(args.prompt), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
