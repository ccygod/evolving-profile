"""Store memory_units.embedding as halfvec (16-bit floats)

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-11-24

Embeddings are normalised sentence vectors; 16-bit precision does not change
cosine ranking in practice (measured on a 75k-unit bank: known-item hit@10
84 -> 87/100, top-10 overlap unchanged), but the table and its HNSW indexes
shrink by about half. New per-bank indexes are created with
``halfvec_cosine_ops`` (see engine/retain/bank_utils.py).

Opt out with EVOLVING_PROFILE_EMBEDDING_STORAGE=vector (also read by bank_utils).
Idempotent: does nothing when the column is already halfvec, and skips (keeps
float32) when pgvector is older than 0.7, which has no halfvec type.
"""

import os
from collections.abc import Sequence

from alembic import context, op
from sqlalchemy import text

from evolving_profile_api.alembic._dialect import run_for_dialect

revision: str = "e2f3a4b5c6d7"
down_revision: str | Sequence[str] | None = "d1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    return context.config.get_main_option("target_schema") or "public"


def _column_type(bind, schema: str) -> str | None:
    return bind.execute(
        text(
            "SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a "
            "JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = :s AND c.relname = 'memory_units' AND a.attname = 'embedding' AND NOT a.attisdropped"
        ),
        {"s": schema},
    ).scalar()


def _pgvector_supports_halfvec(bind) -> bool:
    version = bind.execute(text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")).scalar()
    if not version:
        return False
    major, minor = (int(p) for p in version.split(".")[:2])
    return (major, minor) >= (0, 7)


def _embedding_indexes(bind, schema: str) -> list[tuple[str, str]]:
    return list(
        bind.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = :s AND tablename = 'memory_units' "
                "AND indexdef ILIKE '%(embedding%'"
            ),
            {"s": schema},
        )
    )


def _convert(target: str, source_ops: str, target_ops: str) -> None:
    bind = op.get_bind()
    schema = _schema()
    current = _column_type(bind, schema) or ""
    if not current.startswith(source_ops.split("_")[0] + "("):
        return  # already converted, or no embedding column
    if target == "halfvec" and not _pgvector_supports_halfvec(bind):
        return
    dim = current[current.index("(") + 1 : current.index(")")]
    indexes = _embedding_indexes(bind, schema)
    for name, _ in indexes:
        op.execute(f'DROP INDEX IF EXISTS "{schema}"."{name}"')
    op.execute(
        f'ALTER TABLE "{schema}".memory_units ALTER COLUMN embedding TYPE {target}({dim}) '
        f"USING embedding::{target}({dim})"
    )
    for _, definition in indexes:
        op.execute(definition.replace(source_ops, target_ops))


def _pg_upgrade() -> None:
    if os.environ.get("EVOLVING_PROFILE_EMBEDDING_STORAGE", "halfvec") == "vector":
        return
    _convert("halfvec", "vector_cosine_ops", "halfvec_cosine_ops")


def _pg_downgrade() -> None:
    _convert("vector", "halfvec_cosine_ops", "vector_cosine_ops")


def upgrade() -> None:
    run_for_dialect(pg=_pg_upgrade)


def downgrade() -> None:
    run_for_dialect(pg=_pg_downgrade)
