"""Cross-platform runner for regression checks that need the local API."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from server.app import create_app
from werkzeug.serving import make_server


ROOT = Path(__file__).resolve().parent.parent
SERVER_START_TIMEOUT_SECONDS = 20.0


def workflow_error(title: str, detail: str) -> None:
    """Expose a useful summary even when job logs require authentication."""
    detail = detail[:3000]
    escaped = (detail.replace("%", "%25").replace("\r", "%0D")
               .replace("\n", "%0A"))
    print(f"::error title={title}::{escaped}")


def wait_for_server(url: str, server_thread: threading.Thread,
                    timeout: float = SERVER_START_TIMEOUT_SECONDS) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not server_thread.is_alive():
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
    with tempfile.TemporaryDirectory(prefix="classic-games-ci-") as directory:
        runtime = Path(directory)
        server_env = os.environ.copy()
        server_env.update({
            "GAMES_DB": str(runtime / "server.db"),
            "SDL_VIDEODRIVER": "dummy",
            "SDL_AUDIODRIVER": "dummy",
        })
        app = create_app({"DB_PATH": str(runtime / "server.db")})
        server = make_server("127.0.0.1", 0, app, threaded=True)
        server_thread = threading.Thread(
            target=server.serve_forever, name="classic-games-ci-http",
            daemon=True)
        server_thread.start()
        try:
            base_url = f"http://127.0.0.1:{server.server_port}"
            if not wait_for_server(
                    f"{base_url}/api/health", server_thread):
                workflow_error(
                    "HTTP test server startup",
                    "in-process test server did not become healthy")
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
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
