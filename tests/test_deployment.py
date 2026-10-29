"""Deployment packaging must never copy workspace credentials or runtime data."""

from pathlib import Path
import tarfile
import tempfile
import unittest

from deploy.package import build_archive


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("README.md", "medical_agent/web.py", "medical_agent/frontend/index.html",
                     "medical_agent/frontend/styles.css", "medical_agent/frontend/app.js",
                     "medical_agent/frontend/favicon.svg", "tests/test_web.py", "docs/deployment.md",
                     "lessons/03_tool_calling.py", "evals/questions.json", "evals/research_cases.json",
                     "evals/safety_cases.json", "evals/run_safety.py",
                     "deploy/package.py", "deploy/install.sh", "deploy/medisearch.service", "deploy/Caddyfile"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture", encoding="utf-8")

    def test_archive_uses_allowlist_and_refuses_overwrite(self):
        for name in (".aws", ".env", "outputs/runtime.sqlite3", "medical_agent/auth.json", "deploy/auth.json"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("private-fixture", encoding="utf-8")
        output = self.root / "outputs" / "deployment.tar.gz"
        self.assertEqual(build_archive(self.root, output), 17)
        with tarfile.open(output) as archive:
            names = archive.getnames()
            self.assertIn("deploy/medisearch.service", names)
            self.assertIn("medical_agent/frontend/app.js", names)
            self.assertIn("lessons/03_tool_calling.py", names)
            self.assertIn("evals/questions.json", names)
            self.assertTrue(all(member.uid == 0 and member.gid == 0 and member.mode == 0o644 for member in archive.getmembers()))
            self.assertFalse(any("auth.json" in name or name.startswith((".", "outputs/")) for name in names))
        before = output.read_bytes()
        with self.assertRaises(FileExistsError):
            build_archive(self.root, output)
        self.assertEqual(output.read_bytes(), before)

    def test_archive_rejects_symlink_before_creating_output(self):
        (self.root / "medical_agent" / "secret.py").symlink_to(self.root / "README.md")
        output = self.root / "deployment.tar.gz"
        with self.assertRaises(ValueError):
            build_archive(self.root, output)
        self.assertFalse(output.exists())
