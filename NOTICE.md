# Distribution notice inventory

This file records what a distributor must review before publishing a build. It is not a software license and
does not grant rights to the repository.

## Bundled project material

- Game graphics, launcher cards, icons, tracks, boards, and UI decoration are drawn by the Python source at
  runtime. The repository currently contains no bundled image, audio, music, or font files.
- Chinese text uses fonts available on the host operating system through pygame's system-font lookup. Those
  font files are not copied into the wheel or repository.
- The five game names and any store listing, screenshots, or application icon still require the distributor's
  trademark and publication review described in `docs/release-governance.md`.

## Python dependencies

Runtime and optional development dependencies retain their own licenses and notices. A release build should
archive `release-sbom.json` and review it together with the installed distributions' metadata. Pinning a
dependency version in `constraints-release.txt` does not replace that review.

## Before public distribution

The repository owner must verify code contribution rights and choose a compatible `LICENSE`. If later changes
add an image, sound, music track, font, level pack, or generated asset, record its path, author, source,
license, modifications, and attribution requirement here before including it in a release.
