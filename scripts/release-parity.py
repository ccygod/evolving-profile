#!/usr/bin/env python3
"""Compare this release checkout with the source an installation actually runs.

Releases are cut by selectively syncing a private, running installation into
this sanitised repository. A component that is never synced silently ships an
old version (6.2.0 shipped the pre-11-18 link map this way). This check lists,
per module, every file that exists only in the running install or whose code
differs, so nothing is left behind by accident.

The module map, personal-data patterns and accepted differences live in a
LOCAL file that is never committed (default
``~/.evolving-profile/config/release-parity.json``, override with
``EP_RELEASE_PARITY_CONFIG``)::

    {
      "pairs": {"console/src": "/path/to/running/console/src", ...},
      "personal_patterns": ["regex", ...],      # lines matching are sanitisation
      "exclude": ["ham-os/docs/", ...],          # repo-relative prefixes never shipped
      "accepted": {"host-adapter/retention_worker.py": "repo ahead: lost-ack fix"}
    }

A differing file whose live-side changed lines all match a personal pattern is
reported as ``sanitised`` (expected). Exit status: 0 when every remaining
difference is listed under ``accepted``; 1 otherwise; 2 when the config is
missing or unreadable.

    python3 scripts/release-parity.py [--json]
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE = {".py", ".ts", ".tsx", ".js", ".mjs", ".css", ".json", ".sql", ".zsh", ".sh"}
SKIP_DIRS = {"__pycache__", "node_modules", ".next", ".pytest_cache", ".ruff_cache", ".venv", "venv", ".git"}
SKIP_NAME = re.compile(r"\.(before|pre|bak|orig|previous)[-.]|\.bak$|\.pyc$|\.DS_Store$|\.lock$")


def _files(root: Path) -> dict[str, Path]:
    out = {}
    for directory, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for name in names:
            if SKIP_NAME.search(name) or Path(name).suffix not in CODE:
                continue
            path = Path(directory) / name
            out[str(path.relative_to(root))] = path
    return out


def _sanitised_only(live: Path, repo: Path, personal: re.Pattern | None) -> bool:
    if personal is None:
        return False
    a = live.read_text(encoding="utf-8", errors="replace").splitlines()
    b = repo.read_text(encoding="utf-8", errors="replace").splitlines()
    changed = [line for tag, i1, i2, _, _ in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
               if tag != "equal" for line in a[i1:i2]]
    return bool(changed) and all(personal.search(line) for line in changed)


def compare(config: dict) -> dict:
    patterns = config.get("personal_patterns") or []
    personal = re.compile("|".join(patterns)) if patterns else None
    exclude = tuple(config.get("exclude") or ())
    accepted = config.get("accepted") or {}
    report = {"modules": {}, "unaccepted": []}
    for module, live_root in (config.get("pairs") or {}).items():
        live, repo = _files(Path(live_root)), _files(ROOT / module)
        counts = {"identical": 0, "sanitised": 0, "code": [], "live_only": [], "repo_only": []}
        for rel in sorted(set(live) | set(repo)):
            key = f"{module}/{rel}"
            if key.startswith(exclude):
                continue
            if rel not in repo:
                kind = "live_only"
            elif rel not in live:
                kind = "repo_only"
            elif live[rel].read_bytes() == repo[rel].read_bytes():
                counts["identical"] += 1; continue
            elif _sanitised_only(live[rel], repo[rel], personal):
                counts["sanitised"] += 1; continue
            else:
                kind = "code"
            counts[kind].append(rel)
            if key not in accepted:
                report["unaccepted"].append({"file": key, "kind": kind})
        report["modules"][module] = counts
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    path = Path(os.environ.get("EP_RELEASE_PARITY_CONFIG", Path.home() / ".evolving-profile/config/release-parity.json"))
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"release parity: config unavailable ({type(exc).__name__}); see the module docstring", file=sys.stderr)
        return 2
    report = compare(config)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        for module, c in report["modules"].items():
            print(f"{module:28s} identical={c['identical']:4d} sanitised={c['sanitised']:3d} "
                  f"code={len(c['code']):3d} live_only={len(c['live_only']):3d} repo_only={len(c['repo_only']):3d}")
        for item in report["unaccepted"]:
            print(f"  UNACCEPTED {item['kind']:9s} {item['file']}")
        print("release parity:", "passed" if not report["unaccepted"] else f"{len(report['unaccepted'])} unaccepted difference(s)")
    return 0 if not report["unaccepted"] else 1


if __name__ == "__main__":
    sys.exit(main())
