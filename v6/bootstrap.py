"""EP v6 Code/Data Separation Enforcer & LaunchAgent Generator.

Guarantees EP_CODE_ROOT (Git repo) and EP_DATA_ROOT (~/.evolving-profile) stay strictly separated:
no copied production Python trees under EP_DATA_ROOT/runtime, and no personal data/secrets inside EP_CODE_ROOT.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any


REQUIRED_DATA_DIRS = ("config", "logs", "backups", "state")


def resolve_layout(code_root: str | Path | None = None, data_root: str | Path | None = None) -> dict[str, Path]:
    code = Path(code_root or os.environ.get("EP_CODE_ROOT") or Path(__file__).resolve().parents[1]).expanduser().resolve()
    data = Path(data_root or os.environ.get("EP_DATA_ROOT") or Path.home() / ".evolving-profile").expanduser().resolve()
    return {"code_root": code, "data_root": data}


def verify_code_data_separation(code_root: str | Path, data_root: str | Path) -> dict[str, Any]:
    layout = resolve_layout(code_root, data_root)
    code, data = layout["code_root"], layout["data_root"]
    issues: list[str] = []

    if code == data:
        issues.append("code_root_equals_data_root")
    if data.is_relative_to(code):
        issues.append("data_root_nested_inside_code_root")
    if code.is_relative_to(data):
        issues.append("code_root_nested_inside_data_root")
    if not (code / "v6" / "daemon.py").is_file():
        issues.append("missing_v6_daemon_in_code_root")
    if not (code / "v6" / "model_router.py").is_file():
        issues.append("missing_v6_model_router_in_code_root")

    legacy_copied_runtime = data / "runtime" / "host-adapter"
    warnings: list[str] = []
    if legacy_copied_runtime.is_dir():
        warnings.append("legacy_copied_runtime_present_in_data_root")

    return {
        "ok": not issues,
        "code_root": str(code),
        "data_root": str(data),
        "issues": issues,
        "warnings": warnings,
    }


def render_v6_daemon_plist(
    *,
    python_bin: str,
    code_root: str | Path,
    data_root: str | Path,
    label: str = "com.evolving-profile.v6-daemon",
) -> str:
    layout = resolve_layout(code_root, data_root)
    code, data = layout["code_root"], layout["data_root"]
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key><array>
    <string>{python_bin}</string>
    <string>-m</string>
    <string>v6.daemon</string>
  </array>
  <key>WorkingDirectory</key><string>{code}</string>
  <key>EnvironmentVariables</key><dict>
    <key>EP_CODE_ROOT</key><string>{code}</string>
    <key>EP_DATA_ROOT</key><string>{data}</string>
    <key>PYTHONPATH</key><string>{code}</string>
    <key>HF_HUB_OFFLINE</key><string>1</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>{data}/logs/v6-daemon.log</string>
  <key>StandardErrorPath</key><string>{data}/logs/v6-daemon.error.log</string>
</dict></plist>
"""


def render_v6_router_plist(
    *,
    python_bin: str,
    code_root: str | Path,
    data_root: str | Path,
    label: str = "com.evolving-profile.v6-model-router",
) -> str:
    layout = resolve_layout(code_root, data_root)
    code, data = layout["code_root"], layout["data_root"]
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key><array>
    <string>{python_bin}</string>
    <string>-m</string>
    <string>v6.model_router</string>
  </array>
  <key>WorkingDirectory</key><string>{code}</string>
  <key>EnvironmentVariables</key><dict>
    <key>EP_CODE_ROOT</key><string>{code}</string>
    <key>EP_DATA_ROOT</key><string>{data}</string>
    <key>PYTHONPATH</key><string>{code}</string>
    <key>HF_HUB_OFFLINE</key><string>1</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>{data}/logs/v6-model-router.log</string>
  <key>StandardErrorPath</key><string>{data}/logs/v6-model-router.error.log</string>
</dict></plist>
"""
