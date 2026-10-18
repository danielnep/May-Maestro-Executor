"""Private PostgreSQL RPC controller contract; no live Railway credentials."""
import json
from types import SimpleNamespace
import unittest

from horizon_core.railway_postgres import RailwayPostgresState, RailwayPostgresError, STATE_ENVIRONMENT_ID, METHODS
from horizon_core.state import LeaseError


class RailwayPostgresTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.response = {'ok': True, 'sandbox_id': 'private-state-fixture', 'result': {'fixture': True}}
        self.returncode = 0
        self.env = {'PATH': '/usr/bin', 'HOME': '/tmp', 'RAILWAY_API_TOKEN': 'PRIVATE-RAILWAY-TOKEN',
                    'RAILWAY_STATE_ENVIRONMENT_ID': STATE_ENVIRONMENT_ID,
                    'GITHUB_TOKEN': 'NEVER-SEND-GITHUB', 'GEMINI_API_KEY': 'NEVER-SEND-MODEL',
                    'HORIZON_DATABASE_URL': 'postgres://user:NEVER-SEND-DSN@public-host/db',
                    'NODE_OPTIONS': '--import=untrusted.js'}

    def runner(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return SimpleNamespace(stdout=json.dumps(self.response), stderr='SECRET-PROVIDER-BODY', returncode=self.returncode)

    def client(self, **kwargs):
        return RailwayPostgresState(environ=self.env, runner=self.runner, **kwargs)

    def test_all_state_methods_dispatch_only_arguments_and_reuse_id(self):
        state = self.client()
        for method in METHODS:
            if method == 'list_tasks_snapshot':
                self.assertEqual(state.list_tasks_snapshot(), {'fixture': True})
            else:
                self.assertEqual(getattr(state, method)('fixture', now=1), {'fixture': True})
            body = json.loads(self.calls[-1][1]['input'])
            self.assertEqual(body['method'], method)
            self.assertEqual(body['schema'], 'horizon_agents')
        self.assertEqual(json.loads(self.calls[-1][1]['input'])['sandbox_id'], 'private-state-fixture')
        self.assertEqual(state.sandbox_id, 'private-state-fixture')

    def test_child_controller_does_not_receive_db_model_github_or_node_options(self):
        self.client().health()
        command, options = self.calls[-1]
        self.assertEqual(command[0], 'node')
        self.assertTrue(command[1].endswith('sandbox/postgres_bridge.mjs'))
        child = options['env']
        self.assertEqual(child['RAILWAY_API_TOKEN'], self.env['RAILWAY_API_TOKEN'])
        for key in ('GITHUB_TOKEN', 'GEMINI_API_KEY', 'HORIZON_DATABASE_URL', 'NODE_OPTIONS'):
            self.assertNotIn(key, child)
            self.assertNotIn(self.env[key], options['input'])
        self.assertEqual(json.loads(options['input'])['operation'], 'health')

    def test_wrong_environment_and_schema_fail_before_dispatch(self):
        self.env['RAILWAY_STATE_ENVIRONMENT_ID'] = 'customer-environment'
        with self.assertRaises(RailwayPostgresError):
            self.client().health()
        self.assertFalse(self.calls)
        for schema in ('public', 'pg_catalog', '../app', 'x;DROP'):
            self.env['HORIZON_STATE_SCHEMA'] = schema
            with self.assertRaises(ValueError):
                self.client()

    def test_known_errors_mapped_without_remote_body(self):
        for kind, expected in (('LeaseError', LeaseError), ('ValueError', ValueError), ('PostgresStateError', RailwayPostgresError)):
            self.response = {'ok': False, 'error': {'type': kind, 'message': 'SECRET-DSN-PASSWORD'}}
            with self.subTest(kind=kind), self.assertRaises(expected) as error:
                self.client().claim('worker')
            self.assertNotIn('SECRET', str(error.exception))

    def test_transport_failure_is_not_retried_or_disclosed(self):
        def fail(*args, **kwargs):
            self.calls.append(True)
            raise RuntimeError('postgres://SECRET-DSN')
        state = RailwayPostgresState(environ=self.env, runner=fail)
        with self.assertRaisesRegex(RailwayPostgresError, 'outcome may be unknown') as error:
            state.claim('worker')
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn('SECRET', str(error.exception))

    def test_nonzero_exit_and_invalid_envelope_do_not_pass(self):
        for response in ([], {'ok': 'true'}, {'ok': True, 'sandbox_id': '../untrusted'}):
            self.response = response
            with self.assertRaises(RailwayPostgresError):
                self.client().health()
        self.response = {'ok': True, 'sandbox_id': 'fixture', 'result': 'pretended-success'}
        self.returncode = 1
        with self.assertRaises(RailwayPostgresError):
            self.client().health()

    def test_close_does_not_dispatch_or_destroy_and_blocks_further_requests(self):
        state = self.client()
        state.close()
        self.assertFalse(self.calls)
        with self.assertRaisesRegex(RailwayPostgresError, 'closed'):
            state.tasks()

    def test_generic_sql_and_exec_methods_not_exposed(self):
        state = self.client()
        for name in ('execute', 'db', 'run_check', 'destroy'):
            self.assertFalse(hasattr(state, name))
        with self.assertRaises(ValueError):
            state._request(method='execute', args=('DROP DATABASE customer',))


if __name__ == '__main__':
    unittest.main()
