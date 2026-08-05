#!/usr/bin/env python3
"""Validate exact direct requirements and hashes without installing packages."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


REQUIREMENT_RE = re.compile(
    r"^([A-Za-z0-9_.-]+)(?:\[[A-Za-z0-9_.,-]+\])?==([^\s;\\]+)"
)


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_direct(path: Path) -> dict[str, str]:
    requirements: dict[str, str] = {}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        match = REQUIREMENT_RE.match(line)
        if not match:
            raise ValueError(f"{path}:{line_number}: requirement must use exact == pin")
        requirements[canonical(match.group(1))] = match.group(2)
    if not requirements:
        raise ValueError(f"{path}: no direct requirements found")
    return requirements


def parse_lock(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    requirements: dict[str, str] = {}
    current_name = ""
    current_line = 0
    current_hashed = False

    def finish() -> None:
        if current_name and not current_hashed:
            raise ValueError(f"{path}:{current_line}: locked package has no SHA-256 hash")

    for line_number, raw in enumerate(lines, 1):
        if raw and not raw[0].isspace() and not raw.startswith(("#", "--")):
            finish()
            match = REQUIREMENT_RE.match(raw)
            if not match:
                raise ValueError(f"{path}:{line_number}: lock entry is not exact")
            current_name = canonical(match.group(1))
            current_line = line_number
            current_hashed = "--hash=sha256:" in raw
            requirements[current_name] = match.group(2)
        elif current_name and "--hash=sha256:" in raw:
            current_hashed = True
    finish()
    if not requirements:
        raise ValueError(f"{path}: no locked requirements found")
    return requirements


def check_component(component: Path) -> list[str]:
    direct_path = component / "requirements.txt"
    findings: list[str] = []
    lock_paths = [component / "requirements.lock"]
    windows_lock = component / "requirements.windows.lock"
    if windows_lock.exists():
        lock_paths.append(windows_lock)
    if not direct_path.is_file() or not lock_paths[0].is_file():
        return [f"{component}: requirements.txt and requirements.lock are required"]
    try:
        direct = parse_direct(direct_path)
    except (OSError, UnicodeError, ValueError) as exc:
        return [str(exc)]
    for lock_path in lock_paths:
        try:
            locked = parse_lock(lock_path)
        except (OSError, UnicodeError, ValueError) as exc:
            findings.append(str(exc))
            continue
        for name, version in direct.items():
            if locked.get(name) != version:
                findings.append(
                    f"{component}: {name}=={version} is not represented exactly in {lock_path.name}"
                )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("components", nargs="+")
    args = parser.parse_args()
    findings = []
    for value in args.components:
        findings.extend(check_component(Path(value).resolve()))
    if findings:
        print("PYTHON LOCK CHECK FAILED", file=sys.stderr)
        for finding in findings:
            print(f" - {finding}", file=sys.stderr)
        return 1
    print(f"Python lock check passed for {len(args.components)} component(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
