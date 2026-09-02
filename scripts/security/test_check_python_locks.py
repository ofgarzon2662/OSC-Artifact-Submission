#!/usr/bin/env python3

import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("check_python_locks.py")
SPEC = importlib.util.spec_from_file_location("check_python_locks", MODULE_PATH)
assert SPEC and SPEC.loader
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


class PythonLockTests(unittest.TestCase):
    def fixture(self, root: Path, lock: str) -> Path:
        (root / "requirements.txt").write_text("Demo_Pkg==1.2.3\n", encoding="utf-8")
        (root / "requirements.lock").write_text(lock, encoding="utf-8")
        return root

    def test_exact_hashed_lock_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = (
                "demo-pkg==1.2.3 \\\n"
                "    --hash=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
            )
            self.assertEqual([], CHECKER.check_component(self.fixture(Path(directory), lock)))

    def test_stale_direct_version_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = (
                "demo-pkg==1.2.2 \\\n"
                "    --hash=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
            )
            self.assertTrue(CHECKER.check_component(self.fixture(Path(directory), lock)))

    def test_unhashed_lock_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(Path(directory), "demo-pkg==1.2.3\n")
            self.assertTrue(CHECKER.check_component(root))

    def test_hashed_runtime_lock_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(
                Path(directory),
                "demo-pkg==1.2.3 \\\n"
                "    --hash=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n",
            )
            (root / "requirements.runtime.txt").write_text(
                "Demo_Pkg==1.2.3\n", encoding="utf-8"
            )
            (root / "requirements.runtime.lock").write_text(
                "demo-pkg==1.2.3 \\\n"
                "    --hash=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n",
                encoding="utf-8",
            )
            self.assertEqual([], CHECKER.check_component(root))

    def test_missing_runtime_lock_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(
                Path(directory),
                "demo-pkg==1.2.3 \\\n"
                "    --hash=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n",
            )
            (root / "requirements.runtime.txt").write_text(
                "demo-pkg==1.2.3\n", encoding="utf-8"
            )
            self.assertTrue(CHECKER.check_component(root))


if __name__ == "__main__":
    unittest.main()
