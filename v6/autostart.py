"""Cross-platform OS Auto-Start Manager for Evolving Profile 6.1+ (macOS / Windows / Linux).

Ensures EP services survive reboots automatically:
- macOS:   ~/Library/LaunchAgents/com.evolving-profile.<service>.plist (RunAtLoad + KeepAlive)
- Windows: Task Scheduler ONLOGON tasks (silent hidden execution, no console window popup)
- Linux:   ~/.config/systemd/user/evolving-profile-<service>.service (systemd --user enable)

Usage:
  python -m v6.autostart status
  python -m v6.autostart install [--dry-run] [--code-root PATH] [--data-root PATH]
  python -m v6.autostart uninstall [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any


def _resolve_python(data_root: Path) -> str:
    candidates = [
        data_root / "runtime" / "python-3.11" / "bin" / "python",
        data_root / "runtime" / "python-3.11" / "python.exe",
        data_root / ".venv-test" / "bin" / "python",
        data_root / ".venv-test" / "Scripts" / "python.exe",
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    return sys.executable


def build_service_specs(code_root: Path, data_root: Path) -> list[dict[str, Any]]:
    """Define the core cross-platform EP v6 services to auto-start on login/boot."""
    py = _resolve_python(data_root)
    logs_dir = data_root / "logs"
    return [
        {
            "id": "v6-model-router",
            "label": "com.evolving-profile.v6-model-router",
            "program": [py, "-m", "v6.model_router", "--data-root", str(data_root), "--port", "12095"],
            "cwd": str(code_root),
            "env": {"PYTHONPATH": str(code_root), "EP_DATA_ROOT": str(data_root), "EP_CODE_ROOT": str(code_root), "PYTHONUTF8": "1"},
            "log": str(logs_dir / "v6-model-router.log"),
        },
        {
            "id": "v6-daemon",
            "label": "com.evolving-profile.v6-daemon",
            "program": [py, "-m", "v6.daemon", "--data-root", str(data_root), "--port", "12096"],
            "cwd": str(code_root),
            "env": {"PYTHONPATH": str(code_root), "EP_DATA_ROOT": str(data_root), "EP_CODE_ROOT": str(code_root), "PYTHONUTF8": "1"},
            "log": str(logs_dir / "v6-daemon.log"),
        },
    ]


def render_macos_plist(spec: dict[str, Any]) -> str:
    args_xml = "\n".join(f"    <string>{a}</string>" for a in spec["program"])
    env_xml = "\n".join(f"    <key>{k}</key><string>{v}</string>" for k, v in spec["env"].items())
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n<dict>\n'
        f'  <key>Label</key><string>{spec["label"]}</string>\n'
        f'  <key>ProgramArguments</key>\n  <array>\n{args_xml}\n  </array>\n'
        f'  <key>WorkingDirectory</key><string>{spec["cwd"]}</string>\n'
        f'  <key>EnvironmentVariables</key>\n  <dict>\n{env_xml}\n  </dict>\n'
        '  <key>RunAtLoad</key><true/>\n'
        '  <key>KeepAlive</key><true/>\n'
        '  <key>ThrottleInterval</key><integer>10</integer>\n'
        f'  <key>StandardOutPath</key><string>{spec["log"]}</string>\n'
        f'  <key>StandardErrorPath</key><string>{spec["log"]}</string>\n'
        '</dict>\n</plist>\n'
    )


def render_systemd_unit(spec: dict[str, Any]) -> str:
    exec_start = " ".join(f'"{a}"' if " " in a else a for a in spec["program"])
    env_lines = "\n".join(f'Environment="{k}={v}"' for k, v in spec["env"].items())
    return (
        "[Unit]\n"
        f"Description=Evolving Profile ({spec['id']})\n"
        "After=network.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"WorkingDirectory={spec['cwd']}\n"
        f"{env_lines}\n"
        f"ExecStart={exec_start}\n"
        "Restart=always\n"
        "RestartSec=5\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def render_windows_launcher_vbs(spec: dict[str, Any]) -> str:
    """Silent VBScript wrapper so Windows ONLOGON tasks run without popping up a console window."""
    cmd = " ".join(f'""{a}""' for a in spec["program"])
    env_set = "\n".join(f'WshEnv("{k}") = "{v}"' for k, v in spec["env"].items())
    return (
        'Set WshShell = CreateObject("WScript.Shell")\n'
        'Set WshEnv = WshShell.Environment("PROCESS")\n'
        f"{env_set}\n"
        f'WshShell.CurrentDirectory = "{spec["cwd"]}"\n'
        f'WshShell.Run "{cmd}", 0, False\n'
    )


def install_autostart(
    code_root: str | Path,
    data_root: str | Path,
    *,
    system: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    os_name = (system or platform.system()).lower()
    c_root = Path(code_root).expanduser().resolve()
    d_root = Path(data_root).expanduser().resolve()
    specs = build_service_specs(c_root, d_root)
    installed: list[dict[str, Any]] = []

    if not dry_run:
        (d_root / "logs").mkdir(parents=True, exist_ok=True)

    if os_name == "darwin":
        agents_dir = Path.home() / "Library" / "LaunchAgents"
        uid = os.getuid() if hasattr(os, "getuid") else 501
        for spec in specs:
            plist_path = agents_dir / f"{spec['label']}.plist"
            content = render_macos_plist(spec)
            entry = {"service": spec["id"], "platform": "darwin", "path": str(plist_path), "dry_run": dry_run}
            if not dry_run:
                agents_dir.mkdir(parents=True, exist_ok=True)
                plist_path.write_text(content, encoding="utf-8")
                subprocess.run(["launchctl", "bootout", f"gui/{uid}/{spec['label']}"], capture_output=True, check=False)
                subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)], capture_output=True, check=False)
            installed.append(entry)
    elif os_name == "windows":
        startup_dir = d_root / "autostart-win"
        for spec in specs:
            vbs_path = startup_dir / f"{spec['id']}.vbs"
            task_name = f"EvolvingProfile_{spec['id'].replace('-', '_')}"
            entry = {"service": spec["id"], "platform": "windows", "task": task_name, "launcher": str(vbs_path), "dry_run": dry_run}
            if not dry_run:
                startup_dir.mkdir(parents=True, exist_ok=True)
                vbs_path.write_text(render_windows_launcher_vbs(spec), encoding="utf-8")
                subprocess.run(
                    ["schtasks", "/Create", "/F", "/SC", "ONLOGON", "/RL", "LIMITED", "/TN", task_name, "/TR", f'wscript.exe "{vbs_path}"'],
                    capture_output=True,
                    check=False,
                )
            installed.append(entry)
    else:
        unit_dir = Path.home() / ".config" / "systemd" / "user"
        for spec in specs:
            unit_name = f"evolving-profile-{spec['id']}.service"
            unit_path = unit_dir / unit_name
            entry = {"service": spec["id"], "platform": "linux", "path": str(unit_path), "dry_run": dry_run}
            if not dry_run:
                unit_dir.mkdir(parents=True, exist_ok=True)
                unit_path.write_text(render_systemd_unit(spec), encoding="utf-8")
                subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, check=False)
                subprocess.run(["systemctl", "--user", "enable", "--now", unit_name], capture_output=True, check=False)
            installed.append(entry)

    return {"status": "ok", "platform": os_name, "autostart_enabled": True, "services": installed}


def uninstall_autostart(data_root: str | Path, *, system: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    os_name = (system or platform.system()).lower()
    d_root = Path(data_root).expanduser().resolve()
    specs = build_service_specs(Path(__file__).resolve().parents[1], d_root)
    removed: list[str] = []

    if os_name == "darwin":
        agents_dir = Path.home() / "Library" / "LaunchAgents"
        uid = os.getuid() if hasattr(os, "getuid") else 501
        for spec in specs:
            plist_path = agents_dir / f"{spec['label']}.plist"
            if not dry_run:
                subprocess.run(["launchctl", "bootout", f"gui/{uid}/{spec['label']}"], capture_output=True, check=False)
                if plist_path.exists():
                    plist_path.unlink()
            removed.append(spec["label"])
    elif os_name == "windows":
        for spec in specs:
            task_name = f"EvolvingProfile_{spec['id'].replace('-', '_')}"
            if not dry_run:
                subprocess.run(["schtasks", "/Delete", "/F", "/TN", task_name], capture_output=True, check=False)
            removed.append(task_name)
    else:
        unit_dir = Path.home() / ".config" / "systemd" / "user"
        for spec in specs:
            unit_name = f"evolving-profile-{spec['id']}.service"
            unit_path = unit_dir / unit_name
            if not dry_run:
                subprocess.run(["systemctl", "--user", "disable", "--now", unit_name], capture_output=True, check=False)
                if unit_path.exists():
                    unit_path.unlink()
            removed.append(unit_name)

    return {"status": "ok", "platform": os_name, "removed": removed, "dry_run": dry_run}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["install", "uninstall", "status"])
    parser.add_argument("--code-root", default=os.environ.get("EP_CODE_ROOT", str(Path(__file__).resolve().parents[1])))
    parser.add_argument("--data-root", default=os.environ.get("EP_DATA_ROOT", str(Path.home() / ".evolving-profile")))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.action == "install":
        res = install_autostart(args.code_root, args.data_root, dry_run=args.dry_run)
    elif args.action == "uninstall":
        res = uninstall_autostart(args.data_root, dry_run=args.dry_run)
    else:
        res = install_autostart(args.code_root, args.data_root, dry_run=True)
        res["mode"] = "status_preview"
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
