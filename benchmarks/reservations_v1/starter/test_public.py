"""Public acceptance examples. No evaluator/reference files belong in this workspace."""
from pathlib import Path
import tempfile
import unittest
from engine import Conflict, Engine


class PublicContractExamples(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = Engine(Path(self.tmp.name) / 'fixture.sqlite', ttl=10)

    def tearDown(self):
        self.engine.close()
        self.tmp.cleanup()

    def test_basic_lifecycle_and_dequeue(self):
        self.engine.seed_stock('shop-a', 'coffee', 9)
        reserved = self.engine.reserve('shop-a', 'order-1', 'coffee', 3, 100)
        self.assertEqual(reserved['state'], 'reserved')
        self.assertEqual(self.engine.snapshot('shop-a')['inventory']['coffee']['available'], 6)
        self.assertEqual(self.engine.reserve('shop-a', 'order-1', 'coffee', 3, 102), reserved)
        self.engine.cancel('shop-a', 'order-1', 105)
        self.engine.cancel('shop-a', 'order-1', 106)
        self.assertEqual(self.engine.snapshot('shop-a')['inventory']['coffee']['available'], 9)
        self.assertEqual([event['kind'] for event in self.engine.drain_outbox()], ['reserved', 'cancelled'])
        self.assertEqual(self.engine.drain_outbox(), [])

    def test_tenant_identity_and_conflicting_retry(self):
        for tenant in ('shop-a', 'shop-b'):
            self.engine.seed_stock(tenant, 'coffee', 9)
            self.assertEqual(self.engine.reserve(tenant, 'same-id', 'coffee', 3, 100)['tenant'], tenant)
        with self.assertRaises(Conflict):
            self.engine.reserve('shop-a', 'same-id', 'coffee', 2, 101)

    def test_exact_deadline_releases_once(self):
        self.engine.seed_stock('shop-a', 'coffee', 9)
        self.engine.reserve('shop-a', 'order-1', 'coffee', 3, 100)
        self.assertEqual(self.engine.expire(109), 0)
        self.assertEqual(self.engine.expire(110), 1)
        self.assertEqual(self.engine.cancel('shop-a', 'order-1', 111)['state'], 'expired')
        self.assertEqual(self.engine.snapshot('shop-a')['inventory']['coffee']['available'], 9)


if __name__ == '__main__':
    unittest.main()
