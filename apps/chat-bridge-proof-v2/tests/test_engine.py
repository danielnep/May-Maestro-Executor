import unittest
import sys
from pathlib import Path

app_dir = Path(__file__).resolve().parent.parent
if str(app_dir) not in sys.path:
    sys.path.insert(0, str(app_dir))

from engine import ReservationEngine


class TestReservationEngine(unittest.TestCase):
    def setUp(self):
        self.engine = ReservationEngine(":memory:")
        self.tenant = "tenant-a"
        self.resource = "room-101"
        self.engine.add_resource(self.resource, capacity=2)

    def test_create_reservation_and_capacity(self):
        res1 = self.engine.reserve(self.tenant, self.resource, "idemp-1", payload_hash="user-1")
        self.assertEqual(res1["status"], "CONFIRMED")

        res2 = self.engine.reserve(self.tenant, self.resource, "idemp-2", payload_hash="user-2")
        self.assertEqual(res2["status"], "CONFIRMED")

        with self.assertRaises(ValueError) as ctx:
            self.engine.reserve(self.tenant, self.resource, "idemp-3", payload_hash="user-3")
        self.assertIn("Capacity exhausted", str(ctx.exception))

    def test_idempotency_and_replay_conflict(self):
        res1 = self.engine.reserve(self.tenant, self.resource, "idemp-same", payload_hash="user-1")
        res_replay = self.engine.reserve(self.tenant, self.resource, "idemp-same", payload_hash="user-1")
        self.assertEqual(res1["reservation_id"], res_replay["reservation_id"])
        self.assertTrue(res_replay["replayed"])

        with self.assertRaises(ValueError) as ctx:
            self.engine.reserve(self.tenant, self.resource, "idemp-same", payload_hash="user-DIFFERENT")
        self.assertIn("Idempotency conflict", str(ctx.exception))

    def test_cancellation_and_repeated_cancel(self):
        res = self.engine.reserve(self.tenant, self.resource, "idemp-cancel", payload_hash="user-1")

        c1 = self.engine.cancel(self.tenant, res["reservation_id"])
        self.assertEqual(c1["status"], "CANCELLED")
        self.assertFalse(c1["already_cancelled"])

        c2 = self.engine.cancel(self.tenant, res["reservation_id"])
        self.assertEqual(c2["status"], "CANCELLED")
        self.assertTrue(c2["already_cancelled"])

    def test_tenant_isolation(self):
        self.engine.add_resource("room-b", capacity=1)
        self.engine.reserve(self.tenant, self.resource, "id-a1", payload_hash="user-1")
        self.engine.reserve(self.tenant, self.resource, "id-a2", payload_hash="user-2")

        with self.assertRaises(ValueError):
            self.engine.reserve(self.tenant, self.resource, "id-a3", payload_hash="user-3")

        res_b = self.engine.reserve("tenant-b", "room-b", "id-b1", payload_hash="user-b")
        self.assertEqual(res_b["status"], "CONFIRMED")


if __name__ == "__main__":
    unittest.main()
