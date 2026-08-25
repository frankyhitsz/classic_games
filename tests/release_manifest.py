"""Cross-check release artifacts against the generated dependency evidence."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILD_TOOLS = {"classic-games-hub", "pip", "setuptools", "wheel"}
ASSET_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
    ".wav", ".mp3", ".ogg", ".flac", ".ttf", ".otf", ".woff", ".woff2",
}


def _normalized(name: str) -> str:
    return name.casefold().replace("_", "-")


def main() -> int:
    installed = json.loads(
        (ROOT / "release-installed-packages.json").read_text(encoding="utf-8"))
    sbom = json.loads(
        (ROOT / "release-sbom.json").read_text(encoding="utf-8"))
    components = {
        _normalized(item["name"]): str(item["version"])
        for item in sbom.get("components", [])
        if isinstance(item, dict) and item.get("name") and item.get("version")
    }
    missing = []
    mismatched = []
    for package in installed:
        name = _normalized(str(package.get("name", "")))
        version = str(package.get("version", ""))
        if name in BUILD_TOOLS:
            continue
        if name not in components:
            missing.append(name)
        elif components[name] != version:
            mismatched.append(
                f"{name}: installed={version}, sbom={components[name]}")
    if missing or mismatched:
        raise SystemExit(
            "installed manifest does not match SBOM: "
            f"missing={missing}, mismatched={mismatched}")

    notice = (ROOT / "NOTICE.md").read_text(encoding="utf-8")
    assets = sorted(
        path.relative_to(ROOT).as_posix()
        for package_root in (ROOT / "client", ROOT / "game_service")
        for path in package_root.rglob("*")
        if path.is_file() and path.suffix.casefold() in ASSET_SUFFIXES)
    unregistered = [path for path in assets if path not in notice]
    if unregistered:
        raise SystemExit(
            f"runtime assets are missing from NOTICE.md: {unregistered}")
    print(f"release-manifest: {len(installed)} installed packages; "
          f"{len(components)} SBOM components; {len(assets)} runtime assets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
