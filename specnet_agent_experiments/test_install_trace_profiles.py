#!/usr/bin/env python3
"""Tests for installing the repository's bundled trace profiles."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.install_trace_profiles import install_profiles


class InstallTraceProfilesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.archive = self.root / "profiles.zip"
        self.data_root = self.root / "external_agent_data"
        self.relative_path = "processed/trace_driven_v3_candidate/profile.json"
        self.content = b'{"profile_id":"trace_driven_v3_candidate"}'
        self.expected = {
            self.relative_path: hashlib.sha256(self.content).hexdigest()
        }
        with zipfile.ZipFile(self.archive, "w") as bundle:
            bundle.writestr(
                f"external_agent_data/{self.relative_path}",
                self.content,
            )

    def test_install_and_idempotent_verify(self) -> None:
        first = install_profiles(
            self.archive,
            self.data_root,
            expected_sha256=self.expected,
        )
        destination = self.data_root / self.relative_path
        self.assertEqual(destination.read_bytes(), self.content)
        self.assertEqual(first, [(self.relative_path, "installed and verified")])

        second = install_profiles(
            self.archive,
            self.data_root,
            expected_sha256=self.expected,
        )
        self.assertEqual(second, [(self.relative_path, "already verified")])

    def test_existing_mismatch_requires_force(self) -> None:
        destination = self.data_root / self.relative_path
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"stale")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            install_profiles(
                self.archive,
                self.data_root,
                expected_sha256=self.expected,
            )

        install_profiles(
            self.archive,
            self.data_root,
            force=True,
            expected_sha256=self.expected,
        )
        self.assertEqual(destination.read_bytes(), self.content)
