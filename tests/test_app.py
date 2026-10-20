"""Entrypoints and inbox import tested without credentials or live requests."""
from contextlib import redirect_stdout
import io
import json
import unittest
from unittest.mock import patch

from horizon_core.app import import_tasks, main
from horizon_core.registry import AGENT_IDS
from horizon_core.state import State


class AppTests(unittest.TestCase):
    def test_list_all_specialists_without_starting_gateway(self):
        output = io.StringIO()
        with patch('horizon_core.app.ModelGateway') as gateway, redirect_stdout(output):
            self.assertEqual(main(['--list']), 0)
        self.assertEqual({agent['id'] for agent in json.loads(output.getvalue())}, set(AGENT_IDS))
        gateway.assert_not_called()

    def test_preflight_reports_presence_and_never_discloses_key_or_calls_provider(self):
        output = io.StringIO()
        with patch.dict('os.environ', {'GEMINI_API_KEY': 'SENSITIVE-KEY-FIXTURE',
                                      'GEMINI_MODEL': 'fixture-model',
                                      'GEMINI_FREE_TIER_CONFIRMED': 'true'}, clear=True), \
                patch('horizon_core.app.ModelGateway') as gateway, redirect_stdout(output):
            self.assertEqual(main(['--preflight']), 0)
        document = json.loads(output.getvalue())
        self.assertNotIn('SENSITIVE-KEY-FIXTURE', output.getvalue())
        self.assertEqual(document['requests_sent'], 0)
        self.assertFalse(document['billing_verified'])
        self.assertTrue(all(document['configuration'].values()))
        gateway.assert_not_called()

    def test_preflight_accepts_groq_only_without_starting_any_provider(self):
        output = io.StringIO()
        with patch.dict('os.environ', {'Groq_API_KEY': 'Groq-PRIVATE-FIXTURE'}, clear=True), \
                patch('horizon_core.app.ModelGateway') as gateway, redirect_stdout(output):
            self.assertEqual(main(['--preflight']), 0)
        document = json.loads(output.getvalue())
        self.assertFalse(any(document['configuration'].values()))
        self.assertTrue(document['groq']['api_key_present'])
        self.assertTrue(document['groq']['model'])
        self.assertEqual(document['requests_sent'], 0)
        self.assertFalse(document['billing_verified'])
        self.assertNotIn('Groq-PRIVATE-FIXTURE', output.getvalue())
        gateway.assert_not_called()

    def test_preflight_missing_configuration_is_not_ready(self):
        output = io.StringIO()
        with patch.dict('os.environ', {}, clear=True), redirect_stdout(output):
            self.assertEqual(main(['--preflight']), 2)
        self.assertFalse(any(json.loads(output.getvalue())['configuration'].values()))

    def test_import_tasks_is_idempotent(self):
        state = State(':memory:')
        self.addCleanup(state.close)
        inbox = [{'task_id': 'synthetic-task', 'agent': 'frontend-engineer',
                  'project': 'synthetic', 'objective': 'Read synthetic UI fixture',
                  'payload': {'branch': 'feat/fixture', 'allowed_paths': ['app/ui.py']}}]
        import_tasks(state, inbox)
        import_tasks(state, inbox)
        self.assertEqual(state.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 1)
        self.assertEqual(state.db.execute('SELECT COUNT(*) FROM events').fetchone()[0], 1)

    def test_invalid_specialist_or_task_id_cannot_enter_queue(self):
        for updates in ({'agent': '../frontend-engineer'}, {'task_id': ''}):
            with self.subTest(updates=updates):
                state = State(':memory:')
                try:
                    task = {'task_id': 'fixture', 'agent': 'frontend-engineer',
                            'project': 'synthetic', 'objective': 'Read fixture', **updates}
                    with self.assertRaises(ValueError):
                        import_tasks(state, [task])
                    self.assertEqual(state.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 0)
                finally:
                    state.close()

    def test_same_id_with_changed_specification_is_rejected(self):
        state = State(':memory:')
        self.addCleanup(state.close)
        task = {'task_id': 'fixture', 'agent': 'frontend-engineer',
                'project': 'synthetic', 'objective': 'Read fixture'}
        import_tasks(state, [task])
        with self.assertRaisesRegex(ValueError, 'different content'):
            import_tasks(state, [{**task, 'objective': 'Changed objective'}])

    def test_string_allowlist_is_rejected_not_expanded_to_wildcard(self):
        state = State(':memory:')
        try:
            with self.assertRaises(ValueError):
                import_tasks(state, [{'task_id': 'scope', 'agent': 'frontend-engineer',
                    'project': 'fixture', 'objective': 'Test scope',
                    'payload': {'allowed_paths': '*.py'}}])
        finally:
            state.close()


if __name__ == '__main__':
    unittest.main()
