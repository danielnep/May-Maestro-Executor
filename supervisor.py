#!/usr/bin/env python3
"""Compatibility entry point for the API runtime, without an agent CLI."""
import sys
from horizon_core.app import main

if __name__ == '__main__':
    if '--self-test' in sys.argv:
        import unittest
        suite = unittest.defaultTestLoader.discover('tests')
        raise SystemExit(0 if unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful() else 1)
    raise SystemExit(main())
