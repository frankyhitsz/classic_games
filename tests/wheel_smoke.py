"""Build the release wheel, install it, and smoke-test packaged entry points."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import venv
from pathlib import Path


def _run(command: list[str], *, cwd: Path, environment=None) -> None:
    subprocess.run(
        command, cwd=cwd, env=environment, check=True, timeout=180)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="classic-games-wheel-") as directory:
        work = Path(directory)
        wheelhouse = work / "dist"
        wheelhouse.mkdir()
        _run([
            sys.executable, "-m", "pip", "wheel", ".", "--no-deps",
            "--no-build-isolation", "--wheel-dir", str(wheelhouse),
        ], cwd=root)
        wheels = list(wheelhouse.glob("classic_games_hub-*.whl"))
        if len(wheels) != 1:
            raise RuntimeError("release build did not produce exactly one wheel")
        environment_dir = work / "venv"
        venv.EnvBuilder(with_pip=True).create(environment_dir)
        scripts = environment_dir / ("Scripts" if os.name == "nt" else "bin")
        python = scripts / ("python.exe" if os.name == "nt" else "python")
        _run([
            str(python), "-m", "pip", "install", "--force-reinstall",
            "-c", str(root / "constraints-release.txt"), str(wheels[0]),
        ], cwd=work)
        environment = os.environ.copy()
        environment.update({
            "SDL_VIDEODRIVER": "dummy", "SDL_AUDIODRIVER": "dummy",
            "PYTHONUTF8": "1",
        })
        _run([
            str(python), "-c",
            "import client.launcher, game_service.data_cli, game_service.store",
        ], cwd=work, environment=environment)
        data_command = scripts / (
            "classic-games-data.exe" if os.name == "nt"
            else "classic-games-data")
        _run([str(data_command), "--help"], cwd=work, environment=environment)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
