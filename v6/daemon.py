"""EP v6 Unified Event-Driven Scheduler Daemon replacing fragmented polling LaunchAgents."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable


@dataclass
class ScheduledJob:
    name: str
    interval_seconds: float
    handler: Callable[[], dict[str, Any]]
    max_backoff_seconds: float = 1800.0


class SchedulerDaemon:
    """Single resident scheduler with SQLite state, non-overlapping locks, exponential backoff, and quiet idle logs."""

    def __init__(self, state_db: Path, *, clock: Callable[[], float] = time.time):
        self.state_db = Path(state_db)
        self.state_db.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._jobs: dict[str, ScheduledJob] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._stop_event = threading.Event()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.state_db), timeout=10.0)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS job_state (
                    name TEXT PRIMARY KEY,
                    last_run_at REAL NOT NULL DEFAULT 0,
                    next_due_at REAL NOT NULL DEFAULT 0,
                    last_status TEXT NOT NULL DEFAULT 'never',
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    last_summary_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.commit()

    def register(self, job: ScheduledJob) -> None:
        self._jobs[job.name] = job
        self._locks.setdefault(job.name, threading.Lock())
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO job_state (name, next_due_at) VALUES (?, 0)",
                (job.name,),
            )
            conn.commit()

    def run_job(self, name: str, *, force: bool = False) -> dict[str, Any]:
        job = self._jobs[name]
        lock = self._locks[name]
        if not lock.acquire(blocking=False):
            return {"job": name, "status": "already_running"}
        try:
            now = self._clock()
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT next_due_at, consecutive_failures FROM job_state WHERE name=?",
                    (name,),
                ).fetchone()
                next_due, failures = (row[0], row[1]) if row else (0.0, 0)
            if not force and now < next_due:
                return {"job": name, "status": "not_due", "next_due_at": next_due}
            try:
                summary = job.handler() or {}
                status = str(summary.get("status") or "ok")
                with self._connect() as conn:
                    conn.execute(
                        """
                        UPDATE job_state
                        SET last_run_at=?, next_due_at=?, last_status=?, consecutive_failures=0, last_summary_json=?
                        WHERE name=?
                        """,
                        (now, now + job.interval_seconds, status, json.dumps(summary, ensure_ascii=False), name),
                    )
                    conn.commit()
                return {"job": name, "status": status, "summary": summary}
            except Exception as exc:
                failures += 1
                backoff = min(job.max_backoff_seconds, job.interval_seconds * (2 ** min(failures, 6)))
                err = {"error": type(exc).__name__, "detail": str(exc)[:200]}
                with self._connect() as conn:
                    conn.execute(
                        """
                        UPDATE job_state
                        SET last_run_at=?, next_due_at=?, last_status='error', consecutive_failures=?, last_summary_json=?
                        WHERE name=?
                        """,
                        (now, now + backoff, failures, json.dumps(err, ensure_ascii=False), name),
                    )
                    conn.commit()
                return {"job": name, "status": "error", "backoff_seconds": backoff, **err}
        finally:
            lock.release()

    def tick(self) -> list[dict[str, Any]]:
        results = []
        for name in sorted(self._jobs):
            res = self.run_job(name, force=False)
            if res.get("status") != "not_due":
                results.append(res)
        return results

    def status_snapshot(self) -> dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT name, last_run_at, next_due_at, last_status, consecutive_failures, last_summary_json FROM job_state ORDER BY name"
            ).fetchall()
        return {
            "jobs": [
                {
                    "name": r[0],
                    "last_run_at": r[1],
                    "next_due_at": r[2],
                    "last_status": r[3],
                    "consecutive_failures": r[4],
                    "last_summary": json.loads(r[5]),
                }
                for r in rows
            ]
        }

    def stop(self) -> None:
        self._stop_event.set()


def create_daemon_control_server(daemon: SchedulerDaemon, host: str = "127.0.0.1", port: int = 12096) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: dict[str, Any]) -> None:
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            if self.path in ("/health", "/jobs"):
                self._send(200, {"status": "healthy", **daemon.status_snapshot()})
            else:
                self._send(404, {"error": "not_found"})

        def do_POST(self) -> None:
            if self.path.startswith("/trigger/"):
                name = self.path.split("/trigger/", 1)[1].strip("/")
                if name not in daemon._jobs:
                    self._send(404, {"error": "unknown_job"})
                    return
                self._send(200, daemon.run_job(name, force=True))
            else:
                self._send(404, {"error": "not_found"})

        def log_message(self, format: str, *args: Any) -> None:
            return

    return ThreadingHTTPServer((host, port), Handler)


def _run_cmd(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None, timeout: int = 1800) -> dict[str, Any]:
    import os
    import subprocess

    full_env = {**os.environ, **env} if env else None
    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=full_env, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-300:]
        raise RuntimeError(f"exit_{proc.returncode}:{tail}")
    out = (proc.stdout or "").strip()
    last_line = out.splitlines()[-1] if out else ""
    try:
        parsed = json.loads(last_line)
        if isinstance(parsed, dict):
            return {"status": str(parsed.get("status") or "ok"), "output": parsed}
    except Exception:
        pass
    return {"status": "ok", "tail": last_line[:200]}


def _resolve_python(root: Path) -> str:
    import sys

    candidates = [
        root / "runtime" / "python-3.11" / "bin" / "python",
        root / "runtime" / "python-3.11" / "python.exe",
        root / ".venv-test" / "bin" / "python",
        root / ".venv-test" / "Scripts" / "python.exe",
    ]
    for cand in candidates:
        if cand.exists():
            return str(cand)
    return sys.executable


def _run_events_sync_py(py: str, root: Path) -> dict[str, Any]:
    """Cross-platform pure-Python events sync (no zsh / BSD date dependency)."""
    from datetime import datetime, timedelta, timezone

    ev_dir = root / "events"
    if not (ev_dir / "replay.py").exists():
        return {"status": "skipped", "reason": "events_dir_missing"}
    since = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")
    batch = "auto-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    errors: list[str] = []
    for adapter in ("codex", "claude_native", "trae", "antigravity", "workbuddy"):
        try:
            _run_cmd([py, str(ev_dir / "replay.py"), "--adapter", adapter, "--since", since, "--batch-id", f"{batch}-{adapter}"], timeout=300)
        except Exception as exc:
            errors.append(f"{adapter}:{exc}")
    # Session summaries call the model per changed session; a busy day of long
    # Antigravity sessions exceeded 600 s four times on 11-22/23, so it gets more room.
    for script, extra, limit in (
        ("ledger.py", ["--since", since], 600),
        ("entity_state.py", [], 600),
        ("summarize.py", ["--level", "session", "--since", since], 2400),
    ):
        sp = ev_dir / script
        if sp.exists():
            try:
                _run_cmd([py, str(sp), *extra], timeout=limit)
            except Exception as exc:
                errors.append(f"{script}:{exc}")
    if errors:
        return {"status": "partial", "errors": errors[:5]}
    return {"status": "ok", "since": since}


def build_production_jobs(data_root: str | Path, *, bank_id: str | None = None) -> list[ScheduledJob]:
    """Build unified cross-platform EP v6 background jobs (zero shell-script dependency)."""
    import os

    root = Path(data_root).expanduser()
    py = _resolve_python(root)
    host_adapter = root / "runtime" / "host-adapter"
    guidance_dir = root / "runtime" / "guidance"
    resolved_bank = bank_id or os.environ.get("EVOLVING_PROFILE_BANK_ID") or ""
    path_sep = os.pathsep

    context_cmd = [
        py,
        str(host_adapter / "context-incremental-worker.py"),
        "--state-root", str(root),
        "--session-root", str(Path.home() / ".codex" / "sessions"),
        "--session-root", str(root / "claude-code" / "rollouts"),
        "--process", "--max-jobs", "4", "--scan-limit", "256",
        "--new-after", "2026-10-08T00:00:00Z",
    ]
    if resolved_bank:
        context_cmd.extend(["--bank-id", resolved_bank])

    guidance_env = {
        "EVOLVING_PROFILE_STATE_ROOT": str(root),
        "EVOLVING_PROFILE_SOURCE_API_URL": os.environ.get("EVOLVING_PROFILE_SOURCE_API_URL", "http://127.0.0.1:12088"),
        "PYTHONPATH": str(guidance_dir),
    }
    pg_user = os.environ.get("PGUSER", "evolving_profile_admin")
    pg_port = os.environ.get("PGPORT", "15432")
    pg_db = os.environ.get("PGDATABASE", "evolving_profile")
    default_pg_url = (
        f"postgresql://{pg_user}@127.0.0.1:{pg_port}/{pg_db}"
        if os.name == "nt"
        else f"postgresql:///{pg_db}?port={pg_port}&user={pg_user}"
    )
    pg_url = (
        os.environ.get("EVOLVING_PROFILE_API_DATABASE_URL")
        or os.environ.get("EP_PG_URL")
        or os.environ.get("DATABASE_URL")
        or default_pg_url
    )
    catalog_env = {
        "PYTHONPATH": f"{host_adapter}{path_sep}{guidance_dir}",
        "EVOLVING_PROFILE_API_DATABASE_URL": pg_url,
    }
    topic_catalog_cmd = [
        py,
        str(guidance_dir / "topic_catalog_worker.py"),
        "--output", str(root / "catalog" / "topics.sqlite3"),
        "--markdown", str(root / "catalog" / "topics"),
        "--limit", "500",
    ]
    topic_semantics_cmd = [
        py,
        str(guidance_dir / "bank_hierarchy.py"),
        "--catalog", str(root / "catalog" / "topics.sqlite3"),
        "--output", str(root / "catalog" / "corpus-navigation.json"),
    ]
    if resolved_bank:
        topic_semantics_cmd.extend(["--bank-id", resolved_bank])

    storage_env = {**os.environ, "EVOLVING_PROFILE_STATE_ROOT": str(root)}
    tiering_env = {**storage_env, "EVOLVING_PROFILE_BANK_ID": resolved_bank}
    return [
        ScheduledJob("retention-worker", 30.0, lambda: _run_cmd([py, str(host_adapter / "retention_worker.py"), "--once"], timeout=120)),
        ScheduledJob("context-worker", 60.0, lambda: _run_cmd(context_cmd, timeout=600)),
        ScheduledJob("guidance-worker", 300.0, lambda: _run_cmd([py, str(guidance_dir / "guidance_worker.py")], cwd=guidance_dir, env=guidance_env, timeout=600)),
        ScheduledJob("topic-catalog", 300.0, lambda: _run_cmd(topic_catalog_cmd, env=catalog_env, timeout=600)),
        ScheduledJob("topic-semantics", 600.0, lambda: _run_cmd(topic_semantics_cmd, env=catalog_env, timeout=900)),
        ScheduledJob("context-links", 3600.0, lambda: _run_cmd([py, str(host_adapter / "context-bank-associations.py")], cwd=host_adapter, timeout=600)),
        ScheduledJob("events-sync", 3600.0, lambda: _run_events_sync_py(py, root)),
        # Hosts without a native retain hook (Antigravity) reach the Bank through the ledger.
        ScheduledJob("ledger-bank-bridge", 900.0, lambda: _run_cmd([py, "-m", "v6.ledger_bank_bridge"], timeout=600)),
        ScheduledJob("incremental-sync", 3600.0, lambda: _run_cmd([py, "-m", "v6.incremental_sync"], timeout=300)),
        ScheduledJob("entity-graph", 3600.0, lambda: _run_cmd([py, "-m", "v6.entity_graph"], timeout=600)),
        ScheduledJob("entity-state", 3600.0, lambda: _run_cmd([py, "-m", "v6.entity_state"], timeout=300)),
        ScheduledJob("artifact-index", 3600.0, lambda: _run_cmd([py, "-m", "v6.artifact_index"], timeout=300)),
        ScheduledJob("ontology-hygiene", 86400.0, lambda: _run_cmd([py, "-m", "v6.ontology_hygiene", "--apply"], timeout=600)),
        ScheduledJob("sqlite-hygiene", 86400.0, lambda: _run_cmd([py, "-m", "v6.sqlite_hygiene", "--apply"], timeout=600)),
        ScheduledJob("pg-analyze", 86400.0, lambda: _run_cmd([py, "-m", "v6.pg_autotune", "--analyze-only"], timeout=300)),
        # Storage governance (6.2): archive tool rows of sessions idle > 30 days
        # (self-skips unless the installed ledger carries the archived-session guard).
        ScheduledJob("events-retention", 86400.0, lambda: _run_cmd([py, "-m", "v6.events_retention"], env=storage_env, timeout=900)),
    ] + ([
        # Admission tiering (6.2): new low-value units and near-duplicates go warm.
        ScheduledJob("memory-tiering", 600.0, lambda: _run_cmd([py, "-m", "v6.memory_tiering", "admit"],
                                                               env=tiering_env, timeout=600)),
    ] if resolved_bank else [])


def _scan_watch_signature(paths: list[Path]) -> float:
    latest = 0.0
    for base in paths:
        if not base.exists():
            continue
        try:
            st = base.stat().st_mtime
            if st > latest:
                latest = st
            for child in base.iterdir():
                cst = child.stat().st_mtime
                if cst > latest:
                    latest = cst
        except OSError:
            continue
    return latest


def main() -> int:
    import argparse
    import os
    import signal

    parser = argparse.ArgumentParser(description="EP v6 Unified Event-Driven Scheduler Daemon")
    parser.add_argument("--data-root", default=os.environ.get("EP_DATA_ROOT") or str(Path.home() / ".evolving-profile"))
    parser.add_argument("--bank-id", default=os.environ.get("EVOLVING_PROFILE_BANK_ID"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=12096)
    parser.add_argument("--tick-seconds", type=float, default=5.0)
    args = parser.parse_args()

    root = Path(args.data_root).expanduser()
    daemon = SchedulerDaemon(root / "state" / "v6-scheduler.sqlite3")
    for job in build_production_jobs(root, bank_id=args.bank_id):
        daemon.register(job)

    watch_dirs = [
        Path.home() / ".codex" / "sessions",
        root / "claude-code" / "rollouts",
        Path.home() / ".gemini" / "antigravity" / "brain",
    ]
    last_watch_mtime = _scan_watch_signature(watch_dirs)

    server = create_daemon_control_server(daemon, host=args.host, port=args.port)
    control_thread = threading.Thread(target=server.serve_forever, daemon=True)
    control_thread.start()

    def _handle_signal(_sig, _frame):
        daemon.stop()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    print(json.dumps({"status": "started", "host": args.host, "port": args.port, "jobs": sorted(daemon._jobs)}, ensure_ascii=False), flush=True)
    last_states: dict[str, str] = {}
    try:
        while not daemon._stop_event.is_set():
            # Event-driven wake: when new session files appear, immediately mark context-worker due.
            cur_mtime = _scan_watch_signature(watch_dirs)
            if cur_mtime > last_watch_mtime:
                last_watch_mtime = cur_mtime
                with daemon._connect() as conn:
                    conn.execute("UPDATE job_state SET next_due_at=0 WHERE name IN ('context-worker', 'retention-worker')")
                    conn.commit()
            results = daemon.tick()
            for res in results:
                name, st = res.get("job", ""), res.get("status", "")
                # Quiet idle: log only on error or state transition.
                if st == "error" or st != "idle" or last_states.get(name) != st:
                    print(json.dumps(res, ensure_ascii=False), flush=True)
                last_states[name] = st
            daemon._stop_event.wait(args.tick_seconds)
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
