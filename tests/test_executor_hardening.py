"""Adversarial regressions: bounded requests and fresh verification evidence."""
import json
import unittest
from unittest.mock import patch
from horizon_core.context_budget import compact_history, MAX_HISTORY_TOTAL_CHARS
from horizon_core.worker import Worker
import test_agent_budgets as fixtures
Gateway, action = fixtures.Gateway, fixtures.action


class ContextRegression(unittest.TestCase):
    def test_many_large_fields_cannot_multiply_budget(self):
        history = [{'action': 'read_file', 'outcome': {
            **{str(i): '"\\\n' * 10000 for i in range(120)},
            'content': 'latest source' if n == 3 else 'old source'}} for n in range(4)]
        result = compact_history(history)
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False)), MAX_HISTORY_TOTAL_CHARS)
        self.assertIn('latest source', str(result[-1]))
        self.assertEqual(len(history[0]['outcome']), 121)


class FinishRegression(unittest.TestCase):
    setUp = fixtures.GuardTests.setUp

    def test_pass_before_source_edit_must_be_rerun(self):
        def go(ctx):
            step = ctx['completed_steps']
            if step == 0:
                return action('run_check', {'name': 'python-unittest'})
            if step == 1:
                return action('write_file', {'path': 'src/new.py', 'content': 'VALUE=2\n'})
            return action('finish')
        self.state.enqueue('stale', 'backend-engineer', 'scope', 'Repair and verify source',
                           payload={'branch': 'fix/task', 'allowed_paths': ['src/**']})
        with patch('horizon_core.tools.Tools.run_check', return_value={
                'check': 'python-unittest', 'passed': True, 'tests_run': 1, 'exit_code': 0}):
            result = Worker(self.state, Gateway(go), self.root).run_one()
        self.assertNotEqual(result['status'], 'succeeded')
        self.assertTrue(any(row['action'] == 'verification_required'
                            for row in result['checkpoint']['history']))

    def test_tool_error_is_replaced_by_successful_matching_check(self):
        def go(ctx):
            if ctx['completed_steps'] < 2:
                return action('run_check', {'name': 'python-unittest'})
            return action('finish')
        self.state.enqueue('recovery', 'qa-engineer', 'scope', 'Recover test infrastructure')
        with patch('horizon_core.tools.Tools.run_check', side_effect=[
                RuntimeError('temporary sandbox failure'),
                {'check': 'python-unittest', 'passed': True, 'tests_run': 1, 'exit_code': 0}]):
            result = Worker(self.state, Gateway(go), self.root).run_one()
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['result']['verification'][0]['check'], 'python-unittest')

    def test_fresh_rerun_after_edit_can_finish(self):
        def go(ctx):
            n = ctx['completed_steps']
            if n == 1:
                return action('write_file', {'path': 'src/new.py', 'content': 'VALUE=2\n'})
            if n in (0, 2):
                return action('run_check', {'name': 'python-unittest'})
            return action('finish')
        self.state.enqueue('fresh', 'backend-engineer', 'scope', 'Repair and verify source',
                           payload={'branch': 'fix/task', 'allowed_paths': ['src/**']})
        with patch('horizon_core.tools.Tools.run_check', return_value={
                'check': 'python-unittest', 'passed': True, 'tests_run': 1, 'exit_code': 0}):
            result = Worker(self.state, Gateway(go), self.root).run_one()
        self.assertEqual(result['status'], 'succeeded')
