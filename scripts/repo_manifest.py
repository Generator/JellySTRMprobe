#!/usr/bin/env python3
"""Regenerate Jellyfin plugin repository manifest from GitHub releases.

Replaces Kevinjil/jellyfin-plugin-repo-action which fails with
`TypeError: a.version.localeCompare is not a function` when any
release's build.yaml has `version: 7` (numeric) as in v1.0.0.0.
This script stringifies versions before sorting, matching the fix
in scripts/package.py version_sort_key.
"""
import json
import os
import sys
import urllib.request
import urllib.error
import base64
import yaml  # PyYAML available in runner (used by package.py deps)


def get_build_yaml(owner, repo, ref, token):
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/build.yaml?ref={ref}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "repo-manifest",
    })
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
            content = base64.b64decode(data["content"]).decode("utf-8")
            cfg = yaml.safe_load(content)
            return cfg
    except Exception as e:
        print(f"!! failed to fetch build.yaml at {ref}: {e}", file=sys.stderr)
        return {}


def main():
    repo = os.environ.get("GITHUB_REPOSITORY", "firestaerter3/jellyfin-plugin-strmprobe")
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("GITHUB_TOKEN not set", file=sys.stderr)
        sys.exit(1)
    owner, repo_name = repo.split("/", 1)

    # Fetch releases
    url = f"https://api.github.com/repos/{owner}/{repo_name}/releases?per_page=100"
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "repo-manifest",
    })
    with urllib.request.urlopen(req) as resp:
        releases = json.loads(resp.read())

    # Fetch build.yaml on master for plugin metadata
    build_cfg = get_build_yaml(owner, repo_name, "master", token)
    # Fetch current manifest from gh-pages
    manifest = []
    try:
        # Fetch existing manifest.json from gh-pages to preserve structure (if any)
        url2 = f"https://api.github.com/repos/{owner}/{repo_name}/contents/manifest.json?ref=gh-pages"
        req2 = urllib.request.Request(url2, headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "repo-manifest",
        })
        with urllib.request.urlopen(req2) as resp2:
            data2 = json.loads(resp2.read())
            content2 = base64.b64decode(data2["content"]).decode("utf-8")
            manifest = json.loads(content2)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            manifest = []
        else:
            raise
    except Exception as e:
        print(f"!! failed to fetch existing manifest: {e}", file=sys.stderr)
        manifest = []

    if not manifest:
        # Fallback to build.yaml metadata
        cfg = get_build_yaml(owner, repo_name, "master", token)
        manifest = [{
            "guid": cfg.get("guid", "f203eaa8-4a6f-46b5-a9c4-eebbbd68a787"),
            "name": cfg.get("name", "strmprobe"),
            "overview": cfg.get("overview", "Probe STRM files to extract media info."),
            "description": cfg.get("description", "Probe STRM files to extract media info.\n"),
            "owner": cfg.get("owner", "firestaerter3"),
            "category": cfg.get("category", "General"),
            "imageUrl": cfg.get("imageUrl", ""),
            "versions": [],
        }]

    plugin = manifest[0]
    # Build versions from releases
    versions = []
    for rel in releases:
        if rel.get("draft"):
            continue
        tag = rel.get("tag_name", "")
        if not tag.startswith("v"):
            continue
        # Fetch build.yaml at this tag to get targetAbi etc.
        cfg = get_build_yaml(owner, repo_name, tag, token)
        version = str(cfg.get("version", tag.lstrip("v")))
        # Ensure version is string
        changelog = rel.get("body", "")
        # Find zip asset
        sourceUrl = ""
        checksum = ""
        for asset in rel.get("assets", []):
            if asset["name"].endswith(".zip"):
                sourceUrl = asset["browser_download_url"]
            if asset["name"].endswith(".md5"):
                # Fetch md5 file
                try:
                    with urllib.request.urlopen(asset["browser_download_url"]) as r:
                        checksum = r.read().decode("utf-8").strip().split()[0]
                except Exception:
                    pass
        # Fallback to sourceUrl from release if not found
        versions.append({
            "version": str(version),
            "changelog": changelog,
            "targetAbi": str(cfg.get("targetAbi", "12.0.0.0")),
            "sourceUrl": sourceUrl,
            "checksum": checksum,
            "timestamp": rel.get("published_at", ""),
        })

    # Sort descending by version using numeric-aware key
    def sort_key(v):
        ver = str(v.get("version", ""))
        parts = []
        for part in ver.split("."):
            if "-" in part:
                base, pre = part.split("-", 1)
                try:
                    parts.append((0, int(base), 0, pre))
                except ValueError:
                    parts.append((1, base, 0, pre))
            else:
                try:
                    parts.append((0, int(part), 1))
                except ValueError:
                    parts.append((1, part, 1))
        return tuple(parts)

    versions.sort(key=sort_key, reverse=True)
    plugin["versions"] = versions

    # Ensure top-level metadata from build.yaml (master) is stringified
    for k in ["guid", "name", "overview", "description", "owner", "category", "imageUrl"]:
        if k in build_cfg:
            plugin[k] = str(build_cfg[k]) if build_cfg[k] is not None else plugin.get(k)

    output = json.dumps(manifest, indent=2) + "\n"
    pathlib = __import__("pathlib")
    out_path = pathlib.Path("manifest.json")
    out_path.write_text(output)
    print(f"Generated manifest.json with {len(versions)} versions")
    for v in versions:
        print(f"  - {v['version']} {v['targetAbi']} {v['checksum'][:8]}")

    # Commit to gh-pages via GitHub API (replaces Kevinjil action)
    try:
        # Get current SHA on gh-pages
        url3 = f"https://api.github.com/repos/{owner}/{repo_name}/contents/manifest.json?ref=gh-pages"
        req3 = urllib.request.Request(url3, headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "repo-manifest",
        })
        sha = None
        try:
            with urllib.request.urlopen(req3) as resp3:
                data3 = json.loads(resp3.read())
                sha = data3.get("sha")
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
        # Update file
        content_b64 = base64.b64encode(output.encode("utf-8")).decode("utf-8")
        payload = {
            "message": "Regenerate Jellyfin plugin repository.",
            "content": content_b64,
            "branch": "gh-pages",
            "committer": {"name": "github-actions[bot]", "email": "41898282+github-actions[bot]@users.noreply.github.com"},
            "author": {"name": "github-actions[bot]", "email": "41898282+github-actions[bot]@users.noreply.github.com"},
        }
        if sha:
            payload["sha"] = sha
        data = json.dumps(payload).encode("utf-8")
        req4 = urllib.request.Request(f"https://api.github.com/repos/{owner}/{repo_name}/contents/manifest.json", data=data, headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "repo-manifest",
            "Content-Type": "application/json",
        }, method="PUT")
        with urllib.request.urlopen(req4) as resp4:
            result = json.loads(resp4.read())
            print(f"Committed manifest.json to gh-pages: {result.get('commit', {}).get('sha', '')[:8]}")
    except Exception as e:
        print(f"!! failed to commit manifest.json to gh-pages: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
