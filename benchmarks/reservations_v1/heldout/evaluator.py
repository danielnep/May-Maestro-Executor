"""Private evaluator: mount outside the candidate's readable repair workspace."""
import concurrent.futures
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import random
import tempfile
import threading
import unittest


def load_target(path):
    spec = importlib.util.spec_from_file_location('candidate_engine', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def crash_process(target, database, stage, operation):
    module = load_target(target)
    engine = module.Engine(database, ttl=17)
    def crash(at):
        if at == stage:
            os._exit(73)
    if operation == 'reserve':
        engine.reserve('crash-tenant', 'request', 'item', 4, 100, failpoint=crash)
    else:
        engine.cancel('crash-tenant', 'request', 101, failpoint=crash)
    os._exit(74)


class ReservationRepairEvaluation(unittest.TestCase):
    def setUp(self):
        self.target = str(Path(os.environ['ENGINE_TARGET']).resolve())
        self.module = load_target(self.target)
        self.tmp = tempfile.TemporaryDirectory()
        self.database = str(Path(self.tmp.name) / 'ledger.sqlite')
        self.engine = self.module.Engine(self.database, ttl=17)

    def tearDown(self):
        self.engine.close()
        self.tmp.cleanup()

    def test_tenant_scope_payload_identity_and_terminal_retries(self):
        rng = random.Random(int(os.environ.get('BENCHMARK_SEED', '730517')) + 1)
        tenants = ['tenant-' + str(rng.getrandbits(72)) for _ in range(2)]
        request = 'request-' + str(rng.getrandbits(72))
        for tenant in tenants:
            self.engine.seed_stock(tenant, 'common-sku', 13)
            self.engine.seed_stock(tenant, 'other-sku', 13)
            row = self.engine.reserve(tenant, request, 'common-sku', 3, 100)
            self.assertEqual(row['tenant'], tenant)
            self.assertEqual(self.engine.snapshot(tenant)['inventory']['common-sku']['available'], 10)
        original = self.engine.reserve(tenants[0], request, 'common-sku', 3, 999)
        self.assertEqual(original['expires_at'], 117)
        for sku, qty in [('common-sku', 4), ('other-sku', 3)]:
            before = self.engine.snapshot(tenants[0])
            with self.assertRaises(self.module.Conflict):
                self.engine.reserve(tenants[0], request, sku, qty, 105)
            self.assertEqual(self.engine.snapshot(tenants[0]), before)
        self.engine.cancel(tenants[0], request, 110)
        terminal = self.engine.reserve(tenants[0], request, 'common-sku', 3, 115)
        self.assertEqual(terminal['state'], 'cancelled')
        self.assertEqual(len(self.engine.event_log(tenants[0])), 2)
        self.assertEqual(self.engine.snapshot(tenants[1])['reservations'][request]['state'], 'reserved')
        with self.assertRaises(LookupError):
            self.engine.cancel('unrelated', request, 110)

    def test_parallel_stock_duplicate_requests_and_exclusive_outbox_drain(self):
        self.engine.seed_stock('parallel', 'limited', 23)
        self.engine.seed_stock('parallel', 'duplicate', 7)
        barrier = threading.Barrier(32)
        def reserve(index):
            engine = self.module.Engine(self.database, ttl=17)
            try:
                barrier.wait(timeout=20)
                try:
                    row = engine.reserve('parallel', 'r-' + str(index), 'limited', 1, 100)
                    return ('ok', row)
                except self.module.OutOfStock:
                    return ('empty', None)
            finally:
                engine.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool:
            results = list(pool.map(reserve, range(32)))
        self.assertEqual(sum(kind == 'ok' for kind, _ in results), 23)
        self.assertEqual(sum(kind == 'empty' for kind, _ in results), 9)
        barrier = threading.Barrier(16)
        def duplicate(index):
            engine = self.module.Engine(self.database, ttl=17)
            try:
                barrier.wait(timeout=20)
                return engine.reserve('parallel', 'one-key', 'duplicate', 2, 101 + index)
            finally:
                engine.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            duplicates = list(pool.map(duplicate, range(16)))
        self.assertTrue(all(row == duplicates[0] for row in duplicates))
        snapshot = self.engine.snapshot('parallel')
        self.assertEqual(snapshot['inventory']['limited']['available'], 0)
        self.assertEqual(snapshot['inventory']['duplicate']['available'], 5)
        def drain(_):
            engine = self.module.Engine(self.database)
            found = []
            try:
                while True:
                    batch = engine.drain_outbox(limit=3)
                    if not batch:
                        return found
                    found.extend(batch)
            finally:
                engine.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            events = [event for batch in pool.map(drain, range(4)) for event in batch]
        self.assertEqual(len(events), 24)
        self.assertEqual(len({event['event_id'] for event in events}), 24)
        self.assertEqual(len(self.engine.event_log('parallel')), 24)
        self.assertEqual(self.engine.drain_outbox(), [])

    def test_actual_process_crash_rolls_back_state_and_outbox_together(self):
        self.engine.seed_stock('crash-tenant', 'item', 11)
        context = multiprocessing.get_context('spawn')
        for operation in ('reserve', 'cancel'):
            if operation == 'cancel':
                self.engine.reserve('crash-tenant', 'request', 'item', 4, 100)
            baseline = self.engine.snapshot('crash-tenant')
            events = self.engine.event_log('crash-tenant')
            for stage in ('after_state', 'after_event'):
                process = context.Process(target=crash_process, args=(self.target, self.database, stage, operation))
                process.start()
                process.join(timeout=20)
                if process.is_alive():
                    process.kill()
                    process.join()
                    self.fail('crash subprocess hung')
                self.assertEqual(process.exitcode, 73)
                restarted = self.module.Engine(self.database, ttl=17)
                try:
                    self.assertEqual(restarted.snapshot('crash-tenant'), baseline)
                    self.assertEqual(restarted.event_log('crash-tenant'), events)
                finally:
                    restarted.close()
        self.engine.cancel('crash-tenant', 'request', 101)
        self.assertEqual(self.engine.snapshot('crash-tenant')['inventory']['item']['available'], 11)
        self.assertEqual([event['kind'] for event in self.engine.event_log('crash-tenant')], ['reserved', 'cancelled'])

    def test_deadlines_exact_boundary_and_exception_injection_are_atomic(self):
        self.engine.seed_stock('clock', 'sku', 20)
        for index in range(3):
            self.engine.reserve('clock', str(index), 'sku', index + 1, 100)
        self.assertEqual(self.engine.expire(116), 0)
        self.assertEqual(self.engine.cancel('clock', '0', 117)['state'], 'expired')
        before = self.engine.snapshot('clock')
        events = self.engine.event_log('clock')
        def fault(stage):
            if stage == 'after_event':
                raise RuntimeError('deterministic fixture interruption')
        with self.assertRaises(RuntimeError):
            self.engine.expire(117, failpoint=fault)
        self.assertEqual(self.engine.snapshot('clock'), before)
        self.assertEqual(self.engine.event_log('clock'), events)
        self.assertEqual(self.engine.expire(117), 2)
        self.assertEqual(self.engine.expire(118), 0)
        for index in range(3):
            self.assertEqual(self.engine.cancel('clock', str(index), 119)['state'], 'expired')
            self.assertEqual(self.engine.reserve('clock', str(index), 'sku', index + 1, 120)['state'], 'expired')
        self.assertEqual(self.engine.snapshot('clock')['inventory']['sku']['available'], 20)
        self.assertEqual(len(self.engine.event_log('clock')), 6)

    def test_seeded_state_machine_traces_against_independent_domain_oracle(self):
        seed = int(os.environ.get('BENCHMARK_SEED', '730517'))
        for trace in range(112):
            rng = random.Random(seed + 7919 * trace)
            engine = self.module.Engine(str(Path(self.tmp.name) / ('trace-' + str(trace) + '.sqlite')), ttl=17)
            tenants = ['t-' + str(rng.getrandbits(80)) for _ in range(2)]
            totals = {(tenant, sku): rng.randint(8, 21) for tenant in tenants for sku in ('s-a', 's-b')}
            available = dict(totals)
            records, expected_events = {}, []
            now = rng.randint(0, 100)
            for (tenant, sku), total in totals.items():
                engine.seed_stock(tenant, sku, total)
            try:
                for _ in range(48):
                    now += rng.randint(0, 3)
                    tenant, sku = rng.choice(tenants), rng.choice(('s-a', 's-b'))
                    request, qty = 'r-' + str(rng.randrange(12)), rng.randint(1, 4)
                    key, operation = (tenant, request), rng.randrange(5)
                    if operation < 3:
                        old = records.get(key)
                        if old and (old['sku'], old['qty']) != (sku, qty):
                            with self.assertRaises(self.module.Conflict):
                                engine.reserve(tenant, request, sku, qty, now)
                        elif old:
                            self.assertEqual(engine.reserve(tenant, request, sku, qty, now), old)
                        elif available[tenant, sku] < qty:
                            with self.assertRaises(self.module.OutOfStock):
                                engine.reserve(tenant, request, sku, qty, now)
                        else:
                            record = dict(tenant=tenant, request_id=request, sku=sku, qty=qty, state='reserved', expires_at=now + 17)
                            self.assertEqual(engine.reserve(tenant, request, sku, qty, now), record)
                            records[key] = record
                            available[tenant, sku] -= qty
                            expected_events.append((tenant, request, sku, qty, 'reserved', now))
                    elif operation == 3:
                        if key not in records:
                            with self.assertRaises(LookupError):
                                engine.cancel(tenant, request, now)
                        else:
                            record = records[key]
                            if record['state'] == 'reserved':
                                kind = 'expired' if now >= record['expires_at'] else 'cancelled'
                                record['state'] = kind
                                available[tenant, record['sku']] += record['qty']
                                expected_events.append((tenant, request, record['sku'], record['qty'], kind, now))
                            self.assertEqual(engine.cancel(tenant, request, now), record)
                    else:
                        due = sorted((key for key, row in records.items() if row['state'] == 'reserved' and row['expires_at'] <= now))
                        for key in due:
                            row = records[key]
                            row['state'] = 'expired'
                            available[row['tenant'], row['sku']] += row['qty']
                            expected_events.append((row['tenant'], row['request_id'], row['sku'], row['qty'], 'expired', now))
                        self.assertEqual(engine.expire(now), len(due))
                    for tenant in tenants:
                        snapshot = engine.snapshot(tenant)
                        self.assertEqual(snapshot['reservations'], {request: row for (scope, request), row in records.items() if scope == tenant})
                        for sku in ('s-a', 's-b'):
                            stock = snapshot['inventory'][sku]
                            self.assertEqual(stock, {'total': totals[tenant, sku], 'available': available[tenant, sku]})
                            reserved = sum(row['qty'] for row in records.values() if row['tenant'] == tenant and row['sku'] == sku and row['state'] == 'reserved')
                            self.assertEqual(stock['available'] + reserved, stock['total'])
                    actual_events = [event for tenant in tenants for event in engine.event_log(tenant)]
                    actual = sorted((event['tenant'], event['request_id'], event['sku'], event['qty'], event['kind'], event['occurred_at']) for event in actual_events)
                    self.assertEqual(actual, sorted(expected_events))
            finally:
                engine.close()


class RedactedResult(unittest.TextTestResult):
    def _exc_info_to_string(self, err, test):
        return 'Held-out expectation failed: ' + err[0].__name__ + ' (source and inputs withheld)'


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ReservationRepairEvaluation)
    result = unittest.TextTestRunner(verbosity=1, resultclass=RedactedResult).run(suite)
    print(json.dumps({'suite': 'reservations-v1-private', 'tests': result.testsRun,
                      'passed': result.testsRun - len(result.failures) - len(result.errors),
                      'failures': len(result.failures), 'errors': len(result.errors),
                      'seeded_traces': 112, 'trace_operations': 5376}))
    raise SystemExit(0 if result.wasSuccessful() else 1)
