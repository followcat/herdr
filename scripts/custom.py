#!/usr/bin/env python3
"""Build, package, install and roll back the Followcat Herdr distribution.

The installer commands require only Python 3.6+ and its standard library.
They never stop a server or change Herdr's configuration/session directories.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

REPOSITORY = "followcat/herdr"
ROOT = Path(__file__).resolve().parents[1]
MAX_DOWNLOAD = 256 * 1024 * 1024


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def fetch(url, limit=MAX_DOWNLOAD):
    if not url.startswith("https://"):
        raise ValueError("Downloads require HTTPS: " + url)
    request = urllib.request.Request(url, headers={"User-Agent": "followcat-herdr-installer"})
    with urllib.request.urlopen(request, timeout=60) as response:
        content = response.read(limit + 1)
    if len(content) > limit:
        raise ValueError("Download exceeds size limit")
    return content


def platform_key():
    systems = {"Linux": "linux", "Darwin": "macos"}
    machines = {"x86_64": "x86_64", "AMD64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}
    if platform.system() not in systems or platform.machine() not in machines:
        raise ValueError("Unsupported platform: " + platform.system() + "/" + platform.machine())
    return systems[platform.system()] + "-" + machines[platform.machine()]


def atomic_copy(source, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, stage = tempfile.mkstemp(prefix=".herdr-install-", dir=str(destination.parent))
    try:
        with os.fdopen(fd, "wb") as stream, Path(source).open("rb") as original:
            shutil.copyfileobj(original, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(stage, 0o755)
        os.replace(stage, str(destination))
    finally:
        if os.path.exists(stage):
            os.unlink(stage)


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".herdr-state-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, str(path))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def install_binary(binary, manifest, install_dir, state_dir):
    key = platform_key()
    asset = manifest["assets"].get(key)
    if not asset:
        raise ValueError("This release has no binary for " + key)
    expected = asset.get("sha256", "")
    if not re.fullmatch(r"[0-9a-f]{64}", expected) or digest(binary) != expected:
        raise ValueError("SHA-256 verification failed; installed binary was not changed")
    identity = manifest["base_version"] + "-followcat." + manifest["build_id"]
    result = subprocess.run([str(binary), "--version"], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, universal_newlines=True, timeout=15, check=True)
    if result.stdout.strip() != "herdr " + identity:
        raise ValueError("Downloaded binary identity does not match the release manifest")
    target = Path(install_dir) / "herdr"
    state_dir = Path(state_dir)
    state_path = state_dir / "installed.json"
    previous = read_json(state_path) if state_path.exists() else None
    if target.exists() and digest(target) == expected:
        print("Already installed: herdr " + identity)
        return
    backup = None
    if target.exists():
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        backup = state_dir / "backups" / ("herdr-" + stamp)
        atomic_copy(target, backup)
    atomic_copy(binary, target)
    record = {"version": identity, "sha256": expected, "binary": str(target),
              "repository": REPOSITORY, "backup": str(backup) if backup else None,
              "backup_sha256": digest(backup) if backup else None, "previous": previous}
    write_json(state_path, record)
    print("Installed herdr " + identity + " at " + str(target))
    print("Existing servers and panes were left running. Use 'status' to inspect the installed version.")


def install(args):
    manager = Path(args.install_dir) / "herdr-followcat"
    marker = b'Build, package, install and roll back the Followcat Herdr distribution.'
    if manager.exists() and marker not in manager.read_bytes()[:512]:
        raise ValueError("An unrelated herdr-followcat command already exists; it was not changed")
    manifest = (read_json(args.manifest) if args.manifest else
                json.loads(fetch("https://github.com/" + REPOSITORY +
                                 "/releases/latest/download/update.json", 1024 * 1024)))
    with tempfile.TemporaryDirectory(prefix="herdr-download-") as directory:
        binary = Path(directory) / "herdr"
        manager_source = Path(__file__)
        installer_asset = manifest.get("installer")
        if not args.binary and installer_asset:
            manager_source = Path(directory) / "install.py"
            manager_source.write_bytes(fetch(installer_asset["url"], 1024 * 1024))
            if digest(manager_source) != installer_asset["sha256"]:
                raise ValueError("Installer SHA-256 verification failed; installed files were not changed")
        if args.binary:
            shutil.copyfile(args.binary, binary)
        else:
            key = platform_key()
            if key not in manifest["assets"]:
                raise ValueError("This release has no binary for " + key)
            binary.write_bytes(fetch(manifest["assets"][key]["url"]))
        binary.chmod(0o755)
        install_binary(binary, manifest, args.install_dir, args.state_dir)
        atomic_copy(manager_source, manager)
    print("Update manager: " + str(manager))


def rollback(args):
    state_path = Path(args.state_dir) / "installed.json"
    record = read_json(state_path)
    backup = Path(record["backup"]) if record.get("backup") else None
    if not backup or not backup.is_file():
        raise ValueError("No previous binary is available")
    if digest(backup) != record["backup_sha256"]:
        raise ValueError("Backup checksum mismatch; installed binary was not changed")
    target = Path(args.install_dir) / "herdr"
    if str(target) != record["binary"]:
        raise ValueError("Rollback install directory differs from the recorded installation")
    if not target.is_file() or digest(target) != record["sha256"]:
        raise ValueError("Installed binary changed outside this installer; refusing to overwrite it")
    atomic_copy(backup, target)
    previous = record.get("previous")
    if previous:
        write_json(state_path, previous)
    else:
        state_path.unlink()
    print("Restored previous binary at " + str(target) + "; servers were left running.")


def source_metadata():
    metadata = read_json(ROOT / "custom/release.json")
    if metadata["repository"] != REPOSITORY:
        raise ValueError("Repository differs from the compiled Followcat distribution")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", metadata["build_id"]):
        raise ValueError("Invalid build_id")
    cargo = (ROOT / "Cargo.toml").read_text()
    metadata["base_version"] = re.search(r'^version = "([^"]+)"', cargo, re.M).group(1)
    metadata["tag"] = "followcat-v" + metadata["base_version"] + "-" + metadata["build_id"]
    return metadata


def build(args):
    metadata = source_metadata()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(ROOT),
                                     universal_newlines=True).strip()
    env = dict(os.environ, HERDR_BUILD_CHANNEL="followcat", HERDR_BUILD_ID=metadata["build_id"],
               HERDR_BUILD_COMMIT=commit)
    cargo = shutil.which("cargo") or str(Path.home() / ".cargo/bin/cargo")
    command = [cargo, "build", "--release", "--locked"]
    if args.target:
        command.extend(["--target", args.target])
    subprocess.run(command, cwd=str(ROOT), env=env, check=True)


def package(args):
    metadata = source_metadata()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    asset_name = "herdr-" + platform_key()
    shutil.copy2(__file__, output / "install.py")
    binary = output / asset_name
    shutil.copy2(args.binary, binary)
    binary.chmod(0o755)
    expected_version = "herdr " + metadata["base_version"] + "-followcat." + metadata["build_id"]
    actual = subprocess.check_output([str(binary), "--version"], universal_newlines=True).strip()
    if actual != expected_version:
        raise ValueError("Package binary identity mismatch: " + actual)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(ROOT),
                                     universal_newlines=True).strip()
    protocol = int(re.search(r"pub const PROTOCOL_VERSION: u32 = (\d+);",
                            (ROOT / "src/protocol/wire.rs").read_text()).group(1))
    generation = int(re.search(r"pub const ENDPOINT_PROTOCOL_GENERATION: u32 = (\d+);",
                              (ROOT / "src/protocol/endpoint.rs").read_text()).group(1))
    url = "https://github.com/" + REPOSITORY + "/releases/download/" + metadata["tag"] + "/" + asset_name
    current = {"base_version": metadata["base_version"], "build_id": metadata["build_id"],
               "commit": commit, "built_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "protocol": protocol, "endpoint_generation": generation, "notes": metadata["notes"],
               "assets": {platform_key(): {"url": url, "sha256": digest(binary)}},
               "installer": {"url": "https://github.com/" + REPOSITORY + "/releases/download/" +
                             metadata["tag"] + "/install.py",
                             "sha256": digest(output / "install.py")}}
    builds = {}
    if args.previous_manifest and Path(args.previous_manifest).exists():
        old = read_json(args.previous_manifest)
        builds.update(old.get("builds", {}))
        builds[old["build_id"]] = {k: v for k, v in old.items() if k not in ("builds", "channel")}
        if metadata["build_id"] in builds:
            raise ValueError("build_id is already published; choose a new custom build_id")
    builds[metadata["build_id"]] = dict(current)
    manifest = dict(current, channel="preview", builds=builds)
    write_json(output / "update.json", manifest)
    (output / "SHA256SUMS").write_text("".join(digest(output / name) + "  " + name + "\n"
                                               for name in [asset_name, "install.py", "update.json"]))
    print(metadata["tag"])


def status(args):
    target = Path(args.install_dir) / "herdr"
    print(subprocess.check_output([str(target), "--version"], universal_newlines=True).strip())
    state_path = Path(args.state_dir) / "installed.json"
    if state_path.exists():
        print(json.dumps(read_json(state_path), indent=2))
    else:
        print("No installation record from this installer.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")
    for name in ["install", "update", "rollback", "status"]:
        command = sub.add_parser(name)
        command.add_argument("--install-dir", default=str(Path.home() / ".local/bin"))
        command.add_argument("--state-dir", default=str(Path.home() / ".local/state/herdr-custom"))
        command.set_defaults(run={"install": install, "update": install,
                                  "rollback": rollback, "status": status}[name])
        if name in ("install", "update"):
            command.add_argument("--binary", type=Path)
            command.add_argument("--manifest", type=Path)
    command = sub.add_parser("build")
    command.add_argument("--target")
    command.set_defaults(run=build)
    command = sub.add_parser("package")
    command.add_argument("--binary", required=True, type=Path)
    command.add_argument("--output", default="custom-dist")
    command.add_argument("--previous-manifest", type=Path)
    command.set_defaults(run=package)
    args = parser.parse_args()
    if not hasattr(args, "run"):
        parser.error("a command is required")
    try:
        args.run(args)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        parser.exit(1, "error: " + str(error) + "\n")


if __name__ == "__main__":
    main()
