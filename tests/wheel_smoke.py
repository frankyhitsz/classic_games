"""Build the release wheel, install it, and smoke-test packaged entry points."""

from __future__ import annotations

import os
import json
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
            "--wheel-dir", str(wheelhouse),
        ], cwd=root)
        wheels = list(wheelhouse.glob("classic_games_hub-*.whl"))
        if len(wheels) != 1:
            raise RuntimeError("release build did not produce exactly one wheel")
        _run([
            sys.executable, "-m", "build", "--sdist",
            "--outdir", str(wheelhouse), ".",
        ], cwd=root)
        sdists = list(wheelhouse.glob("classic_games_hub-*.tar.gz"))
        if len(sdists) != 1:
            raise RuntimeError("release build did not produce exactly one sdist")
        environment_dir = work / "venv"
        venv.EnvBuilder(with_pip=True).create(environment_dir)
        scripts = environment_dir / ("Scripts" if os.name == "nt" else "bin")
        python = scripts / ("python.exe" if os.name == "nt" else "python")
        _run([
            str(python), "-m", "pip", "install", "--force-reinstall",
            "-c", str(root / "constraints-release.txt"), str(wheels[0]),
        ], cwd=work)
        _run([
            str(python), "-m", "pip", "install", "--force-reinstall",
            "-c", str(root / "constraints-release.txt"), str(sdists[0]),
        ], cwd=work)
        environment = os.environ.copy()
        environment.update({
            "SDL_VIDEODRIVER": "dummy", "SDL_AUDIODRIVER": "dummy",
            "PYTHONUTF8": "1", "XDG_DATA_HOME": str(work / "user-data"),
            "APPDATA": str(work / "user-data"),
            "GAMES_DATA_DIR": str(work / "user-data"),
        })
        readonly_cwd = work / "readonly-cwd"
        readonly_cwd.mkdir()
        readonly_cwd.chmod(0o555)
        _run([
            str(python), "-c",
            "from pathlib import Path; "
            "import client.launcher, game_service.data_cli, game_service.store; "
            "from game_service.store import LocalGameStore, default_database_path; "
            "p=default_database_path(); LocalGameStore(p); "
            "assert p.is_file() and Path.cwd() not in p.parents",
        ], cwd=readonly_cwd, environment=environment)
        data_command = scripts / (
            "classic-games-data.exe" if os.name == "nt"
            else "classic-games-data")
        _run([str(data_command), "--help"], cwd=work, environment=environment)
        completed = subprocess.run(
            [str(python), "-m", "pip", "list", "--format", "json"],
            cwd=work, env=environment, check=True, timeout=180,
            text=True, stdout=subprocess.PIPE)
        installed = json.loads(completed.stdout)
        (root / "release-installed-packages.json").write_text(
            json.dumps(installed, ensure_ascii=False, indent=2),
            encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
