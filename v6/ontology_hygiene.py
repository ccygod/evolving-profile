"""Cross-platform ontology & preference hygiene engine for EP v6 (macOS / Windows / Linux).

Handles:
1. Deduplicating active mental models in guidance SQLite by normalized title (keeps newest revision).
2. Deactivating stale `superseded` preference units that remain active=1.
3. Enriching active preference units with first-class `scope` (global | project_scoped | task_local)
   and `enforcement` (hard_guardrail | default_policy | soft_style), isolating task_local from default injection.
4. Cleaning ephemeral test/template banks (hermes_tmpl_*, test_*, test-*, load-test-*, bench_*) and their orphan models.
5. Soft-demoting unconfirmed assistant experiences (`Assistant statement (not user-confirmed)`) and <20-char world fragments
   via metadata flags (`trust_level`, `recall_demoted`, `fragment_noise`).
6. Pruning aged singleton entities (`mention_count <= 1`, older than N days, unreferenced by memory_links).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any


TEST_BANK_REGEX = r"^(hermes_tmpl_|test[_-]|load-test-|bench_)"

HARD_GUARDRAIL_KEYWORDS = (
    "严禁", "禁止", "绝不", "不得", "不可", "不能擅自", "必须先", "红线", "授权边界",
    "回退", "备份", "泄露", "密钥", "secret", "never ", "must not", "do not delete", "guardrail",
)
PROJECT_SCOPED_KEYWORDS = (
    "ppt", "飞书", "crm", "审批", "高校", "政企", "trainer", "小黛", "hermes",
    "脚本", "sql", "launchagent", "plist", "部署", "仓库",
)


def classify_preference_enforcement(payload: dict[str, Any]) -> str:
    """Classify a preference unit into hard_guardrail | default_policy | soft_style."""
    existing = str(payload.get("enforcement") or "").strip().lower()
    if existing in {"hard_guardrail", "default_policy", "soft_style"}:
        return existing

    text = " ".join(
        [
            str(payload.get("text") or ""),
            " ".join(payload.get("exceptions") or []) if isinstance(payload.get("exceptions"), list) else "",
            str(payload.get("effect_on_action") or ""),
        ]
    ).lower()
    if any(kw in text for kw in HARD_GUARDRAIL_KEYWORDS):
        return "hard_guardrail"

    nature = str(payload.get("nature") or "").strip().lower()
    category = str(payload.get("primary_category") or "").strip().lower()
    if nature in {"explicit_requirement", "declared_preference"} and category != "communication":
        return "default_policy"
    if category == "communication" or nature == "inferred_pattern":
        return "soft_style"
    return "default_policy"


def classify_preference_scope(payload: dict[str, Any]) -> str:
    """Classify a preference unit into global | project_scoped | task_local."""
    existing = str(payload.get("scope") or "").strip().lower()
    if existing in {"global", "project_scoped", "task_local"}:
        return existing

    nature = str(payload.get("nature") or "").strip().lower()
    if nature == "task_local":
        return "task_local"

    text = " ".join(
        [
            str(payload.get("text") or ""),
            " ".join(payload.get("applies_when") or []) if isinstance(payload.get("applies_when"), list) else "",
        ]
    ).lower()
    if any(kw in text for kw in PROJECT_SCOPED_KEYWORDS):
        return "project_scoped"
    return "global"


def _norm_title(title: str) -> str:
    return re.sub(r"\s+", " ", (title or "").strip().lower())


def dedupe_and_enrich_guidance(sqlite_path: str | Path, *, dry_run: bool = False) -> dict[str, Any]:
    """Deduplicate active mental models, deactivate superseded units, and enrich scope/enforcement."""
    path = Path(sqlite_path)
    if not path.exists():
        return {"status": "skipped", "reason": f"sqlite not found: {path}"}

    con = sqlite3.connect(str(path))
    cur = con.cursor()
    stats: dict[str, Any] = {
        "duplicate_models_deactivated": 0,
        "superseded_units_deactivated": 0,
        "task_local_units_isolated": 0,
        "units_enriched": 0,
        "enforcement_counts": {"hard_guardrail": 0, "default_policy": 0, "soft_style": 0},
        "scope_counts": {"global": 0, "project_scoped": 0, "task_local": 0},
        "dry_run": dry_run,
    }
    try:
        # 1. Deduplicate active mental models by normalized title (keep newest created_at / highest revision)
        rows = cur.execute(
            "SELECT model_id, revision, payload_json, created_at FROM model_revisions WHERE active = 1 ORDER BY created_at DESC, revision DESC"
        ).fetchall()
        seen_titles: set[str] = set()
        dup_models: list[tuple[str, int]] = []
        for model_id, rev, pj, _created in rows:
            try:
                data = json.loads(pj)
            except Exception:
                continue
            title_key = _norm_title(str(data.get("title") or data.get("name") or model_id))
            if title_key in seen_titles:
                dup_models.append((model_id, rev))
            else:
                seen_titles.add(title_key)
        stats["duplicate_models_deactivated"] = len(dup_models)
        if not dry_run and dup_models:
            for model_id, rev in dup_models:
                cur.execute(
                    "UPDATE model_revisions SET active = 0 WHERE model_id = ? AND revision = ?",
                    (model_id, rev),
                )

        # 2. Deactivate active unit_revisions whose latest audit state is superseded
        superseded_ids = {
            r[0]
            for r in cur.execute(
                "SELECT unit_id FROM unit_audits WHERE state = 'superseded'"
            ).fetchall()
        }
        active_units = cur.execute(
            "SELECT unit_id, revision, payload_json FROM unit_revisions WHERE active = 1"
        ).fetchall()
        for unit_id, rev, pj in active_units:
            try:
                data = json.loads(pj)
            except Exception:
                continue
            if unit_id in superseded_ids:
                stats["superseded_units_deactivated"] += 1
                if not dry_run:
                    cur.execute(
                        "UPDATE unit_revisions SET active = 0 WHERE unit_id = ? AND revision = ?",
                        (unit_id, rev),
                    )
                continue

            scope = classify_preference_scope(data)
            enforcement = classify_preference_enforcement(data)
            stats["scope_counts"][scope] = stats["scope_counts"].get(scope, 0) + 1
            stats["enforcement_counts"][enforcement] = stats["enforcement_counts"].get(enforcement, 0) + 1

            changed = False
            if data.get("scope") != scope:
                data["scope"] = scope
                changed = True
            if data.get("enforcement") != enforcement:
                data["enforcement"] = enforcement
                changed = True
            if scope == "task_local":
                stats["task_local_units_isolated"] += 1
                if data.get("inject_default") is not False:
                    data["inject_default"] = False
                    changed = True
            else:
                if data.get("inject_default") is not True:
                    data["inject_default"] = True
                    changed = True

            if changed:
                stats["units_enriched"] += 1
                if not dry_run:
                    cur.execute(
                        "UPDATE unit_revisions SET payload_json = ? WHERE unit_id = ? AND revision = ?",
                        (json.dumps(data, ensure_ascii=False), unit_id, rev),
                    )

        if not dry_run:
            con.commit()
    finally:
        con.close()
    return stats


def _connect_pg(dsn: str | None = None, *, user: str | None = None, port: int = 15432, dbname: str = "evolving_profile"):
    import psycopg2

    if dsn:
        return psycopg2.connect(dsn)
    kw: dict[str, Any] = {"port": port, "dbname": dbname}
    if user:
        kw["user"] = user
    return psycopg2.connect(**kw)


def clean_test_banks_and_models(
    *, dsn: str | None = None, user: str | None = None, port: int = 15432, dbname: str = "evolving_profile", dry_run: bool = False
) -> dict[str, Any]:
    """Remove ephemeral test/template banks and their orphan mental_models / memory_units."""
    conn = _connect_pg(dsn, user=user, port=port, dbname=dbname)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT bank_id FROM public.banks WHERE bank_id ~ %s", (TEST_BANK_REGEX,))
            bank_ids = [r[0] for r in cur.fetchall()]
            if not bank_ids:
                return {"test_banks_found": 0, "deleted": False, "dry_run": dry_run}

            cur.execute("SELECT count(*) FROM public.mental_models WHERE bank_id = ANY(%s)", (bank_ids,))
            mm_cnt = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM public.memory_units WHERE bank_id = ANY(%s)", (bank_ids,))
            mu_cnt = cur.fetchone()[0]

            if not dry_run:
                # Delete dependent rows safely in FK order
                cur.execute(
                    "DELETE FROM public.memory_links WHERE bank_id = ANY(%s)",
                    (bank_ids,),
                )
                cur.execute(
                    "DELETE FROM public.unit_entities WHERE unit_id IN (SELECT id FROM public.memory_units WHERE bank_id = ANY(%s))",
                    (bank_ids,),
                )
                cur.execute("DELETE FROM public.entities WHERE bank_id = ANY(%s)", (bank_ids,))
                cur.execute("DELETE FROM public.mental_models WHERE bank_id = ANY(%s)", (bank_ids,))
                cur.execute("DELETE FROM public.memory_units WHERE bank_id = ANY(%s)", (bank_ids,))
                cur.execute("DELETE FROM public.documents WHERE bank_id = ANY(%s)", (bank_ids,))
                cur.execute("DELETE FROM public.banks WHERE bank_id = ANY(%s)", (bank_ids,))
                conn.commit()
            return {
                "test_banks_found": len(bank_ids),
                "mental_models_removed": mm_cnt,
                "memory_units_removed": mu_cnt,
                "dry_run": dry_run,
            }
    finally:
        conn.close()


def tag_unconfirmed_experiences_and_fragments(
    *, dsn: str | None = None, user: str | None = None, port: int = 15432, dbname: str = "evolving_profile", dry_run: bool = False
) -> dict[str, Any]:
    """Soft-demote unconfirmed assistant experiences and <20-char world fragments via metadata jsonb."""
    conn = _connect_pg(dsn, user=user, port=port, dbname=dbname)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) FROM public.memory_units
                WHERE fact_type = 'experience'
                  AND text LIKE 'Assistant statement%%'
                  AND COALESCE(metadata->>'trust_level', '') <> 'assistant_unconfirmed'
                """
            )
            asst_cnt = cur.fetchone()[0]

            cur.execute(
                """
                SELECT count(*) FROM public.memory_units
                WHERE fact_type = 'world'
                  AND length(trim(text)) < 20
                  AND COALESCE(metadata->>'fragment_noise', '') <> 'true'
                """
            )
            frag_cnt = cur.fetchone()[0]

            if not dry_run:
                if asst_cnt > 0:
                    cur.execute(
                        """
                        UPDATE public.memory_units
                        SET metadata = COALESCE(metadata, '{}'::jsonb)
                                       || '{"trust_level": "assistant_unconfirmed", "recall_demoted": true}'::jsonb
                        WHERE fact_type = 'experience'
                          AND text LIKE 'Assistant statement%%'
                          AND COALESCE(metadata->>'trust_level', '') <> 'assistant_unconfirmed'
                        """
                    )
                if frag_cnt > 0:
                    cur.execute(
                        """
                        UPDATE public.memory_units
                        SET metadata = COALESCE(metadata, '{}'::jsonb)
                                       || '{"fragment_noise": true, "recall_demoted": true}'::jsonb
                        WHERE fact_type = 'world'
                          AND length(trim(text)) < 20
                          AND COALESCE(metadata->>'fragment_noise', '') <> 'true'
                        """
                    )
                conn.commit()
            return {
                "assistant_unconfirmed_tagged": asst_cnt,
                "world_fragments_tagged": frag_cnt,
                "dry_run": dry_run,
            }
    finally:
        conn.close()


