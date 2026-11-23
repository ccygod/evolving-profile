"""Ledger -> Bank bridge for hosts that have no native retain hook.

Some hosts (Antigravity today) are captured into ``ep_events.events`` by the
event adapters, so the work ledger and scenario summaries see them, but nothing
ever retains their dialogue into the Bank.  ``user_recall`` / ``user_research``
therefore cannot find that work.  This bridge closes the gap generically: it
reads already-normalised user/assistant messages from the event ledger and
submits them to the Bank retain endpoint in bounded batches.

Guarantees
- Per-session watermark (``seq``), committed only after the API accepts a batch.
- Deterministic ``document_id`` / ``operation_id``: a retried batch is a no-op.
- Sessions are retained only after they have been idle ``--min-idle-min``.
- Sessions that were already retained manually (existing ``<host>-<sid8>-*``
  documents) are bootstrapped past the manually covered range to avoid
  duplicate memories.
- Read-only on the ledger; the only write is the retain API call + state file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from v6.secret_redaction import redact_text

DEFAULT_HOSTS = ("antigravity",)
MAX_MSG_CHARS = 4000
MAX_BATCH_CHARS = 12000
MIN_MSG_CHARS = 8
HOST_LABEL = {"antigravity": "Antigravity"}


def _connect(dsn: str | None):
    import psycopg2

    if dsn:
        return psycopg2.connect(dsn)
    return psycopg2.connect(
        port=int(os.environ.get("PGPORT", "15432")),
        dbname=os.environ.get("PGDATABASE", "evolving_profile"),
        user=os.environ.get("PGUSER", "evolving_profile_admin"),
    )


def _load_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict) and isinstance(value.get("sessions"), dict):
            return value
    except (OSError, ValueError):
        pass
    return {"version": 1, "sessions": {}}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"…[truncated {len(text) - limit} chars]"


def _bootstrap_seq(cur, bank: str, host: str, session_id: str) -> int:
    """Return the last seq already covered by manual/legacy retains of this session."""
    cur.execute(
        "SELECT max(created_at) FROM public.documents WHERE bank_id=%s AND id LIKE %s",
        (bank, f"{host}-{session_id[:8]}-%"),
    )
    covered_until = cur.fetchone()[0]
    if covered_until is None:
        return -1
    cur.execute(
        "SELECT coalesce(max(seq), -1) FROM ep_events.events WHERE host=%s AND session_id=%s AND occurred_at <= %s",
        (host, session_id, covered_until),
    )
    return int(cur.fetchone()[0])


def _batches(rows: list[tuple]) -> list[list[tuple]]:
    out, cur, size = [], [], 0
    for row in rows:
        piece = len(row[3]) + 16
        if cur and size + piece > MAX_BATCH_CHARS:
            out.append(cur)
            cur, size = [], 0
        cur.append(row)
        size += piece
    if cur:
        out.append(cur)
    return out


def _post(api_base: str, bank: str, item: dict[str, Any], operation_id: str, timeout: int = 45) -> tuple[int, Any]:
    url = f"{api_base.rstrip('/')}/v1/default/banks/{bank}/memories"
    payload = {"async": True, "operation_id": operation_id, "items": [item]}
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode("utf-8", "replace")[:300]}
    except Exception as e:  # network failure: keep watermark, retry next run
        return 0, {"error": f"{type(e).__name__}: {e}"}


def run(*, bank: str, hosts: tuple[str, ...], state_path: Path, api_base: str, dsn: str | None,
        min_idle_min: float = 15.0, dry_run: bool = False, max_batches: int = 200) -> dict[str, Any]:
    state = _load_state(state_path)
    conn = _connect(dsn)
    summary: dict[str, Any] = {"status": "ok", "dry_run": dry_run, "hosts": list(hosts), "submitted": 0,
                               "sessions_processed": 0, "sessions_active_skipped": 0, "bootstrapped": 0,
                               "failed": 0, "messages": 0}
    try:
        conn.set_session(readonly=True, autocommit=True)
        cur = conn.cursor()
        cur.execute(
            """SELECT host, session_id, max(seq), max(occurred_at),
                      extract(epoch FROM now() - max(occurred_at)) / 60.0
               FROM ep_events.events
               WHERE host = ANY(%s) AND event_type IN ('user_message','assistant_message')
               GROUP BY host, session_id ORDER BY max(occurred_at)""",
            (list(hosts),),
        )
        sessions = cur.fetchall()
        for host, sid, max_seq, _last_at, idle_min in sessions:
            key = f"{host}:{sid}"
            rec = state["sessions"].get(key)
            if rec is None:
                seq0 = _bootstrap_seq(cur, bank, host, sid)
                rec = {"seq": seq0, "batches": 0}
                if seq0 >= 0:
                    summary["bootstrapped"] += 1
                if not dry_run:
                    state["sessions"][key] = rec
            if int(max_seq) <= int(rec["seq"]):
                continue
            if idle_min is not None and float(idle_min) < min_idle_min:
                summary["sessions_active_skipped"] += 1
                continue
            cur.execute(
                """SELECT seq, role, occurred_at, body, project_path
                   FROM ep_events.events
                   WHERE host=%s AND session_id=%s AND seq > %s
                     AND event_type IN ('user_message','assistant_message')
                     AND NOT coalesce(is_sidechain, false) AND coalesce(body,'') <> ''
                   ORDER BY seq""",
                (host, sid, int(rec["seq"])),
            )
            rows = [(s, r, t, _truncate(redact_text(b.strip()), MAX_MSG_CHARS), p) for s, r, t, b, p in cur.fetchall()
                    if b and len(b.strip()) >= MIN_MSG_CHARS]
            if not rows:
                rec["seq"] = int(max_seq)
                continue
            summary["sessions_processed"] += 1
            project = next((p for *_x, p in rows if p), "") or ""
            ok = True
            for part, batch in enumerate(_batches(rows), 1):
                if summary["submitted"] >= max_batches:
                    ok = False
                    break
                first, last = batch[0][0], batch[-1][0]
                src = {"session_id": sid, "host": host, "project": project, "seq_range": [first, last],
                       "recorded_at": batch[0][2].astimezone(timezone.utc).isoformat(),
                       "time_semantics": "host_recorded_time_not_claim_valid_time",
                       "capture": "ep_events ledger bridge"}
                body = [f"[source_record: {json.dumps(src, ensure_ascii=False)}]"]
                for _s, role, _t, text, _p in batch:
                    body.append(f"[role: {role}]\n{text}\n[{role}:end]")
                content = "\n\n".join(body)
                digest = hashlib.sha256(f"{host}:{sid}:{first}:{last}:{content}".encode()).hexdigest()
                label = HOST_LABEL.get(host, host)
                name = os.environ.get("EP_USER_DISPLAY_NAME", "").strip()
                speaker = f"{name}（人类用户）" if name else "人类用户"
                tags = [host, "ledger-bridge", f"host:{host}", f"host_family:{host}"]
                item = {
                    "content": content,
                    "context": (f"说话者：{speaker}与 {label} 智能体在本地客户端中的会话，由 EP 事件账本桥接自动捕获，未经人工筛选。"
                                "[role: user] 为人类发言，[role: assistant] 为智能体输出；助手自报的执行结果、状态与判断不等于用户确认。"),
                    "timestamp": batch[0][2].astimezone().isoformat(),
                    "metadata": {"source": f"{host}-ledger-bridge", "host": host, "project": project,
                                 "session_ids": sid, "seq_range": f"{first}-{last}", "turn_count": str(len(batch)),
                                 "estimated_tokens": str(len(content) // 3)},
                    "tags": tags,
                    "document_id": f"{host}-ledger-{sid[:8]}-{digest[:12]}",
                }
                summary["messages"] += len(batch)
                if dry_run:
                    summary.setdefault("preview", []).append(
                        {"doc": item["document_id"], "turns": len(batch), "chars": len(content), "ts": item["timestamp"]})
                    summary["submitted"] += 1
                    continue
                op_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"ep-ledger-bridge:{bank}:{host}:{sid}:{first}:{last}:{digest}"))
                status, resp = _post(api_base, bank, item, op_id)
                if status in (200, 201, 202, 409):
                    summary["submitted"] += 1
                    rec["seq"] = int(last)
                    rec["batches"] = int(rec.get("batches", 0)) + 1
                    rec["updated_at"] = datetime.now(timezone.utc).isoformat()
                    _save_state(state_path, state)
                else:
                    ok = False
                    summary["failed"] += 1
                    summary.setdefault("errors", []).append(f"{key}: status={status} {str(resp)[:160]}")
                    break
            if ok and not dry_run:
                rec["seq"] = max(int(rec["seq"]), int(max_seq))
        if not dry_run:
            _save_state(state_path, state)
        if summary["failed"]:
            summary["status"] = "partial"
        summary.pop("preview", None) if not dry_run else None
        if dry_run and "preview" in summary:
            summary["preview"] = summary["preview"][:30]
        return summary
    finally:
        conn.close()


def main() -> int:
    root = Path(os.environ.get("EP_DATA_ROOT") or Path.home() / ".evolving-profile").expanduser()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bank", default=os.environ.get("EVOLVING_PROFILE_BANK_ID", ""))
    ap.add_argument("--host", action="append", dest="hosts")
    ap.add_argument("--state", default=str(root / "state" / "ledger-bank-bridge.json"))
    ap.add_argument("--api-base", default=os.environ.get("EP_API_BASE", "http://127.0.0.1:12088"))
    ap.add_argument("--dsn", default=os.environ.get("EVOLVING_PROFILE_ADMIN_DSN") or os.environ.get("EVOLVING_PROFILE_API_DATABASE_URL"))
    ap.add_argument("--min-idle-min", type=float, default=15.0)
    ap.add_argument("--max-batches", type=int, default=200)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not args.bank:
        print(json.dumps({"status": "skipped", "reason": "bank_id_missing"}))
        return 0
    result = run(bank=args.bank, hosts=tuple(args.hosts or DEFAULT_HOSTS), state_path=Path(args.state),
                 api_base=args.api_base, dsn=args.dsn, min_idle_min=args.min_idle_min,
                 dry_run=args.dry_run, max_batches=args.max_batches)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "ok" else 3


if __name__ == "__main__":
    raise SystemExit(main())
