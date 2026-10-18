import concurrent.futures
from pathlib import Path
import tempfile
import threading
import unittest

from horizon_core.state import LeaseError, State


class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'state.sqlite'
        self.state = State(self.path)

    def tearDown(self):
        self.state.close()
        self.tmp.cleanup()

    def enqueue(self, task_id, **kwargs):
        return self.state.enqueue(task_id, 'frontend', 'ronda', 'Improve UI', **kwargs)

    def test_idempotence_conflicting_content_and_dependency_rules(self):
        original = self.enqueue('one', payload={'b': 2, 'a': 1})
        same = self.enqueue('one', payload={'a': 1, 'b': 2})
        self.assertEqual(original, same)
        self.assertEqual(len(self.state.events('one')), 1)
        with self.assertRaises(ValueError):
            self.enqueue('one', payload={'a': 9})
        with self.assertRaisesRegex(ValueError, 'first'):
            self.enqueue('two', depends_on=['missing'])
        with self.assertRaisesRegex(ValueError, 'cycle'):
            self.enqueue('two', depends_on=['two'])
        self.enqueue('two', depends_on=['one'])
        # Changing an existing ancestor into a descendant is forbidden.
        with self.assertRaises(ValueError):
            self.enqueue('one', depends_on=['two'])

    def test_priority_fifo_time_and_dependencies(self):
        self.enqueue('first')
        self.enqueue('second')
        self.enqueue('dependent', depends_on=['first'], priority=100)
        self.enqueue('future', priority=999, not_before=200)
        self.enqueue('urgent', priority=2)
        self.assertEqual(self.state.claim('a', now=100)['task_id'], 'urgent')
        self.assertEqual(self.state.claim('b', now=100)['task_id'], 'first')
        self.state.complete('first', 'b', {}, now=101)
        self.assertEqual(self.state.claim('c', now=102)['task_id'], 'dependent')
        self.assertEqual(self.state.claim('d', now=102)['task_id'], 'second')
        self.assertIsNone(self.state.claim('e', now=102))
        self.assertEqual(self.state.claim('e', now=200)['task_id'], 'future')

    def test_concurrent_claims_two_connections(self):
        for index in range(12):
            self.enqueue(str(index))
        other = State(self.path)
        barrier = threading.Barrier(2)
        def claim_all(state, worker):
            barrier.wait()
            claimed = []
            while True:
                task = state.claim(worker, now=100)
                if task is None:
                    return claimed
                claimed.append(task['task_id'])
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(claim_all, self.state, 'one')
                second = pool.submit(claim_all, other, 'two')
                tasks = first.result() + second.result()
            self.assertEqual(len(tasks), 12)
            self.assertEqual(len(set(tasks)), 12)
        finally:
            other.close()

    def test_expiration_checkpoint_resume_and_fences(self):
        self.enqueue('one', max_attempts=2)
        self.state.claim('old', now=100, lease_seconds=10)
        self.state.checkpoint('one', 'old', {'step': 3, 'history': ['a']}, now=101)
        with self.assertRaises(LeaseError):
            self.state.complete('one', 'stranger', {}, now=102)
        for mutate in (
            lambda: self.state.checkpoint('one', 'old', {}, now=110),
            lambda: self.state.complete('one', 'old', {}, now=110),
            lambda: self.state.fail('one', 'old', 'late', now=110),
        ):
            with self.assertRaises(LeaseError):
                mutate()
        resumed = self.state.claim('new', now=110, lease_seconds=10)
        self.assertEqual(resumed['attempts'], 2)
        self.assertEqual(resumed['checkpoint']['step'], 3)
        with self.assertRaises(LeaseError):
            self.state.checkpoint('one', 'old', {}, now=111)
        self.assertIsNone(self.state.claim('third', now=120))
        self.assertEqual(self.state.get('one')['status'], 'failed')

    def test_quota_backoff_max_attempts_and_dependency_blocking(self):
        self.enqueue('one', max_attempts=2)
        self.enqueue('two', depends_on=['one'])
        self.enqueue('three', depends_on=['two'])
        self.state.claim('a', now=100)
        self.state.fail('one', 'a', {'code': 429, 'message': 'quota'}, now=101)
        self.assertEqual(self.state.get('one')['not_before'], 106)
        self.assertIsNone(self.state.claim('b', now=105))
        self.assertEqual(self.state.claim('b', now=106)['attempts'], 2)
        self.state.fail('one', 'b', 'quota', now=107)
        self.assertIsNone(self.state.claim('c', now=108))
        self.assertEqual(self.state.get('one')['status'], 'failed')
        self.assertEqual(self.state.get('two')['status'], 'blocked')
        self.assertEqual(self.state.get('three')['status'], 'blocked')
        self.assertIn('retry_scheduled', [e['kind'] for e in self.state.events('one')])

    def test_permanent_failure_does_not_retry(self):
        self.enqueue('one')
        self.state.claim('a', now=100)
        self.state.fail('one', 'a', 'invalid input', retryable=False, now=101)
        self.assertEqual(self.state.get('one')['status'], 'failed')
        self.assertIsNone(self.state.claim('a', now=999))

    def test_memory_scope_persistence_and_backup(self):
        self.state.remember('project-a', 'frontend', 'key', {'version': 1})
        self.state.remember('project-b', 'frontend', 'key', 'different project')
        self.state.remember('project-a', 'backend', 'key', 'different agent')
        self.state.remember('project-a', 'frontend', 'key', {'version': 2})
        self.enqueue('one')
        self.state.claim('a', now=100)
        self.state.checkpoint('one', 'a', {'step': 5}, now=101)
        destination = Path(self.tmp.name) / 'backup.sqlite'
        self.state.backup(destination)
        restored = State(destination)
        try:
            self.assertEqual(restored.recall('project-a', 'frontend'), {'key': {'version': 2}})
            self.assertEqual(restored.recall('project-a', 'other'), {})
            self.assertEqual(restored.get('one')['checkpoint']['step'], 5)
            restored.claim('b', now=401)
            restored.complete('one', 'b', {'ok': True}, now=402)
            self.assertEqual(restored.get('one')['result'], {'ok': True})
            self.assertEqual(self.state.get('one')['status'], 'running')
        finally:
            restored.close()


if __name__ == '__main__':
    unittest.main()
