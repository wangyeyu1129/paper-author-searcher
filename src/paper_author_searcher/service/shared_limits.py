"""Limits every worker of a deployment keeps together, in one SQLite file on the shared volume.

Each search paces its requests on its own, so ``--scale worker=N`` would otherwise send N times the traffic to every
source. Here the next free request slot per host and the day's spend per host live in the database: a worker reserves
a slot in a transaction and sleeps outside it, on one host or several sharing the volume.

These are limits on traffic, not part of a search's meaning: if the database cannot be reached the request goes ahead.
"""

import logging
import sqlite3
import time
from collections.abc import Callable, Hashable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from paper_author_searcher.http_budget import BudgetExceeded

logger = logging.getLogger(__name__)

BUSY_TIMEOUT_SECONDS = 5.0


class SharedRequestPacer:
    """A ``Pacer`` whose slots are visible to every process using the same ``SqliteSharedLimits``."""

    def __init__(self, limits: 'SqliteSharedLimits', interval: float) -> None:
        self.limits = limits
        self.interval = interval

    def wait(self, key: Hashable | None = None) -> float:
        delay = self.limits.reserve_slot(str(key), self.interval)
        if delay > 0:
            self.limits.sleep(delay)
        return delay

    def hold(self, key: Hashable | None, seconds: float) -> None:
        self.limits.hold(str(key), seconds)


class SqliteSharedLimits:
    def __init__(
        self,
        path: str | Path,
        maximum_daily_cost_usd: float,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.path = Path(path)
        self.maximum_daily_cost_usd = maximum_daily_cost_usd
        self.clock = clock
        self.sleep = sleep
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None)
        try:
            connection.execute('PRAGMA journal_mode=WAL')
        finally:
            connection.close()
        with self._transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS slots (host TEXT PRIMARY KEY, next_slot REAL NOT NULL)')
            db.execute(
                'CREATE TABLE IF NOT EXISTS daily_spend (day TEXT NOT NULL, host TEXT NOT NULL, usd REAL NOT NULL,'
                ' PRIMARY KEY (day, host))'
            )

    def pacer(self, interval: float) -> SharedRequestPacer:
        return SharedRequestPacer(self, interval)

    def reserve_slot(self, host: str, interval: float) -> float:
        """The seconds this caller must wait for its turn at ``host``; the next caller gets the slot after it."""
        try:
            with self._transaction() as db:
                moment = self.clock()
                row = db.execute('SELECT next_slot FROM slots WHERE host = ?', (host,)).fetchone()
                slot = max(moment, row[0]) if row else moment
                self._set_slot(db, host, slot + interval)
        except sqlite3.Error as error:
            logger.warning('Shared pacing unavailable (%s); request not held back', error)
            return 0.0
        return slot - moment

    def hold(self, host: str, seconds: float) -> None:
        try:
            with self._transaction() as db:
                until = self.clock() + seconds
                row = db.execute('SELECT next_slot FROM slots WHERE host = ?', (host,)).fetchone()
                self._set_slot(db, host, max(until, row[0]) if row else until)
        except sqlite3.Error as error:
            logger.warning('Shared pacing unavailable (%s); host not held back', error)

    def charge(self, host: str, cost: float) -> None:
        """Count ``cost`` against the host's spend today, or raise BudgetExceeded without counting it."""
        day = datetime.fromtimestamp(self.clock(), timezone.utc).date().isoformat()
        try:
            with self._transaction() as db:
                row = db.execute('SELECT usd FROM daily_spend WHERE day = ? AND host = ?', (day, host)).fetchone()
                spent = row[0] if row else 0.0
                if spent + cost > self.maximum_daily_cost_usd + 1e-12:
                    raise BudgetExceeded(
                        f'Daily budget of ${self.maximum_daily_cost_usd:.2f} for {host} reached; request not sent'
                    )
                db.execute(
                    'INSERT INTO daily_spend (day, host, usd) VALUES (?, ?, ?)'
                    ' ON CONFLICT (day, host) DO UPDATE SET usd = excluded.usd',
                    (day, host, spent + cost),
                )
                db.execute('DELETE FROM daily_spend WHERE day < ?', (day,))
        except sqlite3.Error as error:
            logger.warning('Shared budget unavailable (%s); request not counted', error)

    def spent_today(self, host: str) -> float:
        day = datetime.fromtimestamp(self.clock(), timezone.utc).date().isoformat()
        with self._transaction() as db:
            row = db.execute('SELECT usd FROM daily_spend WHERE day = ? AND host = ?', (day, host)).fetchone()
        return row[0] if row else 0.0

    @staticmethod
    def _set_slot(db: sqlite3.Connection, host: str, slot: float) -> None:
        db.execute(
            'INSERT INTO slots (host, next_slot) VALUES (?, ?)'
            ' ON CONFLICT (host) DO UPDATE SET next_slot = excluded.next_slot',
            (host, slot),
        )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None)
        try:
            db.execute('BEGIN IMMEDIATE')
            try:
                yield db
            except BaseException:
                db.execute('ROLLBACK')
                raise
            db.execute('COMMIT')
        finally:
            db.close()
