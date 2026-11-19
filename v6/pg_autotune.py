"""Cross-platform, hardware-adaptive PostgreSQL auto-tuner for EP v6 (macOS / Windows / Linux)."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any


def detect_total_ram_bytes() -> int:
    """Detect physical RAM in bytes across macOS, Linux, and Windows."""
    system = platform.system().lower()
    try:
        if system == "darwin":
            out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True, timeout=5).strip()
            return int(out)
        if system == "linux":
            with open("/proc/meminfo", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1]) * 1024
        if system == "windows":
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return int(stat.ullTotalPhys)
    except Exception:
        pass
    return 8 * 1024 * 1024 * 1024  # Conservative 8 GB fallback


def _clamp(val: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, val))


def compute_pg_tuning(ram_bytes: int, cpu_count: int | None = None, *, ssd: bool = True) -> dict[str, str]:
    """Compute safe, adaptive PostgreSQL parameters for any desktop/laptop/server tier."""
    ram_mb = max(1024, int(ram_bytes // (1024 * 1024)))
    cpus = max(1, int(cpu_count or os.cpu_count() or 4))

    shared_buffers_mb = _clamp(ram_mb // 8, 256, 4096)          # 12.5% RAM, [256MB, 4GB]
    effective_cache_mb = _clamp(ram_mb // 2, 1024, 32768)       # 50% RAM,   [1GB, 32GB]
    work_mem_mb = _clamp(ram_mb // 512, 8, 64)                  # RAM/512,   [8MB, 64MB]
    maint_work_mem_mb = _clamp(ram_mb // 32, 128, 1024)         # RAM/32,    [128MB, 1GB]
    max_wal_size_mb = _clamp(ram_mb // 8, 1024, 4096)           # RAM/8,     [1GB, 4GB]
    parallel_gather = _clamp(cpus // 4, 1, 4)

    return {
        "shared_buffers": f"{shared_buffers_mb}MB",
        "effective_cache_size": f"{effective_cache_mb}MB",
        "work_mem": f"{work_mem_mb}MB",
        "maintenance_work_mem": f"{maint_work_mem_mb}MB",
        "max_wal_size": f"{max_wal_size_mb}MB",
        "random_page_cost": "1.1" if ssd else "4.0",
        "max_parallel_workers_per_gather": str(parallel_gather),
        "jit": "off",
        "autovacuum_vacuum_scale_factor": "0.05",
    }


def apply_pg_tuning(params: dict[str, str], *, dsn: str | None = None, user: str | None = None, port: int = 15432, dbname: str = "evolving_profile") -> dict[str, Any]:
    """Apply parameters via portable ALTER SYSTEM SET (requires Postgres reload/restart for shared_buffers)."""
    import psycopg2

    if dsn:
        conn = psycopg2.connect(dsn)
    else:
        kw: dict[str, Any] = {"port": port, "dbname": dbname}
        if user:
            kw["user"] = user
        conn = psycopg2.connect(**kw)
    conn.set_session(autocommit=True)
    applied: dict[str, str] = {}
    try:
        with conn.cursor() as cur:
            for k, v in params.items():
                cur.execute(f"ALTER SYSTEM SET {k} = %s", (v,))
                applied[k] = v
            cur.execute("SELECT pg_reload_conf()")
    finally:
        conn.close()
    return {"status": "applied", "restart_required_for": ["shared_buffers"], "params": applied}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Apply via ALTER SYSTEM SET (default is dry-run)")
    parser.add_argument("--user", default=os.environ.get("PGUSER", "evolving_profile_admin"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PGPORT", "15432")))
    parser.add_argument("--dbname", default=os.environ.get("PGDATABASE", "evolving_profile"))
    parser.add_argument("--dsn", default=os.environ.get("EVOLVING_PROFILE_ADMIN_DSN"))
    args = parser.parse_args()

    ram = detect_total_ram_bytes()
    cpus = os.cpu_count() or 4
    params = compute_pg_tuning(ram, cpus)
    out: dict[str, Any] = {
        "platform": platform.system(),
        "ram_gb": round(ram / (1024 ** 3), 1),
        "cpus": cpus,
        "recommended": params,
    }
    if args.apply:
        out["apply_result"] = apply_pg_tuning(params, dsn=args.dsn, user=args.user, port=args.port, dbname=args.dbname)
    else:
        out["dry_run"] = True
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
