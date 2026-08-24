"""Cross-platform runner for regression checks that need the local API."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def workflow_error(title: str, detail: str) -> None:
    """Expose a useful summary even when job logs require authentication."""
    detail = detail[:3000]
    escaped = (detail.replace("%", "%25").replace("\r", "%0D")
               .replace("\n", "%0A"))
    print(f"::error title={title}::{escaped}")


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def wait_for_server(url: str, process: subprocess.Popen) -> bool:
    for _ in range(50):
        if process.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url, timeout=0.3) as response:
                payload = json.load(response)
            if payload.get("ok") and payload.get("service") == "classic-games":
                return True
        except (OSError, ValueError):
            time.sleep(0.1)
    return False


def main() -> int:
    port = free_port()
    with tempfile.TemporaryDirectory(prefix="classic-games-ci-") as directory:
        runtime = Path(directory)
        server_env = os.environ.copy()
        server_env.update({
            "GAMES_DB": str(runtime / "server.db"),
            "GAMES_PORT": str(port),
            "SDL_VIDEODRIVER": "dummy",
            "SDL_AUDIODRIVER": "dummy",
        })
        log_path = runtime / "server.log"
        with log_path.open("w", encoding="utf-8") as log:
            server = subprocess.Popen(
                [sys.executable, "-m", "server.app"], cwd=ROOT,
                env=server_env, stdout=log, stderr=subprocess.STDOUT,
                text=True)
            try:
                base_url = f"http://127.0.0.1:{port}"
                if not wait_for_server(f"{base_url}/api/health", server):
                    print(log_path.read_text(encoding="utf-8"), file=sys.stderr)
                    return 1
                test_env = server_env.copy()
                test_env.update({
                    "GAMES_DB": str(runtime / "direct.db"),
                    "GAMES_API_URL": base_url,
                })
                result = subprocess.run(
                    [sys.executable, "-m", "tests.regression"],
                    cwd=ROOT, env=test_env, capture_output=True, text=True,
                    check=False)
                output = result.stdout
                if result.stderr:
                    output += "\n[stderr]\n" + result.stderr
                (ROOT / "ci-regression.log").write_text(
                    output, encoding="utf-8")
                print(output, end="" if output.endswith("\n") else "\n")
                if result.returncode:
                    lines = output.splitlines()
                    failures = [line.strip() for line in lines
                                if "FAIL:" in line]
                    detail = "\n".join(
                        failures[:8] + ["", *lines[-12:]])
                    workflow_error("Gameplay regression failures", detail)
                    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
                    if summary_path:
                        with Path(summary_path).open(
                                "a", encoding="utf-8") as summary:
                            summary.write(
                                "## Gameplay regression failures\n\n```text\n"
                                f"{detail[:6000]}\n```\n")
                return result.returncode
            finally:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
