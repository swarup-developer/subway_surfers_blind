from __future__ import annotations

import sys
import traceback
import unittest
from pathlib import Path

import py_compile


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCAN_ROOTS = (PROJECT_ROOT / "subway_blind", PROJECT_ROOT / "tests")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def scan_python_files() -> list[str]:
    issues: list[str] = []
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            try:
                py_compile.compile(str(path), doraise=True)
            except py_compile.PyCompileError as exc:
                error = exc.exc_value
                line = getattr(error, "lineno", "?")
                issues.append(f"COMPILE {path}:{line}: {error.msg}")
            except Exception as exc:
                issues.append(f"SCAN {path}: {exc}")
    return issues


def run_tests() -> tuple[bool, list[str]]:
    loader = unittest.defaultTestLoader
    suite = loader.discover(str(PROJECT_ROOT / "tests"))
    result = unittest.TestResult()
    suite.run(result)
    issues: list[str] = []
    for test_case, failure in result.failures:
        issues.append(f"FAIL {test_case.id()}\n{failure}")
    for test_case, error in result.errors:
        issues.append(f"ERROR {test_case.id()}\n{error}")
    return result.wasSuccessful(), issues


def main() -> int:
    issues = scan_python_files()
    tests_ok, test_issues = run_tests()
    issues.extend(test_issues)
    if issues:
        print("Full scan found issues:")
        for issue in issues:
            print(issue)
        return 1
    print("Full scan passed: compile scan and unit tests completed without errors.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        sys.exit(1)
