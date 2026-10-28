#!/usr/bin/env python3
"""Propose an upstream stable-release merge on the Followcat fork."""
import argparse
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def stable_version(tag):
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise ValueError("Not a stable version tag: " + tag)
    return tuple(int(part) for part in tag[1:].split("."))


def git(root, *args, **kwargs):
    return subprocess.run(["git", *args], cwd=str(root), check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, **kwargs).stdout.strip()


def merge_release(root, tag):
    """Abort our attempted merge on conflict, preserving the starting commit."""
    try:
        git(root, "merge", "--no-ff", "--no-edit", tag)
    except subprocess.CalledProcessError as error:
        conflicts = git(root, "diff", "--name-only", "--diff-filter=U")
        git(root, "merge", "--abort")
        raise RuntimeError("Upstream merge needs manual resolution:\n" + conflicts) from error


def gh(*args):
    return subprocess.check_output(["gh", *args], universal_newlines=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report only; do not create a branch or PR")
    args = parser.parse_args()
    metadata = json.loads((ROOT / "custom/release.json").read_text())
    repository = metadata["repository"]
    if repository != "followcat/herdr":
        raise SystemExit("This workflow only manages followcat/herdr")
    release = json.loads(gh("api", "repos/" + metadata["upstream_repository"] + "/releases/latest"))
    tag = release["tag_name"]
    if release.get("draft") or release.get("prerelease") or not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise SystemExit("Latest upstream release is not a stable version tag")
    if stable_version(tag) <= stable_version(metadata["upstream_tag"]):
        print("Already tracking " + metadata["upstream_tag"] + " (latest upstream release: " + tag + ")")
        return
    print("Upstream release: " + tag + "; tracked: " + metadata["upstream_tag"])
    if args.check:
        return
    branch = "sync/upstream-" + tag
    existing = json.loads(gh("pr", "list", "--repo", repository, "--head", branch,
                            "--state", "all", "--json", "number,state"))
    if existing:
        print("A synchronization PR already exists for " + tag)
        return
    if git(ROOT, "status", "--porcelain"):
        raise SystemExit("Refusing to synchronize with a dirty checkout")
    if subprocess.run(["git", "remote", "get-url", "upstream"], cwd=str(ROOT),
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        git(ROOT, "remote", "add", "upstream",
            "https://github.com/" + metadata["upstream_repository"] + ".git")
    git(ROOT, "fetch", "upstream", "tag", tag)
    git(ROOT, "switch", "-c", branch)
    merge_release(ROOT, tag)
    old_tag = metadata["upstream_tag"]
    metadata["upstream_tag"] = tag
    metadata["upstream_commit"] = git(ROOT, "rev-parse", tag + "^{commit}")
    metadata["build_id"] = (datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d") + "." +
                            os.environ.get("GITHUB_RUN_ID", "local"))
    metadata["notes"] = metadata["notes"].replace(old_tag, tag)
    (ROOT / "custom/release.json").write_text(json.dumps(metadata, indent=2) + "\n")
    git(ROOT, "add", "custom/release.json")
    git(ROOT, "commit", "-m", "build: track upstream " + tag)
    git(ROOT, "push", "origin", branch)
    body = ("Merge upstream " + tag + " into the Followcat distribution.\n\n"
            "Custom changes remain on the followcat branch. Review CI and the upstream changes "
            "before merging; merging this PR does not publish or deploy a binary.\n\n"
            "Upstream release: " + release["html_url"] + "\n")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md") as stream:
        stream.write(body)
        stream.flush()
        print(gh("pr", "create", "--repo", repository, "--base", "followcat", "--head", branch,
                 "--title", "build: sync upstream " + tag, "--body-file", stream.name))
    # Bot-created PR workflows may wait for approval. Dispatch the checks for
    # this trusted synchronization branch explicitly as a separate workflow.
    gh("workflow", "run", "followcat.yml", "--repo", repository, "--ref", branch)


if __name__ == "__main__":
    main()
