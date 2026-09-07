"""Check every supported platform branch, including on a single-platform host."""

import subprocess
import sys


def main() -> None:
    for platform in ("linux", "darwin", "win32"):
        print(f"typing target: {platform}", flush=True)
        subprocess.run([sys.executable, "-m", "mypy", "--platform", platform], check=True)


if __name__ == "__main__":
    main()
