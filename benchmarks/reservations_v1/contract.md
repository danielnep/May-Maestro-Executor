# Reservation engine: public acceptance contract

Repair `engine.py` with Python standard library only. Preserve this public API;
do not modify tests or replace SQLite with an in-memory implementation. Databases
are temporary benchmark fixtures containing synthetic tenants, never real projects.

## Objects and exceptions

`Engine(path, ttl=30)` opens a shared, durable SQLite database. The schema may be
improved or migrated without erasing existing data. Each concurrent thread/process
creates its own Engine instance for the same path. `close()` releases the connection.
`Conflict` and `OutOfStock` are public exception classes derived from `ValueError`.
`ttl` is a positive integer, excluding bool. Times are caller-supplied integer ticks;
there is no wall-clock dependence. Text identifiers are nonempty strings. Quantities
and limits are positive integers excluding bool. Stock totals are integers >= 0.
Invalid arguments raise ValueError before changing state.

## Inventory and reservations

- `seed_stock(tenant, sku, total)` initializes an inventory position exactly once.
  Repeating the same total is a no-op, even after reservations. Another total raises
  Conflict. Tenant + SKU identifies inventory; identical SKUs across tenants differ.
- `reserve(tenant, request_id, sku, qty, now, failpoint=None)` reserves `qty` units.
  Missing inventory or insufficient availability raises OutOfStock without writing
  a reservation, idempotency record or event. Success returns a plain dictionary:
  `tenant`, `request_id`, `sku`, `qty`, `state='reserved'`, `expires_at=now+ttl`.
- Tenant + request_id is the idempotency identity. A repeated request with the same
  SKU and quantity returns its **current persisted row**, including terminal state,
  without extending expiry, debiting stock again, or appending an event. Retry time
  is not part of payload identity. Changing SKU or quantity raises Conflict without
  mutation, including after cancellation/expiry. Other tenants may reuse the ID.
- `cancel(tenant, request_id, now, failpoint=None)` returns the persisted row. Unknown
  IDs in that tenant raise LookupError. A currently reserved row becomes `cancelled`
  when `now < expires_at`, otherwise `expired`, and releases stock exactly once.
  Terminal rows are returned unchanged, with no extra release/event.
- `expire(now, failpoint=None)` atomically transitions **all** reserved rows with
  `expires_at <= now` to expired across tenants, releases each allocation once and
  returns the number transitioned. Rows later than the boundary are unchanged.
- Expiry is explicit, through expire/cancel. Merely reading a snapshot or retrying
  reserve does not advance time or automatically expire a stored row.

At every committed snapshot, for each tenant/SKU:

`0 <= available <= total` and `available + sum(qty for reserved rows) == total`.

Competing reservations must serialize safely: callers receive valid reservations,
OutOfStock or Conflict as appropriate, never SQLite lock/constraint exceptions for
ordinary contention. No overselling, cross-tenant use or lost release is permitted.

## Events, durable atomicity and recovery

Every actual transition appends exactly one durable outbox event, in the same SQLite
transaction as its stock and reservation changes. Event kinds are `reserved`,
`cancelled`, `expired`. No event is generated for retries, rejected calls or reads.
An event has exactly `event_id`, `tenant`, `request_id`, `sku`, `qty`, `kind`,
`occurred_at`. IDs are unique increasing integers. `occurred_at` is the operation's
supplied now. Release events retain the reservation's original SKU and quantity.

`event_log(tenant)` returns all committed events for that tenant in ID order,
including events already drained. `drain_outbox(limit=100)` atomically claims up to
limit undelivered events globally in ID order and marks them delivered. Concurrent
drainers receive disjoint events; later drain calls never repeat an event. This
operation models transactional dequeue only: **exactly-once delivery to an external
network consumer is not claimed**, and no external system exists in this benchmark.

`failpoint`, when provided, is a callback receiving the string `after_state` once
per transition, after stock+reservation mutation but before event insertion, and
`after_event` after insertion but before commit. Callback exceptions propagate and
roll back the **entire operation**. The evaluator may terminate the process from
this callback (`os._exit`), bypassing cleanup. Reopening the database must recover
the pre-operation state and event log, with no partial persistence. Expire's entire
batch shares this guarantee. Idempotent/no-op paths do not invoke callbacks.

## Inspection

`snapshot(tenant)` is a transactionally consistent dictionary:

```json
{
  "inventory": {"sku": {"total": 8, "available": 5}},
  "reservations": {
    "request-id": {
      "tenant": "tenant-a", "request_id": "request-id", "sku": "sku",
      "qty": 3, "state": "reserved", "expires_at": 130
    }
  }
}
```

Only the requested tenant appears. Empty scopes return empty mappings. Persistent
state survives Engine close/reopen and a new interpreter process. Do not hardcode
public fixture names, quantities, times, concurrency counts or random seeds.
