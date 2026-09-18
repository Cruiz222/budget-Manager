"""The hot layer: counters that live in this process and answer in the same breath.

**Why a lock, and why it is not decoration.** Every endpoint in this API is a plain
``def``, so FastAPI runs it in its threadpool - which is the correct place for
blocking SQLite work and means several requests are in this object at once. The
whole job of ``check_and_increment`` is that a *check* and an *update* happen as one
step, and ``claude.md`` names that rule directly: never assume a check followed by
an update is atomic. Here it is atomic because one ``threading.Lock`` spans the read,
the comparison and the increment, and there is no other way in.

**Every increment is kept, including the ones that are refused.** This is the same
position ``app/application/wallet_operation.py`` takes when it records a refused
attempt as a ``FAILED`` row rather than discarding it: a limiter that returned its
own count on refusal would let a caller try forever at exactly the limit's rate, so
the limit would bound nothing at all. The count is spent by the attempt, not by the
success.

**Fixed windows, and the weakness is named rather than hidden.** An entry carries
the instant its window opened; a check at or past ``window`` later opens a new one.
The known cost is that a caller can spend a full budget at the end of one window and
another at the start of the next, so the worst case across a boundary is twice the
policy limit rather than the policy limit. That is accepted here because the
installation ceiling is enforced on a window of its own, and because the upgrade is
a known one - a sliding window counter keeping the previous window's count and
weighting it by how far into the current one the caller is - rather than a redesign.
It is written down so that the number in the policy table means what it says: a rate,
not a ceiling on a burst.

**A lapsed window's unflushed counts are dropped on rollover, deliberately.** The
cold layer exists for two reasons - so a restart does not hand out a fresh budget,
and so a second worker can see this one's counts - and a window that has already
ended serves neither. Persisting it would mean the table held rows nothing would
ever read. What is lost on rollover is therefore at most one flush interval of a
window nobody can be limited by again, and what is *not* lost is the current window,
which is the one that matters.

**``now`` is a parameter rather than a call to ``datetime.now()`` inside these
methods**, and that is what makes the window testable without sleeping. It is the
same seam ``tests/presentation/api/conftest.py``'s ``legacy_account`` already uses
for ``created_at``: the caller holds the clock, so a test can move it.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class Limit:
    """One budget: how many calls, over what window.

    ``calls`` is the number of attempts *permitted*, so a limit of ``3`` allows the
    third call and refuses the fourth. Stated because the off-by-one is the whole
    difference between a limiter and an outage, and because a policy table read
    quickly cannot tell which convention a bare number means.
    """

    calls: int
    window: timedelta

    def __post_init__(self) -> None:
        if self.calls < 1:
            raise ValueError("a limit of zero calls is a route that is switched off")
        if self.window <= timedelta(0):
            raise ValueError("a window must have a positive length")


@dataclass(frozen=True)
class Verdict:
    """What one check decided.

    ``retry_after`` is meaningful only when ``allowed`` is false, and it is the time
    left in the window that refused - which is exactly what ``Retry-After`` is for.
    A caller told to wait is being told something true rather than something round.
    """

    allowed: bool
    retry_after: timedelta

    @property
    def retry_after_seconds(self) -> int:
        """``retry_after`` as the whole seconds an HTTP header carries.

        Rounded **up**, and never below one. Rounding down would let a client that
        obeys the header retry a fraction of a second early and be refused again -
        a header whose advice does not work - and a ``Retry-After: 0`` invites an
        immediate retry, which is the opposite of what a limiter is for.
        """
        return max(1, math.ceil(self.retry_after.total_seconds()))


@dataclass(frozen=True)
class Pending:
    """One key's unwritten increments, as the cold layer should record them.

    ``delta`` rather than a total, and the difference is the whole reason the cold
    layer works with more than one process. Two workers each flushing their *totals*
    would overwrite each other and the stored count would be one worker's rather
    than both; two workers flushing their *deltas* sum, and a sum is the truth.
    """

    bucket: str
    subject: str
    window_started_at: datetime
    delta: int


class _Entry:
    """One key's counter, in the window it belongs to.

    ``flushed_count`` is how much of ``count`` the cold layer has already been told
    about. It is not a second count and never answers a question about the limit -
    it exists only so that ``pending`` can compute a delta.
    """

    __slots__ = ("window_started_at", "count", "flushed_count")

    def __init__(self, window_started_at: datetime) -> None:
        self.window_started_at = window_started_at
        self.count = 0
        self.flushed_count = 0


class InMemoryRateCounter:
    """Counters for every bucket and subject this process has seen recently.

    Keys are ``(bucket, subject)``, where a bucket names the limit being applied and
    a subject names who or what it is being applied to. Nothing in this class knows
    what either string means - which is what lets one mechanism serve a per-address
    limit and an installation-wide ceiling without a second code path.
    """

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], _Entry] = {}
        self._lock = threading.Lock()

    def check_and_increment(
        self, bucket: str, subject: str, limit: Limit, now: datetime
    ) -> Verdict:
        """Spend one of ``limit``'s calls, and say whether it was there to spend.

        The read, the comparison and the increment are one critical section. The
        increment happens on **both** arms - see the module docstring for why a
        refused attempt still costs.
        """
        key = (bucket, subject)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None or now - entry.window_started_at >= limit.window:
                # A new window, and the old one's unwritten delta goes with it. See
                # the module docstring: the window it belonged to is over, so no
                # flush will ever be asked for it again.
                entry = _Entry(now)
                self._entries[key] = entry

            entry.count += 1
            remaining_window = entry.window_started_at + limit.window - now
            if entry.count > limit.calls:
                return Verdict(allowed=False, retry_after=remaining_window)
            return Verdict(allowed=True, retry_after=timedelta(0))

    def pending(self) -> list[Pending]:
        """Every key's increments that the cold layer has not been told about.

        Read-only, and deliberately *not* paired with a ``mark`` in one call. The
        cold write happens between this and ``mark_flushed``, so a write that fails
        leaves the deltas unmarked and the next flush offers them again - losing
        nothing. A single drain-and-mark would have discarded increments whose
        insert had failed.
        """
        with self._lock:
            return [
                Pending(
                    bucket=bucket,
                    subject=subject,
                    window_started_at=entry.window_started_at,
                    delta=entry.count - entry.flushed_count,
                )
                for (bucket, subject), entry in self._entries.items()
                if entry.count > entry.flushed_count
            ]

    def mark_flushed(self, written: list[Pending]) -> None:
        """Record that the cold layer has taken these increments.

        Advances ``flushed_count`` by the delta that was written rather than to the
        current ``count``, so an increment that arrived while the write was in
        flight is offered again next time instead of being silently marked as done.
        """
        with self._lock:
            for item in written:
                entry = self._entries.get((item.bucket, item.subject))
                if entry is None:
                    # The key was swept between the read and the write. Nothing to
                    # mark, and nothing lost: a swept key was fully flushed.
                    continue
                if entry.window_started_at != item.window_started_at:
                    # The window rolled while the write was in flight, so these
                    # increments belong to a window this process has finished with.
                    # Marking them now would charge the new window for the old
                    # one's calls.
                    continue
                entry.flushed_count += item.delta

    def seed(self, rows: list[Pending]) -> None:
        """Adopt counts the cold layer already holds, at startup.

        Both counters are set from the row. ``flushed_count`` starts equal to
        ``count`` because the cold layer is where these came from - offering them
        back on the first flush would add a dead process's calls to this one's.

        A row whose window has already lapsed is skipped rather than adopted: it
        describes a window nobody can be limited by, and adopting it would refuse a
        caller for calls made before the last restart.

        **A seeded count can exceed the limit**, and that is the intended reading
        rather than a bug: a process that restarts after its callers exhausted a
        budget must not hand the budget back. ``check_and_increment`` compares
        ``count > calls``, so an over-limit entry refuses until its window rolls.
        """
        with self._lock:
            for row in rows:
                if row.delta <= 0:
                    continue
                entry = _Entry(row.window_started_at)
                entry.count = row.delta
                entry.flushed_count = row.delta
                self._entries[(row.bucket, row.subject)] = entry

    def forget_lapsed(self, now: datetime, longest: timedelta) -> int:
        """Drop keys whose window ended long enough ago that nothing will ask again.

        Without this the dict grows with every subject ever seen, which for a
        subject-keyed limiter is one entry per address anybody has ever tried - a
        slow leak dressed as a cache. ``longest`` is the longest window any policy
        uses, so a key older than that cannot belong to a live window.

        Only fully-flushed keys are dropped. An unflushed one is left for ``pending``
        to pick up, because dropping it would lose counts the cold layer never got -
        and the cost of waiting is one more interval of a dict entry.
        """
        with self._lock:
            lapsed = [
                key
                for key, entry in self._entries.items()
                if now - entry.window_started_at >= longest
                and entry.count == entry.flushed_count
            ]
            for key in lapsed:
                del self._entries[key]
            return len(lapsed)

    def __len__(self) -> int:
        """How many keys this process is currently holding. For tests and operators."""
        with self._lock:
            return len(self._entries)
