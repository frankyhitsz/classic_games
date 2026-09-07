"""Print a multi-platform hash lock from the reviewed release version pins.

Usage: python -m tests.lock_dependencies
Hashes come from each exact release's HTTPS PyPI JSON metadata. Review the
result before replacing requirements-release.lock; CI installs only wheels.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import urlopen


def locked_release(pin: str) -> str:
    name, version = pin.split("==", 1)
    with urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=30) as response:
        metadata = json.load(response)
    if metadata["info"]["version"] != version:
        raise ValueError(f"release version mismatch for {pin}")
    hashes = sorted({item["digests"]["sha256"] for item in metadata["urls"]
                     if item["packagetype"] == "bdist_wheel" and not item["yanked"]})
    if not hashes or any(len(value) != 64 for value in hashes):
        raise ValueError(f"missing release wheel hashes for {pin}")
    return pin + " \\\n" + " \\\n".join(f"    --hash=sha256:{value}" for value in hashes)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    pins = [line.strip() for line in (root / "constraints-release.txt").read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")]
    with ThreadPoolExecutor(max_workers=6) as worker:
        results = list(worker.map(locked_release, pins))
    print("# Exact release wheels for macOS, Windows and Linux; Python 3.11–3.13.\n"
          "# Generated from reviewed constraints via python -m tests.lock_dependencies.\n"
          "# Install with --require-hashes --only-binary=:all:.\n")
    print("\n\n".join(results))


if __name__ == "__main__":
    main()
