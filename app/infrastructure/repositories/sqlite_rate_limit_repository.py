"""The cold layer: rate-limit counters that outlive the process that counted them.

**This is the one repository in the codebase with no port above it**, and the
absence is deliberate rather than an oversight. Every other repository here
implements an interface in ``app/domain/repositories`` because the domain has an
opinion about the thing being stored - a wallet's invariants, a session's window, a
request's lifecycle. The domain has no opinion about how often a caller knocked on a
door: nothing in ``app/domain`` or ``app/application`` reads or writes this table,
and no use case knows it exists. A port with no domain consumer would be an
interface invented to match a shape rather than to serve a need, so this class is
concrete and sits where its only caller can see it.

**It participates in a transaction like every other repository**: it takes a
connection, issues SQL, and never commits. The flusher owns the unit of work, which
matters more here than usual - see ``TwoLayerRateLimiter.flush`` for why the sweep
and the writes have to land together.

**Liveness is decided in Python rather than in SQL, and that is a considered choice.**
The obvious query is ``WHERE window_started_at > ?``, and it would be a bug waiting
for a quiet moment: ``datetime_to_text`` is ``isoformat()``, which *omits the
fractional part when it is zero* - so the column holds both ``"…T12:00:00"`` and
``"…T12:00:00.123456"`` for different rows. Lexicographic comparison of those two
spellings happens to give the right answer, because a prefix sorts before the string
that extends it and the digits sort numerically, but "happens to be right" is not
something to build a security control on. Reading the rows and comparing parsed
``datetime``s costs a scan of a table that holds one row per recently-seen subject,
and removes the question entirely.
"""

import sqlite3
from datetime import datetime, timedelta

from app.infrastructure.persistence.serialization import (
    datetime_to_text,
    text_to_datetime,
)
from app.infrastructure.rate_limiting.in_memory_counter import Pending

_COLUMNS = "bucket, subject, window_started_at, count"

#: The upsert that makes the stored number a *sum of deltas* rather than one
#: process's total. The ``CASE`` is the half that matters and the half that is easy
#: to leave out: when the incoming row belongs to the same window the stored one
#: does, the deltas add; when it belongs to a *new* window, the stored count is
#: replaced rather than added to, because the old window's calls are over and must
#: not be charged against the new one.
#:
#: SQLite evaluates every right-hand side against the row as it was *before* the
#: update, so ``rate_limits.window_started_at`` in the ``CASE`` is genuinely the old
#: window even though the assignment below it overwrites that column. That is the
#: reason the two clauses can be written in this order and still be correct.
_ADD_DELTAS = """
    INSERT INTO rate_limits (bucket, subject, window_started_at, count)
    VALUES (?, ?, ?, ?)
    ON CONFLICT(bucket, subject) DO UPDATE SET
        count = CASE
            WHEN rate_limits.window_started_at = excluded.window_started_at
            THEN rate_limits.count + excluded.count
            ELSE excluded.count
        END,
        window_started_at = excluded.window_started_at
"""


class SqliteRateLimitRepository:
    """Counter storage over a single SQLite connection."""

    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection
        self._connection.row_factory = sqlite3.Row

    def live(self, now: datetime, longest: timedelta) -> list[Pending]:
        """Every row whose window could still refuse somebody.

        Returned as ``Pending`` - the hot layer's own shape for "increments the cold
        layer should hold" - because that is exactly what these are. Reusing the type
        rather than declaring a parallel one is what keeps ``seed`` and ``add`` a
        round trip rather than a translation: what ``add`` writes is what ``live``
        reads, and a second type could disagree with the first.

        ``delta`` carries the stored ``count``, and the name is momentarily wrong on
        this side of the boundary - a row read back is a total, not a delta. That is
        the one thing ``seed`` normalises, and it is why ``seed`` sets both of an
        entry's counters from it.
        """
        cutoff = now - longest
        return [
            Pending(
                bucket=row["bucket"],
                subject=row["subject"],
                window_started_at=text_to_datetime(row["window_started_at"]),
                delta=row["count"],
            )
            for row in self._connection.execute(f"SELECT {_COLUMNS} FROM rate_limits")
            if text_to_datetime(row["window_started_at"]) > cutoff
        ]

    def add(self, pending: list[Pending]) -> None:
        """Add these increments to whatever the store already holds.

        Idempotent in the sense that matters for a retry: the caller writes deltas it
        has not written before, so a second call with the same list would double the
        count. That is why ``InMemoryRateCounter.mark_flushed`` is called only after
        this returns - see ``TwoLayerRateLimiter.flush``.
        """
        self._connection.executemany(
            _ADD_DELTAS,
            [
                (
                    item.bucket,
                    item.subject,
                    datetime_to_text(item.window_started_at),
                    item.delta,
                )
                for item in pending
            ],
        )

    def forget_lapsed(self, now: datetime, longest: timedelta) -> int:
        """Delete rows whose window ended long enough ago that nothing will ask again.

        Returns how many were removed, so the flusher can report a number rather than
        a hope. Without this the table keeps one row per subject ever seen, which for
        a limiter keyed on caller-supplied identifiers is unbounded - and an unbounded
        table is a disk that fills rather than a limit that holds.

        Deleted by key rather than by a ``WHERE window_started_at <`` clause, for the
        reason the module docstring gives about comparing these strings. The rows were
        already read by ``live``, so the keys are in hand and the comparison has
        already been made properly.
        """
        cutoff = now - longest
        lapsed = [
            (row["bucket"], row["subject"])
            for row in self._connection.execute(f"SELECT {_COLUMNS} FROM rate_limits")
            if text_to_datetime(row["window_started_at"]) <= cutoff
        ]
        if lapsed:
            self._connection.executemany(
                "DELETE FROM rate_limits WHERE bucket = ? AND subject = ?", lapsed
            )
        return len(lapsed)
