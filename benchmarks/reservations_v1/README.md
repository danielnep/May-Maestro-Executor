# Reservation Repair v1

An isolated engineering benchmark of causal bug repair. It does **not** train model
weights. A candidate reads a public contract, broken source and public failures,
then produces a real source patch through the normal agent/runtime tools.

This fixture combines multitenancy, limited stock, request identity, SQLite
contention, durable outbox events and temporal state transitions. The starter has
five causal defects; a superficial public-test patch is insufficient for private
concurrency/crash/model-based validation. There are three public examples and five
dense private end-to-end tests, rather than hundreds of implementation-copy tests.

## Isolation: required runner layout

1. Create a fresh temporary Git worktree on a `feat/...` branch containing **only**
   `starter/engine.py`, `starter/test_public.py` and `contract.md` renamed/copied to
   their workspace root. That is the complete context readable by the repair agent.
2. Allow writes to `engine.py` only. Never give its file tools the maestro repository
   root: `reference/` and `heldout/` must be physically outside that readable root.
3. Run candidate code in a disposable Docker container with no network, no host
   credentials or Docker socket, dropped capabilities, read-only root filesystem,
   a writable temporary directory for fixture SQLite files and resource limits.
4. Mount `heldout/evaluator.py` read-only at `/evaluation/evaluator.py` **after**
   repair, outside `/project`, and set `ENGINE_TARGET=/project/engine.py`.
   No model is called while that mount is present. Do not expose evaluator source
   or raw private traces to the repair agent. The evaluator emits redacted results.
5. Keep `reference/` out of candidate containers entirely. It is a trusted control,
   never a source of patches, model context, retrieval or specialist memory.

The evaluator is a test oracle, not an adversarial-code security boundary. Docker
is the execution boundary. A candidate designed to tamper with the interpreter
needs separate integrity review. The runner should reject unexpected changed files,
model API traffic from evaluation and any attempted modification of test harnesses.

## Repeatable controls (trusted fixture code only)

From repository root:

```bash
ENGINE_TARGET=benchmarks/reservations_v1/starter/engine.py python3 benchmarks/reservations_v1/heldout/evaluator.py
ENGINE_TARGET=benchmarks/reservations_v1/reference/engine.py python3 benchmarks/reservations_v1/heldout/evaluator.py
python3 -m unittest discover -s tests -p test_benchmark.py -v
```

The first command must exit 1; the reference command must exit 0. These two
implementations are trusted fixtures and can be evaluated locally by developers.
Generated candidate code must use the isolated execution boundary above.
Public failures are available with `python3 -m unittest discover -s . -p test_public.py -v`
inside the isolated starter workspace. The reference is verified by copying it into
a separate temporary workspace alongside the same public tests, not by editing the
starter or giving the candidate access to it.

## Private measurements

Five tests independently cover tenant/payload/terminal identity, contention and
exclusive outbox dequeue, real process death/restart at transaction cut points,
exact temporal boundaries and batch rollback, and an independent domain-state
oracle. The final test executes **112 seeded traces of 48 operations = 5,376
operations**, comparing committed snapshots, conservation equations and event logs
after every operation. Contention uses 32 independent writers, 16 duplicate-request
writers and four drainers. Four spawned processes die at deterministic crash points.

`BENCHMARK_SEED` changes synthetic identifiers, totals and operation order. Run at
least two distinct evaluator seeds for a final claim. The seed is evaluator-only;
it must not become repair context. Metrics are pass counts out of five, public
pass count out of three, diff scope, model calls/provider identity, iterations,
wall time and any unavailable execution capabilities. A reported/finished agent
task is not a benchmark pass. A patched candidate passes only after all five private
tests and all three public tests pass on its produced file, in isolation.

The redacted JSON summary describes the configured workload. If a test fails early,
`trace_operations` is the configured total, not a claim that all were completed.
The parent runner records actual attempts and distinguishes fixture controls,
simulated model output and an actual API-driven repaired candidate.
