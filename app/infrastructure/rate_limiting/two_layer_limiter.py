"""Composing the two layers: a fast answer now, a durable one later.

**The property worth stating first is that the hot layer never waits on the cold
one.** Every limit decision is made from the dict in this process, so a database
that is slow, locked or gone does not stop the limiter limiting - it stops the
counts *persisting*, which is the smaller failure and the one that degrades in the
safe direction. A request is never refused because a flush failed, and a request is
never admitted because a flush failed either; the answer comes from memory either
way. What is lost when the cold layer is unhealthy is durability across a restart.

**The honest limits of what the two layers together guarantee.** They are worth
writing down precisely, because "the limit is 10" is a claim a reader will make
whether or not it is true.

- *One worker*: the limit is the limit, exactly. A restart does not hand the budget
  back, because ``warm`` adopts what the last process flushed.
- *Several workers*: each holds its own hot counter, so the effective installation
  limit is up to N times the policy limit, and it stays that way between flushes -
  a flush makes the cold layer agree, it does not retroactively refuse anything. The
  stored count converges on the true total, and there is no mechanism by which one
  worker's refusal is seen by another. A deployment that needs the exact number has
  to route a subject to one worker or move the hot layer to something shared; the
  README's Phase 5 names Redis for exactly this.
- *An unclean stop*: at most one flush interval of the current window is lost,
  because those increments were only ever in memory.
- *A clean stop*: nothing is lost. ``BackgroundFlusher.stop`` flushes once more
  before the thread ends.

**Over-counting is the safe direction and under-counting is not**, and the design is
arranged so that failures land on the safe side. A crash between a successful commit
and ``mark_flushed`` leaves increments unmarked, so they are written a second time
and the stored count is too high - which refuses more than the policy says, and is
recoverable by waiting out one window. Nothing in this module can produce a stored
count that is too low while the hot layer is intact.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.infrastructure.rate_limiting.in_memory_counter import (
    InMemoryRateCounter,
    Limit,
    Verdict,
)

#: How long past the end of its window a stored row is worth keeping. The caller
#: passes the longest window any policy uses, so that a row which could still
#: refuse somebody is never swept - see ``TwoLayerRateLimiter.__init__``.
DEFAULT_RETENTION = timedelta(hours=1)


@dataclass(frozen=True)
class FlushReport:
    """What one flush did, as numbers rather than as a hope.

    Returned rather than logged, because there is no logging in ``app/`` yet - the
    MVP list carries that as its own item. Until there is, this is the value an
    operator or a test can assert on, and ``BackgroundFlusher.last_report`` is where
    the most recent one is kept.
    """

    written: int
    swept: int


class TwoLayerRateLimiter:
    """The hot counter, plus the store it is warmed from and flushed to."""

    def __init__(
        self,
        counter: InMemoryRateCounter,
        unit_of_work_factory,
        retention: timedelta = DEFAULT_RETENTION,
    ) -> None:
        """``retention`` is how long a *lapsed* window's row is kept.

        It wants to be at least the longest window any policy uses, and passing less
        would be a real bug rather than a tuning choice: a row swept while its window
        was still live would hand a caller a fresh budget mid-window, which is the
        one thing the cold layer exists to prevent. ``app/presentation/api/rate_limits.py``
        computes it from the policy table rather than choosing a number, so the two
        cannot drift apart.
        """
        self._counter = counter
        self._factory = unit_of_work_factory
        self._retention = retention

    def check(
        self, bucket: str, subject: str, limit: Limit, now: datetime | None = None
    ) -> Verdict:
        """Spend one of ``limit``'s calls for this subject, and answer.

        No I/O and no lock beyond the counter's own, so this is safe to call on the
        request path in front of work that costs money. ``now`` defaults to the wall
        clock and is a parameter so that a test can move time rather than wait.
        """
        return self._counter.check_and_increment(
            bucket, subject, limit, datetime.now() if now is None else now
        )

    def warm(self, now: datetime | None = None) -> int:
        """Adopt the counts the last process flushed. Returns how many keys it took.

        Called once, at startup, before the application serves anything - so a
        restart is not a way to get a fresh budget. Read inside a unit of work like
        every other database access in this codebase, even though it only reads.
        """
        moment = datetime.now() if now is None else now
        uow = self._factory.start()
        try:
            rows = uow.rate_limits.live(moment, self._retention)
        finally:
            uow.rollback()
        self._counter.seed(rows)
        return len(rows)

    def flush(self, now: datetime | None = None) -> FlushReport:
        """Write this process's unwritten increments, and sweep what has lapsed.

        **The sweep and the writes share one transaction, and that is not tidiness.**
        A sweep that committed separately could delete a row between this process
        reading it and writing to it, and the write would then resurrect a count from
        a window that had just been swept - with the original increment gone, so the
        number would be this process's delta presented as a total. One unit of work
        makes the pair atomic.

        ``mark_flushed`` is called only after the commit returns, which is what makes
        a failed write lose nothing: the deltas stay unmarked and the next flush
        offers them again. The window between a successful commit and the mark is the
        one place a crash costs accuracy, and it costs it by counting twice - the
        safe direction, per the module docstring.
        """
        moment = datetime.now() if now is None else now
        pending = self._counter.pending()

        uow = self._factory.start()
        try:
            if pending:
                uow.rate_limits.add(pending)
            swept = uow.rate_limits.forget_lapsed(moment, self._retention)
            uow.commit()
        finally:
            uow.rollback()

        if pending:
            self._counter.mark_flushed(pending)
        self._counter.forget_lapsed(moment, self._retention)
        return FlushReport(written=len(pending), swept=swept)

    def held_keys(self) -> int:
        """How many keys the hot layer is holding. For tests and operators."""
        return len(self._counter)


class BackgroundFlusher:
    """A daemon thread that calls ``flush`` on an interval.

    **This is the first background thread in ``app/``, and it is deliberately small
    and deliberately optional.** A deployment that sets the interval to zero gets an
    application with no thread at all, which is what the HTTP test suite does: a
    thread per test application would be hundreds of them, each with its own timer,
    costing wall clock to prove something the cold layer's own tests prove directly
    by calling ``flush``.

    ``stop`` flushes once more before the thread ends, so a clean shutdown loses
    nothing. That final flush is also what makes a restart's warm start adopt the
    counts from the process that just stopped rather than from the one before it.
    """

    def __init__(self, limiter: TwoLayerRateLimiter, interval_seconds: float) -> None:
        if interval_seconds <= 0:
            raise ValueError("an interval of zero is no flusher; do not build one")
        self._limiter = limiter
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        #: The last exception a flush raised, or ``None``. **Read this if limits look
        #: wrong**, because nothing else reports it - there is no logging in ``app/``
        #: yet, and that is the MVP list's own item rather than an oversight here.
        self.last_error: BaseException | None = None
        #: The most recent successful report, or ``None`` before the first flush.
        self.last_report: FlushReport | None = None

    def start(self) -> None:
        """Start the thread. Idempotent, so a lifespan cannot double-start it."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="rate-limit-flusher", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the thread to finish, flush once more, and wait for it.

        The final flush happens on **this** thread rather than the departing one, so
        that a caller which has returned from ``stop`` knows the counts are stored.
        Waiting on the thread to do it would make the guarantee depend on scheduling.
        """
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
            self._thread = None
        try:
            self.last_report = self._limiter.flush()
        except Exception as exc:  # a failing final flush must not break shutdown
            self.last_error = exc

    def _run(self) -> None:
        """Flush until told to stop.

        **A failing flush must not end the loop.** A limiter whose counters stopped
        persisting because of one locked database would be a security control that
        quietly turned itself off, and the whole point of the hot layer is that
        limiting carries on regardless. So the exception is kept and the loop
        continues; the limits keep working from memory, and only durability is lost.
        """
        while not self._stop.wait(self._interval):
            try:
                self.last_report = self._limiter.flush()
                self.last_error = None
            except Exception as exc:  # see the method docstring: the loop carries on
                self.last_error = exc
