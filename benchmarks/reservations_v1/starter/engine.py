"""Reservation service under repair. Public contract defines required behavior."""
from contextlib import contextmanager
import sqlite3


class Conflict(ValueError):
    pass


class OutOfStock(ValueError):
    pass


class Engine:
    def __init__(self, path, ttl=30):
        if not isinstance(ttl, int) or isinstance(ttl, bool) or ttl <= 0:
            raise ValueError('ttl must be a positive integer')
        self.ttl = ttl
        self.db = sqlite3.connect(str(path), timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA busy_timeout=30000')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS inventory (
                tenant TEXT NOT NULL, sku TEXT NOT NULL,
                total INTEGER NOT NULL CHECK(total>=0),
                available INTEGER NOT NULL CHECK(available>=0 AND available<=total),
                PRIMARY KEY(tenant,sku)
            );
            CREATE TABLE IF NOT EXISTS reservations (
                tenant TEXT NOT NULL, request_id TEXT NOT NULL, sku TEXT NOT NULL,
                qty INTEGER NOT NULL CHECK(qty>0),
                state TEXT NOT NULL CHECK(state IN ('reserved','cancelled','expired')),
                expires_at INTEGER NOT NULL,
                PRIMARY KEY(tenant,request_id),
                FOREIGN KEY(tenant,sku) REFERENCES inventory(tenant,sku)
            );
            CREATE TABLE IF NOT EXISTS outbox (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant TEXT NOT NULL, request_id TEXT NOT NULL, sku TEXT NOT NULL,
                qty INTEGER NOT NULL, kind TEXT NOT NULL, occurred_at INTEGER NOT NULL,
                delivered INTEGER NOT NULL DEFAULT 0,
                UNIQUE(tenant,request_id,kind),
                FOREIGN KEY(tenant,request_id) REFERENCES reservations(tenant,request_id)
            );
        ''')

    def close(self):
        self.db.close()

    @contextmanager
    def _transaction(self):
        self.db.execute('BEGIN')
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    @staticmethod
    def _text(value):
        if not isinstance(value, str) or not value:
            raise ValueError('nonempty text required')

    @staticmethod
    def _number(value, positive=False):
        if not isinstance(value, int) or isinstance(value, bool) or (positive and value <= 0):
            raise ValueError('integer required')

    def seed_stock(self, tenant, sku, total):
        self._text(tenant)
        self._text(sku)
        self._number(total)
        if total < 0:
            raise ValueError('negative stock')
        with self._transaction():
            old = self.db.execute('SELECT total FROM inventory WHERE tenant=? AND sku=?', (tenant, sku)).fetchone()
            if old:
                if old['total'] != total:
                    raise Conflict('stock already initialized differently')
            else:
                self.db.execute('INSERT INTO inventory VALUES(?,?,?,?)', (tenant, sku, total, total))

    def _before_event(self):
        self.db.commit()
        self.db.execute('BEGIN')

    @staticmethod
    def _inject(failpoint, stage):
        if failpoint is not None:
            failpoint(stage)

    def _event(self, row, kind, now):
        self.db.execute('INSERT INTO outbox(tenant,request_id,sku,qty,kind,occurred_at) VALUES(?,?,?,?,?,?)',
                        (row['tenant'], row['request_id'], row['sku'], row['qty'], kind, now))

    def reserve(self, tenant, request_id, sku, qty, now, failpoint=None):
        for item in (tenant, request_id, sku):
            self._text(item)
        self._number(qty, positive=True)
        self._number(now)
        with self._transaction():
            old = self.db.execute('SELECT * FROM reservations WHERE request_id=?', (request_id,)).fetchone()
            if old:
                return dict(old)
            stock = self.db.execute('SELECT available FROM inventory WHERE tenant=? AND sku=?', (tenant, sku)).fetchone()
            if not stock or stock['available'] < qty:
                raise OutOfStock('insufficient stock')
            self.db.execute('UPDATE inventory SET available=available-? WHERE tenant=? AND sku=?', (qty, tenant, sku))
            self.db.execute('INSERT INTO reservations VALUES(?,?,?,?,?,?)',
                            (tenant, request_id, sku, qty, 'reserved', now + self.ttl))
            row = self.db.execute('SELECT * FROM reservations WHERE tenant=? AND request_id=?', (tenant, request_id)).fetchone()
            self._before_event()
            self._inject(failpoint, 'after_state')
            self._event(row, 'reserved', now)
            self._inject(failpoint, 'after_event')
            return dict(row)

    def _release(self, row, kind, now, failpoint):
        self.db.execute('UPDATE reservations SET state=? WHERE tenant=? AND request_id=?',
                        (kind, row['tenant'], row['request_id']))
        self.db.execute('UPDATE inventory SET available=available+? WHERE tenant=? AND sku=?',
                        (row['qty'], row['tenant'], row['sku']))
        self._before_event()
        self._inject(failpoint, 'after_state')
        self._event(row, kind, now)
        self._inject(failpoint, 'after_event')

    def cancel(self, tenant, request_id, now, failpoint=None):
        self._text(tenant)
        self._text(request_id)
        self._number(now)
        with self._transaction():
            row = self.db.execute('SELECT * FROM reservations WHERE tenant=? AND request_id=?', (tenant, request_id)).fetchone()
            if row is None:
                raise LookupError('unknown reservation')
            if row['state'] == 'reserved':
                kind = 'expired' if now > row['expires_at'] else 'cancelled'
                self._release(row, kind, now, failpoint)
            return dict(self.db.execute('SELECT * FROM reservations WHERE tenant=? AND request_id=?', (tenant, request_id)).fetchone())

    def expire(self, now, failpoint=None):
        self._number(now)
        with self._transaction():
            rows = self.db.execute("SELECT * FROM reservations WHERE state='reserved' AND expires_at<? ORDER BY tenant,request_id", (now,)).fetchall()
            for row in rows:
                self._release(row, 'expired', now, failpoint)
            return len(rows)

    def snapshot(self, tenant):
        self._text(tenant)
        with self._transaction():
            inventory = {row['sku']: {'total': row['total'], 'available': row['available']} for row in
                         self.db.execute('SELECT * FROM inventory WHERE tenant=? ORDER BY sku', (tenant,))}
            reservations = {row['request_id']: dict(row) for row in
                            self.db.execute('SELECT * FROM reservations WHERE tenant=? ORDER BY request_id', (tenant,))}
            return {'inventory': inventory, 'reservations': reservations}

    def event_log(self, tenant):
        self._text(tenant)
        return [{key: row[key] for key in ('event_id','tenant','request_id','sku','qty','kind','occurred_at')} for row in
                self.db.execute('SELECT * FROM outbox WHERE tenant=? ORDER BY event_id', (tenant,))]

    def drain_outbox(self, limit=100):
        self._number(limit, positive=True)
        with self._transaction():
            rows = self.db.execute('SELECT * FROM outbox WHERE delivered=0 ORDER BY event_id LIMIT ?', (limit,)).fetchall()
            for row in rows:
                self.db.execute('UPDATE outbox SET delivered=1 WHERE event_id=?', (row['event_id'],))
            return [{key: row[key] for key in ('event_id','tenant','request_id','sku','qty','kind','occurred_at')} for row in rows]
