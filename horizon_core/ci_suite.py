"""Safe CI unit suite: do not execute the deprecated Playwright runners.

These old browser programs are retained for historical source reference only.
They do not certify current live UI. KERNEL live review is a separate owner gate.
"""
import unittest
from pathlib import Path

SKIP = {"test_browser_flow_cli.py", "test_web_checks.py",
        "test_tools_checks.py"}


def main():
    tests = Path(__file__).resolve().parent.parent / "tests"
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for path in sorted(tests.glob("test_*.py")):
        if path.name in SKIP:
            continue
        suite.addTests(loader.discover(str(tests), pattern=path.name))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
