"""No-network regressions for the shared Groq Free RPM/TPM scheduler."""
import unittest

from horizon_core.groq_minute_budget import (
    GroqMinuteBudget, SAFE_REQUESTS, SAFE_TOKENS, estimated_input_tokens)
from horizon_core.state import State


class GroqMinuteBudgetTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.state = State(":memory:")
        self.addCleanup(self.state.close)
        self.budget = GroqMinuteBudget(self.state, clock=lambda: self.now)
        self.messages = [{"role": "system", "content": "Return a small JSON action"},
                         {"role": "user", "content": "Check work queue"}]

    def test_free_limits_with_headroom_and_no_daily_cap(self):
        self.assertLess(SAFE_REQUESTS, 30)
        self.assertLess(SAFE_TOKENS, 8000)
        self.assertGreater(estimated_input_tokens(self.messages), 0)
        self.assertFalse(hasattr(self.budget, "daily_requests"))
        self.assertEqual(SAFE_REQUESTS, 24)
        self.assertEqual(SAFE_TOKENS, 7200)

    def test_durable_reservation_blocks_second_call_in_same_second(self):
        receipt, wait, output = self.budget.reserve("openai/gpt-oss-120b", self.messages, 900)
        self.assertTrue(receipt)
        self.assertEqual(wait, 0)
        recovered = GroqMinuteBudget(self.state, clock=lambda: self.now)
        second, seconds, _ = recovered.reserve("openai/gpt-oss-120b", self.messages, 900)
        self.assertIsNone(second)
        self.assertGreaterEqual(seconds, 2.5)
        self.now += 2.6
        again, delay, _ = recovered.reserve("openai/gpt-oss-120b", self.messages, 900)
        self.assertTrue(again)
        self.assertEqual(delay, 0)

    def test_org_level_window_is_shared_not_17_per_agent_buckets(self):
        receipts = []
        for _ in range(35):
            reservation, seconds, _ = self.budget.reserve("qwen/qwen3.8-27b", self.messages, 400)
            if not reservation:
                self.assertGreater(seconds, 0)
                break
            receipts.append(reservation)
            self.now += 2.6
        self.assertLessEqual(len(receipts), SAFE_REQUESTS)
        self.assertGreater(len(receipts), 0)

    def test_separate_models_get_separate_organization_windows(self):
        for model in ("openai/gpt-oss-120b", "qwen/qwen3.8-27b"):
            receipt, delay, _ = self.budget.reserve(model, self.messages, 1000)
            self.assertTrue(receipt)
            self.assertEqual(delay, 0)

    def test_actual_usage_refines_token_budget_across_resume(self):
        model = "qwen/qwen3.8-27b"
        first, _, _ = self.budget.reserve(model, self.messages, 1400)
        self.budget.actual_usage(model, first, 400)
        stored = self.state.recall("__runtime__", "groq-minute-budget")[model]
        self.assertEqual(stored[-1]["tokens"], 400)
        self.now += 3
        second = GroqMinuteBudget(self.state, clock=lambda: self.now)
        receipt, _, _ = second.reserve(model, self.messages, 1400)
        self.assertTrue(receipt)

    def test_tokens_not_just_rpm_protect_expensive_prompts(self):
        long_text = "a" * 10000
        messages = [{"role": "user", "content": long_text}]
        receipt, wait, cap = self.budget.reserve("openai/gpt-oss-120b", messages, 1536)
        self.assertTrue(receipt)
        self.assertGreaterEqual(cap, 384)
        self.now += 2.6
        next_receipt, wait, _ = self.budget.reserve(
            "openai/gpt-oss-120b", messages, 1536)
        self.assertIsNone(next_receipt)
        self.assertGreater(wait, 0)

    def test_oversized_prompt_cannot_use_free_minute_cap(self):
        message = [{"role": "user", "content": "a" * 20000}]
        decision, wait, output = self.budget.reserve("qwen/qwen3.8-27b", message)
        self.assertEqual(decision["reason"], "oversized_prompt")
        self.assertIsNone(wait)
        self.assertEqual(output, 0)

    def test_official_headers_override_stricter_project_token_cap(self):
        model = "openai/gpt-oss-120b"
        self.budget.ingest_headers(model, {
            "x-ratelimit-limit-tokens": "3000",
            "x-ratelimit-remaining-tokens": "100",
            "x-ratelimit-reset-tokens": "25s",
            "x-ratelimit-remaining-requests": "1"})
        receipt, wait, _ = self.budget.reserve(model, self.messages, 600)
        self.assertIsNone(receipt)
        self.assertGreaterEqual(wait, 25)
        self.now += 26
        receipt, wait, _ = self.budget.reserve(model, self.messages, 600)
        self.assertTrue(receipt)
        self.assertEqual(wait, 0)

    def test_header_reset_duration_parsing(self):
        self.assertEqual(GroqMinuteBudget._seconds("2m7.5s"), 127.5)
        self.assertEqual(GroqMinuteBudget._seconds("7.66s"), 7.66)
        self.assertEqual(GroqMinuteBudget._seconds("not-a-duration"), 0)

    def test_window_expiration_allows_new_calls(self):
        self.assertTrue(self.budget.reserve("qwen/qwen3.8-27b", self.messages)[0])
        self.now += 66
        self.assertTrue(self.budget.reserve("qwen/qwen3.8-27b", self.messages)[0])


if __name__ == "__main__":
    unittest.main()
