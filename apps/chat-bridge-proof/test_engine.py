import random
import threading
import unittest
import tempfile
import os
from engine import ReservationEngine

class TestReservationEngine(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp()
        self.engine = ReservationEngine(db_path=self.db_path)

    def tearDown(self):
        os.close(self.db_fd)
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_finite_capacity(self):
        self.engine.create_resource("res-1", 2)
        r1, status1 = self.engine.reserve("tenant-a", "res-1", "key-1", "payload-1")
        self.assertEqual(status1, "confirmed")
        r2, status2 = self.engine.reserve("tenant-a", "res-1", "key-2", "payload-2")
        self.assertEqual(status2, "confirmed")
        with self.assertRaises(ValueError) as ctx:
            self.engine.reserve("tenant-a", "res-1", "key-3", "payload-3")
        self.assertIn("Capacity exhausted", str(ctx.exception))

    def test_idempotency_and_conflict_rejection(self):
        self.engine.create_resource("res-1", 5)
        r1, status1 = self.engine.reserve("tenant-a", "res-1", "key-1", "payload-same")
        # Replay with identical payload returns existing reservation
        r2, status2 = self.engine.reserve("tenant-a", "res-1", "key-1", "payload-same")
        self.assertEqual(r1, r2)
        self.assertEqual(status1, status2)

        # Conflicting payload with same idempotency key must be rejected
        with self.assertRaises(ValueError) as ctx:
            self.engine.reserve("tenant-a", "res-1", "key-1", "payload-different")
        self.assertIn("Conflict", str(ctx.exception))

        # Different tenant can use the same key without conflict
        r3, status3 = self.engine.reserve("tenant-b", "res-1", "key-1", "payload-different")
        self.assertNotEqual(r1, r3)
        self.assertEqual(status3, "confirmed")

    def test_cancel_without_duplicate_refund(self):
        self.engine.create_resource("res-1", 1)
        r1, _ = self.engine.reserve("tenant-a", "res-1", "key-1", "p1")
        
        # Cannot reserve second because capacity is 1
        with self.assertRaises(ValueError):
            self.engine.reserve("tenant-a", "res-1", "key-2", "p2")

        # Cancel reservation
        status = self.engine.cancel("tenant-a", r1)
        self.assertEqual(status, "cancelled")

        # Capacity refunded once, now can reserve
        r2, _ = self.engine.reserve("tenant-a", "res-1", "key-2", "p2")
        self.assertEqual(status, "cancelled")

        # Repeat cancel on r1 - idempotent, should NOT increase capacity again
        status_repeat = self.engine.cancel("tenant-a", r1)
        self.assertEqual(status_repeat, "cancelled")

        # Still capacity should be 0 because r2 occupies it, cannot reserve r3
        with self.assertRaises(ValueError):
            self.engine.reserve("tenant-a", "res-1", "key-3", "p3")

    def test_concurrent_threads_competing_for_last_capacity_and_seed(self):
        random.seed(42)
        # Resource capacity = 1
        self.engine.create_resource("res-contest", 1)
        
        num_threads = 10
        results = []
        errors = []
        threads = []
        barrier = threading.Barrier(num_threads)

        def worker(thread_idx):
            barrier.wait()
            # pseudo-random jitter with fixed seed sequence
            jitter = random.uniform(0.001, 0.01)
            try:
                res_id, status = self.engine.reserve(
                    tenant_id=f"tenant-{thread_idx}",
                    resource_id="res-contest",
                    idempotency_key=f"key-{thread_idx}",
                    payload_sig=f"payload-{thread_idx}"
                )
                results.append((thread_idx, res_id, status))
            except Exception as e:
                errors.append((thread_idx, str(e)))

        for i in range(num_threads):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # Exactly 1 thread must succeed, remaining 9 must fail with Capacity exhausted
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), num_threads - 1)
        for _, err in errors:
            self.assertIn("Capacity exhausted", err)

if __name__ == '__main__':
    unittest.main()
