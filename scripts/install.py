#!/usr/bin/env python3
"""Cross-platform installer for Evolving Profile 6.1+ (macOS / Windows / Linux).

Runs release preflight, secret scan, .env template creation, Python 3.11+ dependency sync
via `uv` (so Windows users with older system Python do not need WSL), and SDK + Console builds.
Build-only: never starts background services or mutates host MCP configs automatically.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path


def run(cmd: list[str], *, cwd: Path, dry_run: bool) -> None:
    print("+", " ".join(cmd), flush=True)
    if not dry_run:
        subprocess.run(cmd, cwd=str(cwd), check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", default="local", choices=["local"])
    parser.add_argument("--dry-run", action="store_true", help="Print steps without modifying files")
    parser.add_argument("--skip-deps", action="store_true", help="Skip uv/npm dependency installation")
    parser.add_argument("--no-launch", action="store_true", help="Build-only mode (skip starting services and auto-start registration)")
    parser.add_argument("--no-autostart", action="store_true", help="Opt out of OS auto-start registration (enabled by default)")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    print(f"Evolving Profile {version} cross-platform installer ({platform.system()}): {root}")

    # 1. Release metadata preflight & secret scan
    run([sys.executable, str(root / "scripts" / "release-preflight.py"), "--root", str(root), "--json"], cwd=root, dry_run=args.dry_run)
    run([sys.executable, str(root / "scripts" / "scan-release-secrets.py"), "--root", str(root)], cwd=root, dry_run=args.dry_run)

    # 2. Create .env template if missing
    env_file = root / ".env"
    env_example = root / ".env.example"
    if not env_file.exists() and env_example.exists():
        print(f"+ copy {env_example.name} -> {env_file.name}")
        if not args.dry_run:
            shutil.copy2(env_example, env_file)
            if platform.system() != "Windows":
                os.chmod(env_file, 0o600)
        print("Created .env from .env.example; fill database and provider settings before runtime use.")

    # 3. Provision Python 3.11+ and Node dependencies
    uv_bin = shutil.which("uv")
    npm_bin = shutil.which("npm")
    if not args.skip_deps:
        if uv_bin and (root / "api" / "pyproject.toml").is_file():
            run([uv_bin, "sync", "--python", "3.11", "--project", str(root / "api")], cwd=root, dry_run=args.dry_run)
        else:
            print("Warning: uv not found; install uv (https://docs.astral.sh/uv/) to auto-provision Python 3.11+.")
        if npm_bin:
            run([npm_bin, "ci"], cwd=root, dry_run=args.dry_run)
        else:
            print("Error: npm not found; Node.js 20.9+ is required to build the Console.", file=sys.stderr)
            return 1

    # 4. Build SDK before Console
    if npm_bin:
        run([npm_bin, "run", "build", "--workspace=@evolving-profile/client"], cwd=root, dry_run=args.dry_run)
        run([npm_bin, "run", "build", "--workspace=@evolving-profile/console"], cwd=root, dry_run=args.dry_run)

    # 5. Register OS auto-start by default (macOS LaunchAgents / Windows Task Scheduler ONLOGON / Linux systemd --user)
    autostart_enabled = False
    if not args.no_launch and not args.no_autostart:
        data_root = Path(os.environ.get("EP_DATA_ROOT", str(Path.home() / ".evolving-profile"))).expanduser()
        run(
            [sys.executable, "-m", "v6.autostart", "install", "--code-root", str(root), "--data-root", str(data_root)]
            + (["--dry-run"] if args.dry_run else []),
            cwd=root,
            dry_run=args.dry_run,
        )
        autostart_enabled = True
    else:
        print("Auto-start registration skipped (--no-launch or --no-autostart). Run `python -m v6.autostart install` to enable boot auto-start.")

    # 6. Write local launch manifest
    manifest = {
        "product": "Evolving Profile",
        "version": version,
        "platform": platform.system(),
        "mode": args.mode,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "autostart_enabled": autostart_enabled,
        "launch_deferred": bool(args.no_launch),
    }
    manifest_path = root / ".ep-install-manifest.json"
    print(f"+ write {manifest_path.name}")
    if not args.dry_run:
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"EP {version} build complete. Next: configure .env, run `python -m v6.pg_autotune --apply`, and start API/Console.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
