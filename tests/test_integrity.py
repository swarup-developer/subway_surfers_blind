from __future__ import annotations

import py_compile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE_ROOTS = (PROJECT_ROOT / "subway_blind", PROJECT_ROOT / "tests")


def _python_files() -> list[Path]:
    files: list[Path] = []
    for root in SOURCE_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if path.name == "__pycache__":
                continue
            files.append(path)
    return sorted(files)


class IntegrityScanTests(unittest.TestCase):
    def test_all_python_files_compile(self) -> None:
        compile_errors: list[str] = []
        for path in _python_files():
            try:
                py_compile.compile(str(path), doraise=True)
            except py_compile.PyCompileError as exc:
                error = exc.exc_value
                line = getattr(error, "lineno", "?")
                compile_errors.append(f"{path}:{line}: {error.msg}")
        if compile_errors:
            self.fail("Python compile scan failed:\n" + "\n".join(compile_errors))
