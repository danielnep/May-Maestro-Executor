"""Regression checks for token-conscious source inspection."""
import tempfile
import unittest
from pathlib import Path
from horizon_core.tools import Tools


class BoundedSourceReadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tools = Tools(self.root, {'tools': ['read_file']})

    def test_large_file_supports_small_excerpt(self):
        (self.root / 'large.py').write_text('line\\n' * 20000, encoding='utf-8')
        result = self.tools.execute('read_file', {'path': 'large.py', 'start_line': 20, 'end_line': 22})
        self.assertEqual(result['content'], 'line\\n' * 3)
        with self.assertRaisesRegex(ValueError, 'request line range'):
            self.tools.execute('read_file', {'path': 'large.py'})

    def test_invalid_ranges_rejected(self):
        (self.root / 'code.py').write_text('hello\\n' * 300)
        for start, end in [(0, 2), (3, 2), (1, 202), (True, 2), (1, None)]:
            with self.subTest(start=start, end=end):
                with self.assertRaises(ValueError):
                    self.tools.execute('read_file', {'path': 'code.py', 'start_line': start, 'end_line': end})

    def test_regular_small_read_unchanged(self):
        (self.root / 'small.py').write_text('print(1)\\n')
        result = self.tools.execute('read_file', {'path': 'small.py'})
        self.assertEqual(result['content'], 'print(1)\\n')


if __name__ == '__main__':
    unittest.main()