def prune_singleton_entities(
    *,
    dsn: str | None = None,
    user: str | None = None,
    port: int = 15432,
    dbname: str = "evolving_profile",
    min_age_days: int = 30,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Prune unreferenced singleton entities older than min_age_days."""
    conn = _connect_pg(dsn, user=user, port=port, dbname=dbname)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) FROM public.entities e
                WHERE COALESCE(e.mention_count, 0) <= 1
                  AND e.last_seen < now() - make_interval(days => %s)
                  AND NOT EXISTS (SELECT 1 FROM public.memory_links ml WHERE ml.entity_id = e.id)
                """,
                (min_age_days,),
            )
            candidates = cur.fetchone()[0]
            deleted = 0
            if not dry_run and candidates > 0:
                cur.execute(
                    """
                    WITH doomed AS (
                        SELECT e.id FROM public.entities e
                        WHERE COALESCE(e.mention_count, 0) <= 1
                          AND e.last_seen < now() - make_interval(days => %s)
                          AND NOT EXISTS (SELECT 1 FROM public.memory_links ml WHERE ml.entity_id = e.id)
                    ),
                    del_ue AS (
                        DELETE FROM public.unit_entities WHERE entity_id IN (SELECT id FROM doomed)
                    )
                    DELETE FROM public.entities WHERE id IN (SELECT id FROM doomed)
                    """,
                    (min_age_days,),
                )
                deleted = cur.rowcount
                conn.commit()
            return {
                "singleton_candidates": candidates,
                "singleton_deleted": deleted,
                "min_age_days": min_age_days,
                "dry_run": dry_run,
            }
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Apply hygiene changes (default: dry-run)")
    parser.add_argument("--guidance-db", default=os.environ.get("EVOLVING_PROFILE_GUIDANCE_DB", str(Path.home() / ".evolving-profile" / "guidance-v1" / "guidance.sqlite3")))
    parser.add_argument("--user", default=os.environ.get("PGUSER", "evolving_profile_admin"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PGPORT", "15432")))
    parser.add_argument("--dbname", default=os.environ.get("PGDATABASE", "evolving_profile"))
    parser.add_argument("--dsn", default=os.environ.get("EVOLVING_PROFILE_ADMIN_DSN"))
    parser.add_argument("--singleton-age-days", type=int, default=30)
    args = parser.parse_args()

    dry = not args.apply
    report = {
        "guidance": dedupe_and_enrich_guidance(args.guidance_db, dry_run=dry),
        "test_banks": clean_test_banks_and_models(dsn=args.dsn, user=args.user, port=args.port, dbname=args.dbname, dry_run=dry),
        "memory_trust": tag_unconfirmed_experiences_and_fragments(dsn=args.dsn, user=args.user, port=args.port, dbname=args.dbname, dry_run=dry),
        "singleton_entities": prune_singleton_entities(dsn=args.dsn, user=args.user, port=args.port, dbname=args.dbname, min_age_days=args.singleton_age_days, dry_run=dry),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
