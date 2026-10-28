import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("custom_distribution", Path(__file__).with_name("custom.py"))
custom = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(custom)
SYNC_SPEC = importlib.util.spec_from_file_location("custom_sync", Path(__file__).with_name("sync_custom_upstream.py"))
sync = importlib.util.module_from_spec(SYNC_SPEC)
SYNC_SPEC.loader.exec_module(sync)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.binary = self.root / "candidate"
        self.binary.write_text("#!/bin/sh\necho 'herdr 0.9.5-followcat.test'\n")
        self.binary.chmod(0o755)
        self.bin = self.root / "bin"
        self.state = self.root / "state"
        self.bin.mkdir()
        self.old = b"previous binary"
        (self.bin / "herdr").write_bytes(self.old)
        self.manifest = {"base_version": "0.9.5", "build_id": "test",
                         "assets": {custom.platform_key(): {"sha256": custom.digest(self.binary)}}}

    def test_tampered_download_cannot_replace_installed_binary(self):
        self.binary.write_bytes(b"corrupted")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            custom.install_binary(self.binary, self.manifest, self.bin, self.state)
        self.assertEqual((self.bin / "herdr").read_bytes(), self.old)
        self.assertFalse(self.state.exists())

    def test_wrong_build_identity_cannot_replace_installed_binary(self):
        self.manifest["build_id"] = "different"
        with self.assertRaisesRegex(ValueError, "identity"):
            custom.install_binary(self.binary, self.manifest, self.bin, self.state)
        self.assertEqual((self.bin / "herdr").read_bytes(), self.old)

    def test_install_and_rollback_preserve_previous_binary(self):
        custom.install_binary(self.binary, self.manifest, self.bin, self.state)
        self.assertEqual(custom.digest(self.bin / "herdr"), custom.digest(self.binary))
        args = type("Args", (), {"state_dir": self.state, "install_dir": self.bin})()
        custom.rollback(args)
        self.assertEqual((self.bin / "herdr").read_bytes(), self.old)
        self.assertFalse((self.state / "installed.json").exists())

    def test_rollback_preserves_binary_changed_by_another_installer(self):
        custom.install_binary(self.binary, self.manifest, self.bin, self.state)
        (self.bin / "herdr").write_bytes(b"another installation")
        args = type("Args", (), {"state_dir": self.state, "install_dir": self.bin})()
        with self.assertRaisesRegex(ValueError, "changed outside"):
            custom.rollback(args)
        self.assertEqual((self.bin / "herdr").read_bytes(), b"another installation")

    def test_no_server_control_commands_are_used_during_installation(self):
        result = subprocess.CompletedProcess([], 0, "herdr 0.9.5-followcat.test\n", "")
        with patch.object(custom.subprocess, "run", return_value=result) as run:
            custom.install_binary(self.binary, self.manifest, self.bin, self.state)
        self.assertEqual(run.call_args[0][0], [str(self.binary), "--version"])
        self.assertEqual(run.call_count, 1)


class UpstreamSyncTests(unittest.TestCase):
    def test_stable_versions_are_numeric_and_reject_previews(self):
        self.assertGreater(sync.stable_version("v0.10.0"), sync.stable_version("v0.9.5"))
        with self.assertRaises(ValueError):
            sync.stable_version("v0.10.0-preview.1")

    def test_conflicted_merge_restores_the_starting_commit_and_worktree(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sync.git(root, "init", "-b", "master")
            sync.git(root, "config", "user.email", "test@example.invalid")
            sync.git(root, "config", "user.name", "Test")
            (root / "file").write_text("base\n")
            sync.git(root, "add", "file")
            sync.git(root, "commit", "-m", "base")
            sync.git(root, "switch", "-c", "followcat")
            (root / "file").write_text("custom\n")
            sync.git(root, "commit", "-am", "custom")
            starting = sync.git(root, "rev-parse", "HEAD")
            sync.git(root, "switch", "master")
            (root / "file").write_text("upstream\n")
            sync.git(root, "commit", "-am", "upstream")
            sync.git(root, "tag", "v1.0.0")
            sync.git(root, "switch", "followcat")
            with self.assertRaisesRegex(RuntimeError, "manual resolution"):
                sync.merge_release(root, "v1.0.0")
            self.assertEqual(sync.git(root, "rev-parse", "HEAD"), starting)
            self.assertEqual(sync.git(root, "status", "--porcelain"), "")
            self.assertEqual((root / "file").read_text(), "custom\n")


if __name__ == "__main__":
    unittest.main()
