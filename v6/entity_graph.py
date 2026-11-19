"""Generalized, IDF-weighted Entity-Graph link builder for public.memory_links."""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any


def compute_entity_idf_weight(entity_doc_freq: int, total_units: int, *, max_doc_ratio: float = 0.08) -> float | None:
    """Return an IDF-scaled edge weight in [0.60, 0.95], or None if entity is too generic (stop-entity)."""
    if total_units <= 0 or entity_doc_freq < 2:
        return None
    if (entity_doc_freq / total_units) > max_doc_ratio:
        return None
    idf = math.log((total_units + 1.0) / (entity_doc_freq + 1.0))
    max_idf = math.log((total_units + 1.0) / 3.0)
    norm = max(0.0, min(1.0, idf / max_idf)) if max_idf > 0 else 0.5
    return round(0.60 + 0.35 * norm, 4)


def build_entity_links(
    data_root: str | Path | None = None,
    *,
    bank_id: str | None = None,
    max_neighbors_per_entity_unit: int = 4,
    max_doc_ratio: float = 0.05,
    max_entities: int = 3000,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Populate high-precision IDF-weighted entity links in public.memory_links from public.unit_entities."""
    root = Path(data_root or os.environ.get("EP_DATA_ROOT") or Path.home() / ".evolving-profile").expanduser()
    host_adapter = root / "runtime" / "host-adapter"
    if str(host_adapter) not in sys.path:
        sys.path.insert(0, str(host_adapter))

    from lib.exhaustive_query import _connect
    from psycopg2.extras import execute_values

    conn = _connect()
    try:
        with conn.cursor() as cur:
            resolved_bank = bank_id or os.environ.get("EVOLVING_PROFILE_BANK_ID")
            if not resolved_bank:
                cur.execute("SELECT bank_id FROM public.memory_units GROUP BY bank_id ORDER BY count(*) DESC LIMIT 1")
                row = cur.fetchone()
                resolved_bank = row[0] if row else None
            if not resolved_bank:
                return {"status": "no_bank"}

            cur.execute("SELECT count(*) FROM public.memory_units WHERE bank_id = %s", (resolved_bank,))
            total_units = cur.fetchone()[0]
            max_df = max(2, int(total_units * max_doc_ratio))

            # Select specific entities (df between 2 and max_df)
            cur.execute(
                """
                SELECT ue.entity_id, count(DISTINCT ue.unit_id) AS df
                FROM public.unit_entities ue
                JOIN public.memory_units mu ON mu.id = ue.unit_id
                WHERE mu.bank_id = %s
                GROUP BY ue.entity_id
                HAVING count(DISTINCT ue.unit_id) BETWEEN 2 AND %s
                ORDER BY df ASC
                LIMIT %s
                """,
                (resolved_bank, max_df, max_entities),
            )
            entities = cur.fetchall()
            if dry_run:
                return {"status": "dry_run", "bank_id": resolved_bank, "total_units": total_units, "eligible_entities": len(entities), "max_df": max_df}

            to_insert: list[tuple[Any, ...]] = []
            for entity_id, df in entities:
                w = compute_entity_idf_weight(df, total_units, max_doc_ratio=max_doc_ratio)
                if w is None:
                    continue
                cur.execute(
                    """
                    SELECT ue.unit_id
                    FROM public.unit_entities ue
                    JOIN public.memory_units mu ON mu.id = ue.unit_id
                    WHERE ue.entity_id = %s AND mu.bank_id = %s
                    ORDER BY COALESCE(mu.occurred_start, mu.mentioned_at) DESC NULLS LAST
                    LIMIT 40
                    """,
                    (entity_id, resolved_bank),
                )
                uids = [r[0] for r in cur.fetchall()]
                # Connect each unit to its K nearest temporal neighbors sharing this specific entity
                for i, u1 in enumerate(uids):
                    for u2 in uids[i + 1 : i + 1 + max_neighbors_per_entity_unit]:
                        to_insert.append((u1, u2, "entity", entity_id, w, resolved_bank))
                        to_insert.append((u2, u1, "entity", entity_id, w, resolved_bank))

            inserted = 0
            if to_insert:
                sql = (
                    "INSERT INTO public.memory_links (from_unit_id, to_unit_id, link_type, entity_id, weight, bank_id) "
                    "VALUES %s ON CONFLICT DO NOTHING"
                )
                execute_values(cur, sql, to_insert, page_size=1000)
                inserted = cur.rowcount
            conn.commit()
            cur.execute("SELECT count(*) FROM public.memory_links WHERE link_type = 'entity'")
            total_entity_links = cur.fetchone()[0]
            return {
                "status": "ok",
                "eligible_entities": len(entities),
                "candidate_edges": len(to_insert),
                "inserted_edges": inserted,
                "total_entity_links": total_entity_links,
            }
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--bank-id", default=os.environ.get("EVOLVING_PROFILE_BANK_ID"))
    args = parser.parse_args()
    print(json.dumps(build_entity_links(bank_id=args.bank_id, dry_run=args.dry_run), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
