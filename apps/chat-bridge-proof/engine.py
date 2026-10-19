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
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_db(self):
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS resources (
                    id TEXT PRIMARY KEY,
                    capacity INTEGER NOT NULL,
                    available INTEGER NOT NULL
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS reservations (
                    id TEXT PRIMARY KEY,
                    resource_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    UNIQUE(tenant_id, idempotency_key)
                )
            """)
            conn.commit()
            conn.close()

    @contextmanager
    def transaction(self):
        conn = self._get_conn()
        # BEGIN IMMEDIATE prevents busy deadlocks in SQLite under concurrency
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def create_resource(self, resource_id: str, capacity: int):
        with self.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO resources (id, capacity, available) VALUES (?, ?, ?)",
                (resource_id, capacity, capacity)
            )

    def reserve(self, tenant_id: str, resource_id: str, idempotency_key: str, payload_sig: str = "default"):
        with self.transaction() as conn:
            cursor = conn.cursor()
            # Check idempotency
            cursor.execute(
                "SELECT id, status, payload_hash FROM reservations WHERE tenant_id = ? AND idempotency_key = ?",
                (tenant_id, idempotency_key)
            )
            row = cursor.fetchone()
            if row:
                if row["payload_hash"] != payload_sig:
                    raise ValueError("Conflict: Idempotency key reused with different payload")
                return row["id"], row["status"]

            # Check resource capacity
            cursor.execute("SELECT capacity, available FROM resources WHERE id = ?", (resource_id,))
            res = cursor.fetchone()
            if not res:
                raise ValueError("Resource not found")
            
            if res["available"] <= 0:
                raise ValueError("Capacity exhausted")

            # Decrement available
            conn.execute(
                "UPDATE resources SET available = available - 1 WHERE id = ?",
                (resource_id,)
            )

            reservation_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO reservations (id, resource_id, tenant_id, idempotency_key, status, payload_hash) VALUES (?, ?, ?, ?, ?, ?)",
                (reservation_id, resource_id, tenant_id, idempotency_key, "confirmed", payload_sig)
            )
            return reservation_id, "confirmed"

    def cancel(self, tenant_id: str, reservation_id: str):
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT resource_id, status FROM reservations WHERE id = ? AND tenant_id = ?",
                (reservation_id, tenant_id)
            )
            row = cursor.fetchone()
            if not row:
                raise ValueError("Reservation not found or unauthorized")
            
            if row["status"] == "cancelled":
                # Already cancelled, idempotent return without double refund
                return "cancelled"

            resource_id = row["resource_id"]
            conn.execute(
                "UPDATE reservations SET status = 'cancelled' WHERE id = ?",
                (reservation_id,)
            )
            conn.execute(
                "UPDATE resources SET available = available + 1 WHERE id = ?",
                (resource_id,)
            )
            return "cancelled"
