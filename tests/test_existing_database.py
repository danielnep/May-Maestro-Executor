import os
import unittest
from urllib.parse import urlsplit
from horizon_core.existing_database import agent_dsn, provision, DATABASE, ROLE


class ExistingDatabaseTests(unittest.TestCase):
    def test_derived_role_is_separate_and_password_does_not_reuse_admin_password(self):
        original = 'postgresql://admin:synthetic%21admin@postgres.railway.internal:5432/client?sslmode=prefer'
        result = agent_dsn(original)
        self.assertEqual(result, agent_dsn(original))
        parts = urlsplit(result)
        self.assertEqual(parts.username, ROLE)
        self.assertEqual(parts.path, '/' + DATABASE)
        self.assertEqual(parts.query, 'sslmode=prefer')
        self.assertNotIn('synthetic', result)
        self.assertEqual(len(parts.password), 64)

    @unittest.skipUnless(os.getenv('POSTGRES_TEST_DSN'), 'Disposable real PostgreSQL required')
    def test_provision_is_idempotent_and_uses_non_superuser_separate_database(self):
        first = provision(os.environ['POSTGRES_TEST_DSN'])
        second = provision(os.environ['POSTGRES_TEST_DSN'])
        self.assertEqual(first['database'], DATABASE)
        self.assertEqual(first['role'], ROLE)
        self.assertFalse(first['superuser'])
        self.assertFalse(second['new_service_created'])
        import psycopg
        with psycopg.connect(agent_dsn(os.environ['POSTGRES_TEST_DSN'])) as connection:
            self.assertFalse(connection.execute("SELECT has_database_privilege(current_user, current_database(), 'CREATE') IS NULL").fetchone()[0])
