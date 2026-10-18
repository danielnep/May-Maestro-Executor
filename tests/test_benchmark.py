"""Meta-controls of trusted benchmark fixtures, never local generated-code execution."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


BENCHMARK = Path(__file__).resolve().parents[1] / 'benchmarks' / 'reservations_v1'


class BenchmarkFixtureControls(unittest.TestCase):
    def evaluate(self, target, seed):
        env = dict(os.environ, ENGINE_TARGET=str(target), BENCHMARK_SEED=str(seed), PYTHONDONTWRITEBYTECODE='1')
        result = subprocess.run([sys.executable, str(BENCHMARK / 'heldout/evaluator.py')],
                                env=env, capture_output=True, text=True, timeout=180)
        summary = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(summary['tests'], 5)
        return result, summary

    def test_broken_starter_is_rejected_with_redacted_diagnostics(self):
        result, summary = self.evaluate(BENCHMARK / 'starter/engine.py', 730517)
        self.assertEqual(result.returncode, 1)
        self.assertLess(summary['passed'], 5)
        self.assertGreaterEqual(summary['failures'] + summary['errors'], 3)
        self.assertNotIn('Traceback', result.stderr)
        self.assertNotIn('assertEqual(', result.stderr)
        self.assertNotIn('SELECT ', result.stderr)

    def test_trusted_reference_passes_dense_private_oracle_and_public_contract(self):
        for seed in (730517, 930731):
            with self.subTest(seed=seed):
                result, summary = self.evaluate(BENCHMARK / 'reference/engine.py', seed)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(summary['passed'], 5)
                self.assertEqual(summary['seeded_traces'], 112)
        with tempfile.TemporaryDirectory() as directory:
            for source in (BENCHMARK / 'reference/engine.py', BENCHMARK / 'starter/test_public.py'):
                shutil.copyfile(source, Path(directory) / source.name)
            result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', directory,
                                     '-p', 'test_public.py', '-v'], capture_output=True, text=True,
                                    env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
