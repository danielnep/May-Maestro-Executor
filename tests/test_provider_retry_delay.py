"""Provider outages cannot indefinitely accumulate a backoff after a real action."""
import unittest
from types import SimpleNamespace

from horizon_core.worker import provider_retry_delay


def issue(*reasons, wait=None):
    value = SimpleNamespace(failures=[{"reason": reason} for reason in reasons])
    if wait is not None:
        value.retry_after_seconds = wait
    return value


class ProviderRetryDelayTests(unittest.TestCase):
    def test_exponential_is_bounded(self):
        failure = issue("quota", "server")
        for size, expected in [(1, 60), (2, 120), (3, 240),
                               (4, 480), (5, 900), (40, 900)]:
            history = [{"action": "provider_retry"}] * size
            self.assertEqual(provider_retry_delay(history, failure), expected)

    def test_success_resets_old_backoff(self):
        history = ([{"action": "provider_retry"}] * 8 +
                   [{"action": "read_file"}] +
                   [{"action": "provider_retry"}])
        self.assertEqual(provider_retry_delay(history, issue("server")), 60)

    def test_real_local_groq_budget_hint_beats_exponential(self):
        history = [{"action": "provider_retry"}] * 10
        self.assertEqual(provider_retry_delay(
            history, issue("rate_limited_local", wait=82.1)), 83)
        self.assertEqual(provider_retry_delay(
            history, issue("rate_limited_local", wait=3600)), 3600)

    def test_unknown_or_invalid_hint_keeps_bounded_exponential(self):
        history = [{"action": "provider_retry"}] * 3
        self.assertEqual(provider_retry_delay(
            history, issue("server", wait=25)), 240)
        self.assertEqual(provider_retry_delay(
            history, issue("rate_limited_local", wait=-4)), 240)


if __name__ == "__main__":
    unittest.main()
