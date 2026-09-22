#!/usr/bin/env python3
"""Build and package strmprobe releases for Jellyfin 12.0+."""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
ARTIFACTS = ROOT / "artifacts"
CSPROJ = ROOT / "JellySTRMprobe" / "JellySTRMprobe.csproj"
# Note: This script is in scripts/, but CSPROJ is in the repo root

PLUGIN_GUID = "f203eaa8-4a6f-46b5-a9c4-eebbbd68a787"
PLUGIN_NAME = "strmprobe"
PLUGIN_DESC = "Probe STRM files to extract media info (codec, resolution, duration, audio)."
PLUGIN_OVERVIEW = "Probe STRM files to extract media info (codec, resolution, duration, audio)"
PLUGIN_CATEGORY = "General"
PLUGIN_OWNER = "firestaerter3"
PLUGIN_IMAGE_URL = ""

# Jellyfin target → (targetAbi, SDK)
JELLYFIN_TARGETS = {
    "12.0": ("12.0.0.0", "net10.0"),
}


def md5(path: pathlib.Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd: list[str]) -> None:
    print(f"$ {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, check=True)


def publish_jellyfin(jf_version: str, manifest_version: str, out_dir: pathlib.Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    run([
        "dotnet", "publish", str(CSPROJ),
        "-c", "Release",
        f"-p:Version={manifest_version}",
        "-o", str(out_dir),
        "--nologo",
    ])


def build_jellyfin_zip(
    jf_version: str,
    manifest_version: str,
    changelog: str,
    timestamp: str,
    target_abi: str,
    publish_dir: pathlib.Path,
) -> pathlib.Path:
    meta = {
        "category": PLUGIN_CATEGORY,
        "guid": PLUGIN_GUID,
        "name": PLUGIN_NAME,
        "description": PLUGIN_DESC,
        "overview": PLUGIN_OVERVIEW,
        "owner": PLUGIN_OWNER,
        "targetAbi": target_abi,
        "version": manifest_version,
        "changelog": changelog,
        "timestamp": timestamp,
    }
    zip_path = ARTIFACTS / f"strmprobe.zip"
    dll = publish_dir / "JellySTRMprobe.dll"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("meta.json", json.dumps(meta, indent=2))
        z.write(dll, dll.name)
    return zip_path


def _pre_key(pre_id: str) -> tuple:
    """Encodes prerelease identifiers like 'rc.2' into a type-safe, orderable tuple.

    Numeric identifiers become (0, <int>) and nonnumeric identifiers become (1, <str>),
    so identifiers compare numerically when numeric ('rc.10' sorts after 'rc.2') and
    never raise TypeError when a manifest mixes numeric and nonnumeric identifiers.
    """
    return tuple(
        (0, int(ident)) if ident.isdigit() else (1, ident)
        for ident in pre_id.split(".")
    )


def version_sort_key(version: str) -> tuple:
    """Converts a version string like '1.10.0-beta' into a comparable, type-safe tuple.

    Numeric components compare numerically. The prerelease marker (0, <int>, 0, <key>)
    sorts below the stable marker (0, <int>, 1), so stable versions sort ahead of
    prereleases with the same prefix. Every tuple element starts with an int, so a
    manifest mixing numeric, string, stable, and prerelease parts never raises TypeError.
    """
    key: list[tuple] = []
    for part in version.split("."):
        if "-" in part:
            base, pre_id = part.split("-", 1)
            try:
                key.append((0, int(base), 0, _pre_key(pre_id)))
            except ValueError:
                key.append((1, base, 0, _pre_key(pre_id)))
        else:
            try:
                key.append((0, int(part), 1))
            except ValueError:
                key.append((1, part, 1))
    return tuple(key)


def update_manifest(entries: list[dict]) -> None:
    manifest_path = ROOT / "manifest.json"
    if manifest_path.exists():
        # A corrupt manifest must fail the release rather than silently being
        # converted to an empty list (which would lose plugin history).
        manifest = json.loads(manifest_path.read_text())
    else:
        manifest = []

    if not manifest:
        manifest = [{
            "guid": PLUGIN_GUID,
            "name": PLUGIN_NAME,
            "overview": PLUGIN_OVERVIEW,
            "description": PLUGIN_DESC,
            "owner": PLUGIN_OWNER,
            "category": PLUGIN_CATEGORY,
            "imageUrl": PLUGIN_IMAGE_URL,
            "versions": [],
        }]

    plugin = manifest[0]
    existing = {v.get("version"): v for v in plugin.get("versions", [])}
    for entry in entries:
        existing[entry["version"]] = entry
    plugin["versions"] = sorted(existing.values(), key=lambda v: version_sort_key(str(v.get("version", ""))), reverse=True)
    manifest[0] = plugin

    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--version", required=True, help="Base plugin version (e.g. 0.0.3)")
    p.add_argument("--repo", default="", help="owner/repo for GitHub release URL")
    p.add_argument("--changelog", default="", help="Release notes blurb")
    p.add_argument(
        "--jellyfin",
        default="12.0",
        help="Comma-separated Jellyfin targets to build (default: 12.0).",
    )
    args = p.parse_args()

    base_version = args.version.lstrip("v")
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if ARTIFACTS.exists():
        shutil.rmtree(ARTIFACTS)
    ARTIFACTS.mkdir()

    jf_requested = [v.strip() for v in args.jellyfin.split(",") if v.strip()]
    manifest_entries: list[dict] = []

    for jf_version in jf_requested:
        if jf_version not in JELLYFIN_TARGETS:
            print(f"!! unknown Jellyfin target: {jf_version} — skipping", file=sys.stderr)
            continue
        target_abi, _tfm = JELLYFIN_TARGETS[jf_version]
        manifest_version = base_version  # e.g. tag v1.3.0.0 -> 1.3.0.0

        publish_dir = ARTIFACTS / f"publish-jf-{jf_version}"
        publish_jellyfin(jf_version, manifest_version, publish_dir)

        zip_path = build_jellyfin_zip(
            jf_version, manifest_version, args.changelog, timestamp, target_abi, publish_dir,
        )
        checksum = md5(zip_path)
        print(f"  → {zip_path.name}  md5={checksum}  abi={target_abi}")

        if args.repo:
            manifest_entries.append({
                "version": manifest_version,
                "changelog": args.changelog,
                "targetAbi": target_abi,
                "sourceUrl": f"https://github.com/{args.repo}/releases/download/v{base_version}/{zip_path.name}",
                "checksum": checksum,
                "timestamp": timestamp,
            })

    if manifest_entries:
        update_manifest(manifest_entries)
        print(f"manifest.json: {len(manifest_entries)} entry(ies) added")
    else:
        print("manifest.json: skipped (no --repo or no Jellyfin targets)")

    return 0


if __name__ == "__main__":
    sys.exit(main())