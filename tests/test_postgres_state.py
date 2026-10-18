"""Real PostgreSQL integration tests; no database mocks.

Set POSTGRES_TEST_DSN to a disposable PostgreSQL 17+ database and install
requirements-postgres.txt. Each test owns a randomly named horizon_test_* schema;
only that schema is removed in teardown. The CI PostgreSQL service is ephemeral.
Without POSTGRES_TEST_DSN the integration suite explicitly skips, not passes.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
import threading
import unittest
import uuid

from horizon_core.postgres_state import PostgresState
from horizon_core.registry import AGENT_IDS
from horizon_core.state import LeaseError


@unittest.skipUnless(os.getenv('POSTGRES_TEST_DSN'), 'POSTGRES_TEST_DSN required for real PostgreSQL integration')
class PostgresStateIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.dsn = os.environ['POSTGRES_TEST_DSN']
        self.schema = 'horizon_test_' + uuid.uuid4().hex
        self.state = PostgresState(self.dsn, self.schema)
        self.connections = []

    def tearDown(self):
        from psycopg import sql
        for connection in self.connections:
            connection.close()
        try:
            self.state.db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))
        finally:
            self.state.close()

    def peer(self):
        connection = PostgresState(self.dsn, self.schema)
        self.connections.append(connection)
        return connection

    def enqueue(self, task_id='task', **kwargs):
        return self.state.enqueue(task_id, 'backend-engineer', 'synthetic', 'Verify fixture', **kwargs)

    def test_jsonb_roundtrip_idempotency_and_changed_specification_rejection(self):
        payload = {'unicode': 'memória', 'nested': [True, None, 123, {'x': 'y'}]}
        first = self.enqueue(payload=payload)
        second = self.enqueue(payload=payload)
        self.assertEqual(first, second)
        self.assertEqual(self.state.get('task')['payload'], payload)
        self.assertEqual(len(self.state.events('task')), 1)
        with self.assertRaisesRegex(ValueError, 'different content'):
            self.enqueue(payload={'changed': True})
        self.assertEqual(self.state.get('task')['payload'], payload)
        self.assertEqual(len(self.state.tasks()), 1)

    def test_concurrent_idempotent_enqueue_has_one_task_and_event(self):
        peers = [self.peer() for _ in range(6)]
        barrier = threading.Barrier(len(peers))
        def insert(peer):
            barrier.wait(timeout=10)
            return peer.enqueue('same-id', 'qa-engineer', 'synthetic', 'Same request')['task_id']
        with ThreadPoolExecutor(max_workers=len(peers)) as pool:
            self.assertEqual(list(pool.map(insert, peers)), ['same-id'] * len(peers))
        self.assertEqual(len(self.state.tasks()), 1)
        self.assertEqual(len(self.state.events('same-id')), 1)

    def test_17_workers_claim_distinct_rows_and_preserve_all_specialist_memory(self):
        for index, ident in enumerate(AGENT_IDS):
            self.state.enqueue('work-' + str(index), ident, 'synthetic', 'Independent work')
            self.state.remember('synthetic', ident, 'context', {'specialist': ident})
        peers = [self.peer() for _ in AGENT_IDS]
        barrier = threading.Barrier(len(peers))
        def claim(item):
            index, peer = item
            barrier.wait(timeout=15)
            row = peer.claim('worker-' + str(index), now=100, lease_seconds=30)
            self.assertIsNotNone(row)
            peer.checkpoint(row['task_id'], 'worker-' + str(index), {'step': 'verified'}, now=101)
            peer.complete(row['task_id'], 'worker-' + str(index), {'passed': True}, now=102)
            return row['task_id']
        with ThreadPoolExecutor(max_workers=17) as pool:
            claimed = list(pool.map(claim, enumerate(peers)))
        self.assertEqual(len(set(claimed)), 17)
        self.assertTrue(all(task['status'] == 'succeeded' for task in self.state.tasks()))
        self.assertIsNone(self.state.claim('extra', now=103))
        for ident in AGENT_IDS:
            self.assertEqual(self.state.recall('synthetic', ident), {'context': {'specialist': ident}})
            self.assertEqual(self.state.recall('other-project', ident), {})

    def test_skip_locked_claim_does_not_wait_on_another_held_row(self):
        self.enqueue('locked', priority=10)
        self.enqueue('available', priority=0)
        peer = self.peer()
        with self.state._tx():
            self.state.db.execute('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE', ('locked',))
            claimed = peer.claim('other-worker', now=100)
            self.assertEqual(claimed['task_id'], 'available')
        self.assertEqual(self.state.claim('first-worker', now=100)['task_id'], 'locked')

    def test_dependency_success_unblocks_only_after_committed_completion(self):
        self.enqueue('first')
        self.enqueue('second', depends_on=['first'], priority=100)
        first = self.state.claim('worker-a', now=100)
        self.assertEqual(first['task_id'], 'first')
        self.assertIsNone(self.peer().claim('worker-b', now=101))
        self.state.complete('first', 'worker-a', {'checked': True}, now=102)
        self.assertEqual(self.state.claim('worker-c', now=103)['task_id'], 'second')
        with self.assertRaises(ValueError):
            self.enqueue('missing', depends_on=['not-enqueued'])
        with self.assertRaises(ValueError):
            self.enqueue('self', depends_on=['self'])
        self.assertIsNone(self.state.get('missing'))

    def test_dependency_failure_propagates_entire_dag(self):
        self.enqueue('first')
        self.enqueue('second', depends_on=['first'])
        self.enqueue('third', depends_on=['second'])
        self.state.claim('worker', now=100)
        self.state.fail('first', 'worker', {'failure': 'fixture'}, retryable=False, now=101)
        self.assertIsNone(self.state.claim('next', now=102))
        self.assertEqual(self.state.get('second')['status'], 'blocked')
        self.assertEqual(self.state.get('third')['status'], 'blocked')
        self.assertEqual(self.state.get('third')['error'], {'failed_dependencies': ['second']})

    def test_expired_lease_rejects_old_owner_and_restores_checkpoint(self):
        self.enqueue()
        self.state.claim('old', now=100, lease_seconds=5)
        self.state.checkpoint('task', 'old', {'completed_tool': 1}, now=101)
        replacement = self.peer().claim('new', now=106, lease_seconds=30)
        self.assertEqual(replacement['checkpoint'], {'completed_tool': 1})
        self.assertEqual(replacement['attempts'], 2)
        for mutation in (lambda: self.state.complete('task', 'old', {}, now=107),
                         lambda: self.state.renew_lease('task', 'old', now=107),
                         lambda: self.state.checkpoint('task', 'old', {}, now=107),
                         lambda: self.state.yield_task('task', 'old', now=107)):
            with self.assertRaises(LeaseError):
                mutation()
        self.assertEqual(self.state.get('task')['lease_owner'], 'new')

    def test_renew_and_yield_do_not_consume_failure_attempt(self):
        self.enqueue()
        self.state.claim('first', now=100, lease_seconds=10)
        self.assertEqual(self.state.renew_lease('task', 'first', now=105, lease_seconds=60), 165)
        self.state.checkpoint('task', 'first', {'step': 2}, now=106)
        self.state.yield_task('task', 'first', now=107)
        self.assertEqual(self.state.get('task')['attempts'], 0)
        replacement = self.peer().claim('next', now=108)
        self.assertEqual(replacement['attempts'], 1)
        self.assertEqual(replacement['checkpoint'], {'step': 2})
        self.assertIn('yielded', [event['kind'] for event in self.state.events('task')])

    def test_retry_backoff_and_terminal_attempt_limit(self):
        self.enqueue(max_attempts=2)
        self.state.claim('first', now=100)
        self.state.fail('task', 'first', {'provider': 'fixture'}, now=101)
        self.assertIsNone(self.state.claim('early', now=105))
        self.assertIsNotNone(self.state.claim('second', now=106))
        self.state.fail('task', 'second', {'provider': 'fixture'}, now=107)
        self.assertEqual(self.state.get('task')['status'], 'failed')
        self.assertIsNone(self.state.claim('third', now=200))

    def test_expiration_of_final_attempt_is_terminal(self):
        self.enqueue(max_attempts=1)
        self.state.claim('worker', now=100, lease_seconds=1)
        self.assertIsNone(self.state.claim('next', now=102))
        self.assertEqual(self.state.get('task')['status'], 'failed')

    def test_memory_is_durable_and_separated_by_agent_and_project(self):
        self.state.remember('p1', 'frontend-engineer', 'decision', {'layout': 'integrated'})
        self.state.remember('p1', 'backend-engineer', 'decision', {'auth': 'scoped'})
        self.state.remember('p2', 'frontend-engineer', 'decision', {'layout': 'other'})
        peer = self.peer()
        self.assertEqual(peer.recall('p1', 'frontend-engineer'), {'decision': {'layout': 'integrated'}})
        peer.remember('p1', 'frontend-engineer', 'decision', {'layout': 'approved'})
        self.assertEqual(self.state.recall('p1', 'frontend-engineer'), {'decision': {'layout': 'approved'}})
        self.assertEqual(self.state.recall('p1', 'backend-engineer'), {'decision': {'auth': 'scoped'}})
        self.assertEqual(self.state.recall('p2', 'frontend-engineer'), {'decision': {'layout': 'other'}})

    def test_transaction_rollback_includes_task_and_event(self):
        self.enqueue()
        before = self.state.events('task')
        with self.assertRaisesRegex(RuntimeError, 'forced rollback'):
            with self.state._tx():
                self.state.db.execute("UPDATE tasks SET objective='uncommitted' WHERE task_id=%s", ('task',))
                self.state._event('task', 'must_rollback', {'fixture': True})
                raise RuntimeError('forced rollback')
        self.assertEqual(self.state.get('task')['objective'], 'Verify fixture')
        self.assertEqual(self.state.events('task'), before)

    def test_project_queries_return_decoded_records_and_snapshot(self):
        self.enqueue('one', payload={'a': 1})
        self.state.enqueue('two', 'qa-engineer', 'another', 'Separate project')
        self.assertEqual([task['task_id'] for task in self.state.tasks('synthetic')], ['one'])
        self.assertEqual(self.state.tasks('synthetic')[0]['payload'], {'a': 1})
        self.assertEqual(len(self.state.list_tasks_snapshot()), 2)
        self.assertNotIn('specification', self.state.tasks()[0])
        json.dumps(self.state.list_tasks_snapshot())

    def test_atomic_project_filter_never_claims_another_project(self):
        self.state.enqueue('other', 'qa-engineer', 'other', 'Other project', priority=100)
        self.state.enqueue('selected', 'qa-engineer', 'selected', 'Selected project', priority=0)
        self.assertEqual(self.peer().claim('worker', now=100, project='selected')['task_id'], 'selected')
        self.assertEqual(self.state.get('other')['status'], 'queued')
        self.assertIsNone(self.state.claim('same-project', now=100, project='selected'))
        self.assertEqual(self.state.claim('other-worker', now=100, project='other')['task_id'], 'other')

    def test_schema_isolation_and_rejected_unsafe_identifiers(self):
        for schema in ('public', 'pg_catalog', 'information_schema', 'x;DROP SCHEMA public', '../other'):
            with self.assertRaises(ValueError):
                PostgresState(self.dsn, schema)
        other_schema = 'horizon_test_' + uuid.uuid4().hex
        other = PostgresState(self.dsn, other_schema)
        try:
            self.state.remember('p', 'agent', 'k', 'private to first schema')
            self.assertEqual(other.recall('p', 'agent'), {})
        finally:
            from psycopg import sql
            other.db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(other_schema)))
            other.close()


if __name__ == '__main__':
    unittest.main()
