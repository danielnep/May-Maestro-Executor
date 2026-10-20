"""Shared, durable, conservative rolling-minute budget for free Groq models.

Groq Free publishes 30 RPM and 8,000 TPM *per model*, at organization scope.
Use 24 RPM / 7,200 estimated TPM over a 65s rolling window and space calls
by 2.5s. Never enforce a local daily limit. State-backed workers reuse the
same encrypted GitHub SQLite checkpoint; reservations are persisted BEFORE
HTTP inference when a callback is supplied.

Estimate is deliberately conservative but not exact tokenizer accounting.
Actual usage refines the window; upstream headers/429 remain authoritative.
"""
import json
import math
import sqlite3
import time
import uuid

WINDOW_SECONDS = 65.0
SAFE_REQUESTS = 24
SAFE_TOKENS = 7200
MIN_SPACING_SECONDS = 2.5
NAMESPACE = "__runtime__"
AGENT = "groq-minute-budget"


def estimated_input_tokens(messages):
    """UTF-8 length proxy with overhead, safer than plain word counting."""
    packed = json.dumps(messages, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return max(64, math.ceil(len(packed) / 2.5) + 128)


class GroqMinuteBudget:
    def __init__(self, state=None, clock=None, persist=None):
        self.state = state
        self.clock = clock or time.time
        self.persist = persist
        self._local = {}

    def _read(self, model):
        if self.state is None:
            return list(self._local.get(model, []))
        if isinstance(getattr(self.state, "db", None), sqlite3.Connection):
            row = self.state.db.execute(
                "SELECT value FROM memory WHERE project=? AND agent=? AND key=?",
                (NAMESPACE, AGENT, model)).fetchone()
            return json.loads(row["value"]) if row else []
        return list(self.state.recall(NAMESPACE, AGENT).get(model, []))

    def _write(self, model, records):
        if self.state is None:
            self._local[model] = records
        elif getattr(self.state, "db", None) is not None:
            self.state.db.execute(
                "INSERT INTO memory(project,agent,key,value) VALUES(?,?,?,?) "
                "ON CONFLICT(project,agent,key) DO UPDATE SET value=excluded.value",
                (NAMESPACE, AGENT, model,
                 json.dumps(records, separators=(",", ":"), allow_nan=False)))
        else:
            self.state.remember(NAMESPACE, AGENT, model, records)

    def _atomic(self, callback):
        if self.state is not None and isinstance(getattr(self.state, "db", None), sqlite3.Connection) and hasattr(self.state, "_tx"):
            with self.state._tx():
                return callback()
        return callback()

    def reserve(self, model, messages, max_output_tokens=1536):
        """Return (receipt, wait_seconds). No request is sent on a wait."""
        estimated = estimated_input_tokens(messages)
        if estimated + max_output_tokens > SAFE_TOKENS:
            # Reduce output headroom for large prompts instead of overspending.
            max_output_tokens = min(max_output_tokens, SAFE_TOKENS - estimated)
        if max_output_tokens < 384:
            return {"reason": "oversized_prompt", "input": estimated}, None, 0
        reserve_tokens = estimated + max_output_tokens
        now = self.clock()
        def do_reserve():
            rows = sorted(
                [row for row in self._read(model)
                 if isinstance(row, dict) and isinstance(row.get("at"), (int, float))
                 and 0 <= now - row["at"] < WINDOW_SECONDS],
                key=lambda row: row["at"])
            waits = []
            if rows:
                waits.append(max(0.0, MIN_SPACING_SECONDS - (now - rows[-1]["at"])))
            if len(rows) >= SAFE_REQUESTS:
                waits.append(max(0.0, rows[-SAFE_REQUESTS]["at"] + WINDOW_SECONDS - now))
            balance = sum(row["tokens"] for row in rows)
            if balance + reserve_tokens > SAFE_TOKENS:
                remaining = balance
                for row in rows:
                    remaining -= row["tokens"]
                    if remaining + reserve_tokens <= SAFE_TOKENS:
                        waits.append(max(0.0, row["at"] + WINDOW_SECONDS - now))
                        break
                else:
                    waits.append(WINDOW_SECONDS)
            seconds = max(waits, default=0.0)
            if seconds > 0:
                return None, max(0.05, seconds), max_output_tokens
            receipt = uuid.uuid4().hex
            rows.append({"id": receipt, "at": now, "tokens": reserve_tokens})
            self._write(model, rows)
            return receipt, 0.0, max_output_tokens
        result = self._atomic(do_reserve)
        if isinstance(result[0], str) and self.persist:
            self.persist()  # Remote checkpoint before any paid/provider request.
        return result

    def actual_usage(self, model, receipt, total_tokens):
        if not isinstance(total_tokens, int) or total_tokens < 0 or not receipt:
            return
        now = self.clock()
        def adjust():
            rows = self._read(model)
            for row in rows:
                if row.get("id") == receipt:
                    row["tokens"] = total_tokens
                    break
            else:
                return False
            self._write(model, rows)
            return True
        changed = self._atomic(adjust)
        if changed and self.persist:
            self.persist()
