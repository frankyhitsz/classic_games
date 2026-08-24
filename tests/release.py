"""One machine-readable entry point for the existing test layers."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path


def _commands(profile: str) -> list[tuple[str, list[str]]]:
    storage = [
        sys.executable, "-m", "unittest", "discover", "-s", "tests",
        "-p", "test_storage*.py"]
    commands = [("storage", storage)]
    if profile in {"full", "release"}:
        commands.extend([
            ("stress", [sys.executable, "-m", "tests.stress"]),
            ("gameplay", [sys.executable, "-m", "tests.ci_regression"]),
        ])
    if profile == "release":
        commands[:0] = [
            ("ruff", [sys.executable, "-m", "ruff", "check",
                      "client", "game_service", "server", "tests"]),
            ("dependency-audit", [sys.executable, "-m", "pip_audit",
                                  "--cache-dir", str(Path(tempfile.gettempdir())
                                                     / "classic-games-pip-audit"),
                                  "-r", "constraints-release.txt"]),
            ("compile", [sys.executable, "-m", "compileall", "-q",
                         "client", "game_service", "server", "tests"]),
        ]
    return commands


def _write_junit(path: Path, results: list[dict]) -> None:
    suite = ET.Element(
        "testsuite", name="classic-games-release",
        tests=str(len(results)),
        failures=str(sum(result["returncode"] != 0 for result in results)),
        time=f"{sum(result['duration_seconds'] for result in results):.3f}")
    for result in results:
        case = ET.SubElement(
            suite, "testcase", name=result["name"],
            time=f"{result['duration_seconds']:.3f}")
        if result["returncode"]:
            failure = ET.SubElement(
                case, "failure", message=f"exit {result['returncode']}")
            failure.text = result["output"][-12000:]
        output = ET.SubElement(case, "system-out")
        output.text = result["output"][-12000:]
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Classic Games test profiles")
    parser.add_argument("profile", choices=("fast", "full", "release"),
                        nargs="?", default="full")
    parser.add_argument("--junit", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    results = []
    with tempfile.TemporaryDirectory(prefix="classic-games-release-") as directory:
        environment = os.environ.copy()
        environment.update({
            "SDL_VIDEODRIVER": "dummy", "SDL_AUDIODRIVER": "dummy",
            "GAMES_DB": str(Path(directory) / "games.db"),
            "PYTHONUTF8": "1",
        })
        for name, command in _commands(args.profile):
            started = time.perf_counter()
            completed = subprocess.run(
                command, cwd=Path(__file__).resolve().parents[1],
                env=environment, text=True, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, check=False)
            duration = time.perf_counter() - started
            result = {"name": name, "command": command,
                      "returncode": completed.returncode,
                      "duration_seconds": round(duration, 3),
                      "output": completed.stdout}
            results.append(result)
            print(completed.stdout, end="")
            print(f"[{name}] exit={completed.returncode} time={duration:.2f}s")
            if completed.returncode:
                break
    summary = {"ok": all(result["returncode"] == 0 for result in results),
               "profile": args.profile, "results": results}
    if args.junit:
        _write_junit(args.junit, results)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
