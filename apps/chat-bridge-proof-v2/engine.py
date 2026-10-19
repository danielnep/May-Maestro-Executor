import sqlite3
import threading
import uuid
from contextlib import contextmanager

class ReservationEngine:
    def __init__(self, db_path=":memory:"):
        self.db_path = db_path
        self._local = threading.local()
        self.lock = threading.Lock()
        self._init_db()

    def _get_conn(self):
        if not hasattr(self._local, "conn"):
            self._local.conn = sqlite3.connect(self.db_path, isolation_level=None)
            self._local.conn.execute("PRAGMA journal_mode=WAL;")
            self._init_db_conn(self._local.conn)
        return self._local.conn

    def _init_db(self):
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        self._init_db_conn(conn)
        conn.close()

    def _init_db_conn(self, conn):
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS resources (
                resource_id TEXT PRIMARY KEY,
                capacity INTEGER NOT NULL,
                available INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reservations (
                reservation_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                UNIQUE(tenant_id, idempotency_key)
            );
        """)

    @contextmanager
    def transaction(self):
        conn = self._get_conn()
        conn.execute("BEGIN IMMEDIATE;")
        try:
            yield conn
            conn.execute("COMMIT;")
        except Exception:
            conn.execute(
                "ROLLBACK;" if conn.in_transaction else ""
            )
            raise

    def add_resource(self, resource_id: str, capacity: int):
        with self.lock, self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT capacity, available FROM resources WHERE resource_id = ?;", (resource_id,))
            row = cursor.fetchone()
            if row:
                old_cap, old_avail = row
                diff = capacity - old_cap
                new_avail = max(0, old_avail + diff)
                conn.execute(
                    "UPDATE resources SET capacity = ?, available = ? WHERE resource_id = ?;",
                    (capacity, new_avail, resource_id)
                )
            else:
                conn.execute(
                    "INSERT INTO resources (resource_id, capacity, available) VALUES (?, ?, ?);",
                    (resource_id, capacity, capacity)
                )

    def reserve(self, tenant_id: str, resource_id: str, idempotency_key: str, payload_hash: str = "default") -> dict:
        with self.lock, self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT reservation_id, payload_hash, status FROM reservations WHERE tenant_id = ? AND idempotency_key = ?;",
                (tenant_id, idempotency_key)
            )
            row = cursor.fetchone()
            if row:
                res_id, stored_hash, status = row
                if stored_hash != payload_hash:
                    raise ValueError("Idempotency conflict: payload hash mismatch")
                return {"reservation_id": res_id, "status": status, "replayed": True}

            cursor.execute("SELECT available FROM resources WHERE resource_id = ?;", (resource_id,))
            res_row = cursor.fetchone()
            if not res_row:
                raise ValueError("Resource not found")
            
            available = res_row[0]
            if available <= 0:
                raise ValueError("Capacity exhausted")

            conn.execute(
                "UPDATE resources SET available = available - 1 WHERE resource_id = ?;",
                (resource_id,)
            )

            reservation_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO reservations (reservation_id, tenant_id, resource_id, idempotency_key, payload_hash, status) VALUES (?, ?, ?, ?, ?, ?);",
                (reservation_id, tenant_id, resource_id, idempotency_key, payload_hash, "CONFIRMED")
            )
            return {"reservation_id": reservation_id, "status": "CONFIRMED", "replayed": False}

    def cancel(self, tenant_id: str, reservation_id: str) -> dict:
        with self.lock, self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT resource_id, status FROM reservations WHERE tenant_id = ? AND reservation_id = ?;",
                (tenant_id, reservation_id)
            )
            row = cursor.fetchone()
            if not row:
                raise ValueError("Reservation not found")

            resource_id, status = row
            if status == "CANCELLED":
                return {"reservation_id": reservation_id, "status": "CANCELLED", "already_cancelled": True}

            conn.execute(
                "UPDATE reservations SET status = 'CANCELLED' WHERE tenant_id = ? AND reservation_id = ?;",
                (tenant_id, reservation_id)
            )
            conn.execute(
                "UPDATE resources SET available = available + 1 WHERE resource_id = ?;",
                (resource_id,)
            )
            return {"reservation_id": reservation_id, "status": "CANCELLED", "already_cancelled": False}
