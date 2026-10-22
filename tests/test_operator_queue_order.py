"""Operator ordering for the Railway-local direct task queue."""
import json
import tempfile
import unittest
from pathlib import Path

from horizon_core.commit_bridge import ordered_direct_requests


class TestDirectQueueOrder(unittest.TestCase):
    def test_operator_order_and_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            requests=Path(temp)/"requests"
            requests.mkdir()
            for name in ("ctl-a-one.json","ctl-b-two.json","ctl-c-three.json"):
                (requests/name).write_text("{}",encoding="utf-8")
            self.assertEqual([p.stem for p in ordered_direct_requests(requests)],
                             ["ctl-a-one","ctl-b-two","ctl-c-three"])
            (Path(temp)/"queue-order.json").write_text(
                json.dumps(["ctl-c-three","ctl-a-one","ctl-b-two"]),encoding="utf-8")
            self.assertEqual([p.stem for p in ordered_direct_requests(requests)],
                             ["ctl-c-three","ctl-a-one","ctl-b-two"])
            (Path(temp)/"queue-order.json").write_text('{"unexpected":true}',encoding="utf-8")
            self.assertEqual([p.stem for p in ordered_direct_requests(requests)],
                             ["ctl-a-one","ctl-b-two","ctl-c-three"])
            (Path(temp)/"queue-order.json").write_text(
                json.dumps(["../../unsafe","ctl-c-three"]),encoding="utf-8")
            self.assertEqual([p.stem for p in ordered_direct_requests(requests)],
                             ["ctl-a-one","ctl-b-two","ctl-c-three"])


if __name__ == "__main__":
    unittest.main()
