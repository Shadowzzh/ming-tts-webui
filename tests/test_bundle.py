"""Exercise the release entry point from an isolated application checkout."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


class BundlePortabilityTest(unittest.TestCase):
    def test_release_is_self_contained_without_ops_or_git(self):
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="ming-release-test-") as directory:
            root = Path(directory)
            checkout = root / "checkout"
            shutil.copytree(
                source,
                checkout,
                ignore=shutil.ignore_patterns(
                    ".git", "node_modules", "dist", "__pycache__", ".pytest_cache",
                    ".venv", "uploads", "outputs", "state", "models", "*.tsbuildinfo",
                ),
            )
            # Prebuilt assets isolate packaging from npm/network availability.
            assets = checkout / "frontend" / "dist"
            assets.mkdir(parents=True)
            (assets / "index.html").write_text("<!doctype html><title>release fixture</title>")
            output = root / "release.tar.gz"
            result = subprocess.run(
                [sys.executable, str(checkout / "bundle.py"), "--skip-build", "--output", str(output)],
                cwd=root, text=True, capture_output=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            with tarfile.open(output) as archive:
                def read_member(name: str) -> bytes:
                    member = archive.extractfile(name)
                    assert member is not None, name
                    return member.read()

                names = set(archive.getnames())
                required = {
                    "Dockerfile", "compose.yaml", ".env.example", "requirements-model.txt",
                    "requirements-web.txt", "README.md", "docs/ray.md", "LICENSE",
                    "app/server.py", "app/tts_wrapper.py", "static/index.html", "manifest.json",
                }
                self.assertTrue(required <= names, required - names)
                self.assertFalse(any(name.startswith(("hosts/", ".git/", "uploads/", "state/")) for name in names))
                manifest = json.loads(read_member("manifest.json"))
                entries = manifest["sha256"]
                self.assertEqual(set(entries), names - {"manifest.json"})
                for name, expected in entries.items():
                    body = read_member(name)
                    self.assertEqual(hashlib.sha256(body).hexdigest(), expected, name)
                compose = read_member("compose.yaml").decode()
                self.assertIn("MING_BACKEND: local", compose)
                self.assertNotIn("ray-gpu-control", compose)
                self.assertNotIn("external: true", compose)
                dockerfile = read_member("Dockerfile").decode()
                self.assertNotIn("FROM local/", dockerfile)
                self.assertIn("python:3.12-slim-bookworm", dockerfile)
                self.assertIn("WEB_ASSETS=bundled", read_member(".env.example").decode())


if __name__ == "__main__":
    unittest.main()
