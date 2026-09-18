"""Composing the layers: what survives a restart, and what a second worker does.

**These tests call ``flush`` and ``warm`` directly rather than waiting for a
thread.** That is the whole reason the two are public methods and the thread is a
separate object: what the cold layer does is a fact about a database, and it can be
established in milliseconds by asking rather than in thirty seconds by waiting.
``BackgroundFlusher`` gets its own tests at the bottom, where the subject really is
the thread.

The one claim worth stating before the tests is the one they are arranged around:
**a delta flush is what makes more than one process correct.** Two workers each
writing their totals would overwrite each other and the stored number would be one
worker's rather than both; two workers writing their deltas sum, and a sum is the
truth.
"""

import sqlite3
import time
from datetime import datetime, timedelta

import pytest

from app.infrastructure.persistence.serialization import text_to_datetime
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)
from app.infrastructure.rate_limiting import (
    BackgroundFlusher,
    InMemoryRateCounter,
    Limit,
    TwoLayerRateLimiter,
)

#: The moment every window in this file opens at. Fixed, so nothing here depends on
#: when the suite ran - ``test_in_memory_counter.py``'s reason for ``NOON``.
#:
#: The thread tests at the bottom deliberately do **not** use it. ``flush`` with no
#: ``now`` reads the wall clock, which is what a background thread must do, so a test
#: that pinned a window at ``NOON`` and then let a thread sweep against the real clock
#: would be asserting something about what time the suite ran - and would delete its
#: own row any afternoon after one o'clock.
NOON = datetime(2026, 9, 17, 12, 0, 0)

HOUR = timedelta(hours=1)

LIMIT = Limit(calls=100, window=HOUR)


@pytest.fixture
def db_path(tmp_path):
    """A database file for one test.

    Declared here rather than imported from ``tests/presentation/api/conftest.py``,
    where the same fixture exists: a fixture is only shared through a conftest above
    the file that needs it, and the API suite's conftest is scoped to that directory.
    Reaching across would mean importing a module whose fixtures belong to a different
    presentation, which is a coupling that would have to be unpicked the day those
    fixtures move.
    """
    return str(tmp_path / "budget.db")


@pytest.fixture
def factory(db_path):
    """A factory over a database that has the schema and no rows in it.

    **The schema is applied here rather than left to the first flush, and that is a
    fix for a real failure rather than tidiness.** ``SqliteUnitOfWorkFactory.start``
    is what calls ``open_sqlite_connection``, so nothing exists on disk until some
    test opens a unit of work - and ``stored`` reads the table *directly*, so on the
    first poll of a test that has not written yet it was answered with ``no such
    table: rate_limits`` rather than with ``{}``. ``test_a_flusher_writes_without_being_asked``
    is the one that noticed, because it polls before the background thread's first
    interval has elapsed, but the predicate was wrong for every test in the file.

    Opening one unit and rolling it back makes the precondition true up front. It
    also keeps the assertion honest: if the table were genuinely missing from
    ``SCHEMA``, the flush itself would raise and every test here would still fail -
    just with the error naming the cause rather than the symptom.
    """
    factory = SqliteUnitOfWorkFactory(db_path)
    factory.start().rollback()
    return factory


@pytest.fixture
def limiter(factory):
    return TwoLayerRateLimiter(InMemoryRateCounter(), factory, retention=HOUR)


def stored(factory) -> dict[tuple[str, str], tuple[datetime, int]]:
    """Every row the cold layer holds, as ``{(bucket, subject): (moment, count)}``.

    **The table is read directly rather than through ``rate_limits.live``**, and the
    difference decides what these tests can assert. ``live`` answers "which rows could
    still refuse somebody", which is a question about the *current* moment; a sweep
    test needs the blunter question "what is on disk", because a row surviving its
    window is exactly the state being checked for. Reading the table is also the only
    way to see a row that ``live`` would filter out, which is what makes the retention
    margin below observable at all.

    Opened with a busy timeout because the flusher tests read this while a background
    thread may be mid-commit - the one moment SQLite refuses a reader.
    """
    connection = sqlite3.connect(factory.db_path, timeout=5)
    try:
        return {
            (row[0], row[1]): (text_to_datetime(row[2]), row[3])
            for row in connection.execute(
                "SELECT bucket, subject, window_started_at, count FROM rate_limits"
            )
        }
    finally:
        connection.close()


def test_a_flush_writes_what_the_hot_layer_counted(limiter, factory):
    limiter.check("sign_up", "ada@example.com", LIMIT, now=NOON)
    limiter.check("sign_up", "ada@example.com", LIMIT, now=NOON)
    limiter.check("sign_in", "grace@example.com", LIMIT, now=NOON)

    report = limiter.flush(now=NOON)

    assert report.written == 2
    assert stored(factory) == {
        ("sign_up", "ada@example.com"): (NOON, 2),
        ("sign_in", "grace@example.com"): (NOON, 1),
    }


def test_warming_adopts_what_the_last_process_flushed(factory):
    """A restart is not a way to get a fresh budget - the claim the cold layer exists for."""
    first = TwoLayerRateLimiter(InMemoryRateCounter(), factory, retention=HOUR)
    for _ in range(4):
        first.check("sign_in", "ada@example.com", LIMIT, now=NOON)
    first.flush(now=NOON)

    second = TwoLayerRateLimiter(InMemoryRateCounter(), factory, retention=HOUR)
    adopted = second.warm(now=NOON)

    assert adopted == 1
    assert second.held_keys() == 1
    verdict = second.check(
        "sign_in", "ada@example.com", Limit(calls=4, window=HOUR), now=NOON
    )
    assert not verdict.allowed, "the four calls from the dead process still count"


def test_warming_does_not_adopt_a_window_that_has_already_lapsed(factory):
    """A stored row nobody can be limited by must not refuse the new process's callers.

    The row is still on disk when the process that wrote it stops - retention keeps it
    past the end of its window so a *live* window is never swept - so the check that it
    has lapsed belongs on the way in as well as on the way out. A restart must not
    resurrect a budget that expired while the process was down.
    """
    first = TwoLayerRateLimiter(InMemoryRateCounter(), factory, retention=HOUR)
    for _ in range(4):
        first.check("sign_in", "ada@example.com", LIMIT, now=NOON)
    first.flush(now=NOON)

    second = TwoLayerRateLimiter(InMemoryRateCounter(), factory, retention=HOUR)
    adopted = second.warm(now=NOON + 2 * HOUR)

    assert adopted == 0
    assert second.held_keys() == 0


def test_a_second_flush_does_not_write_the_same_increments_again(limiter, factory):
    """Flushing twice is two deltas, not the same total twice.

    This is what ``flushed_count`` is for. Without it the second flush would report the
    running total again and the stored count would double - and it would double on
    every interval, so a long-running process would inflate its own counters without
    limit and start refusing callers who had barely knocked.
    """
    limiter.check("sign_up", "ada@example.com", LIMIT, now=NOON)
    limiter.flush(now=NOON)

    limiter.check("sign_up", "ada@example.com", LIMIT, now=NOON)
    limiter.check("sign_up", "ada@example.com", LIMIT, now=NOON)
    report = limiter.flush(now=NOON)

    assert report.written == 1
    assert stored(factory)[("sign_up", "ada@example.com")] == (NOON, 3)


def test_a_flush_with_nothing_to_write_is_not_an_error(limiter, factory):
    """The common case, and it must not raise.

    A quiet installation flushes every thirty seconds and nearly every one of those
    flushes has nothing to say. A flusher that treated that as a failure would spend
    its life in error, and the error would be ignored long before the one that mattered
    arrived.
    """
    report = limiter.flush(now=NOON)

    assert report.written == 0
    assert stored(factory) == {}


def test_two_limiters_over_one_database_sum_rather_than_overwrite(factory):
    """The claim the delta flush is built for: two processes, one true total.

    Each limiter is a separate process's view - its own hot counter, its own unflushed
    increments. Writing totals would leave whichever flushed last owning the number;
    writing deltas leaves the stored count equal to the sum, which is what a caller's
    actual usage was. Note this is about the *storage* being correct, not about the two
    workers enforcing one budget - a single worker's refusal is not seen by the other,
    and ``two_layer_limiter.py``'s docstring says so plainly.
    """
    first = TwoLayerRateLimiter(InMemoryRateCounter(), factory, retention=HOUR)
    second = TwoLayerRateLimiter(InMemoryRateCounter(), factory, retention=HOUR)

    for _ in range(3):
        first.check("sign_in", "ada@example.com", LIMIT, now=NOON)
    for _ in range(4):
        second.check("sign_in", "ada@example.com", LIMIT, now=NOON)

    first.flush(now=NOON)
    second.flush(now=NOON)

    assert stored(factory)[("sign_in", "ada@example.com")] == (NOON, 7)


def test_a_new_window_replaces_the_stored_count_rather_than_adding_to_it(factory):
    """When the windows differ, the incoming count is the new total, not a delta.

    The ``CASE`` in the upsert. Adding here would charge a caller for the previous
    window's calls on top of this window's, so somebody who used their whole budget an
    hour ago would begin the new window already over it and be refused forever - a
    limiter that never lets anybody back in.
    """
    first = TwoLayerRateLimiter(InMemoryRateCounter(), factory, retention=HOUR)
    for _ in range(5):
        first.check("sign_in", "ada@example.com", LIMIT, now=NOON)
    first.flush(now=NOON)

    second = TwoLayerRateLimiter(InMemoryRateCounter(), factory, retention=HOUR)
    for _ in range(2):
        second.check("sign_in", "ada@example.com", LIMIT, now=NOON + HOUR)
    second.flush(now=NOON + HOUR)

    assert stored(factory)[("sign_in", "ada@example.com")] == (NOON + HOUR, 2)


def test_a_flush_deletes_rows_whose_window_has_lapsed(factory):
    """The table is bounded, which is a durability property rather than tidiness.

    One row per subject ever seen is unbounded, and a limiter keyed on caller-supplied
    identifiers is therefore a table an attacker can grow - which makes it a disk that
    fills rather than a limit that holds.
    """
    limiter = TwoLayerRateLimiter(InMemoryRateCounter(), factory, retention=HOUR)
    limiter.check("sign_up", "ada@example.com", LIMIT, now=NOON)
    limiter.flush(now=NOON)

    report = limiter.flush(now=NOON + 2 * HOUR)

    assert report.swept == 1
    assert stored(factory) == {}


def test_a_row_inside_its_retention_is_not_swept(factory):
    """Retention is at least the longest window, and this is the margin it buys.

    A row swept while its window was still live would hand its subject a fresh budget
    in the middle of that window - which is why ``rate_limits.longest_window`` computes
    the value from the policy table rather than a number being chosen here.
    """
    limiter = TwoLayerRateLimiter(InMemoryRateCounter(), factory, retention=HOUR)
    limiter.check("sign_up", "ada@example.com", LIMIT, now=NOON)
    limiter.flush(now=NOON)

    report = limiter.flush(now=NOON + timedelta(minutes=59))

    assert report.swept == 0
    assert stored(factory) != {}


def test_check_never_touches_the_database():
    """The hot layer answers alone, so a dead store does not stop the limiting.

    This is the property that makes the two-layer arrangement worth having rather than
    a cache in front of a store. What is lost when the cold layer is unhealthy is
    durability across a restart; what is *not* lost is the limit, because nothing on
    the request path reads it. ``check`` is called here against a factory whose very
    ``start`` raises, and it still answers.
    """
    limiter = TwoLayerRateLimiter(
        InMemoryRateCounter(), _RefusesToStart(), retention=HOUR
    )

    verdict = limiter.check(
        "sign_up", "ada@example.com", Limit(calls=1, window=HOUR), now=NOON
    )

    assert verdict.allowed


def test_a_flush_that_could_not_be_stored_loses_nothing(factory):
    """``mark_flushed`` runs only after the commit returns, and this is why.

    A failed write leaves the increments unmarked, so the next flush offers them again
    and the count is merely late. The alternative - marking first - would discard
    counts on every failure, which is under-counting: the direction nothing in this
    design is allowed to fail in.

    The counter is shared between the two limiters so that the second one is looking at
    the increments the first one failed to write. That is the whole assertion: the
    retry writes them.
    """
    counter = InMemoryRateCounter()
    broken = TwoLayerRateLimiter(counter, _RefusesToWrite(factory), retention=HOUR)
    broken.check("sign_up", "ada@example.com", LIMIT, now=NOON)

    with pytest.raises(RuntimeError):
        broken.flush(now=NOON)

    healed = TwoLayerRateLimiter(counter, factory, retention=HOUR)
    assert healed.flush(now=NOON).written == 1
    assert stored(factory)[("sign_up", "ada@example.com")] == (NOON, 1)


def test_a_failed_flush_leaves_the_lapsed_row_for_the_next_attempt(factory):
    """A failed flush must not leave a half-done sweep behind.

    The writes and the sweep share one unit of work, and the module records why: a
    sweep that committed separately could delete a row between this process reading it
    and writing to it, and the write would then resurrect a count from a window that
    had just been swept - presenting this process's delta as a total. So a failed write
    means the sweep did not happen either, and the row is still there next time.
    """
    limiter = TwoLayerRateLimiter(InMemoryRateCounter(), factory, retention=HOUR)
    limiter.check("sign_up", "ada@example.com", LIMIT, now=NOON)
    limiter.flush(now=NOON)

    # A fresh counter with a live increment of its own, so the failing ``add`` is
    # actually reached - a flush with nothing pending never writes and so never fails.
    broken = TwoLayerRateLimiter(
        InMemoryRateCounter(), _RefusesToWrite(factory), retention=HOUR
    )
    broken.check("sign_up", "grace@example.com", LIMIT, now=NOON + 2 * HOUR)

    with pytest.raises(RuntimeError):
        broken.flush(now=NOON + 2 * HOUR)

    assert ("sign_up", "ada@example.com") in stored(factory), (
        "the lapsed row survived, because the sweep shares the failed transaction"
    )


# ---------------------------------------------------------------------------
# The thread
# ---------------------------------------------------------------------------
#
# Everything above calls ``flush`` directly. These three are the only tests in the
# file that involve a thread, and they are here because the subject is genuinely the
# thread - that it runs unprompted, that it stops when asked, and that a failure does
# not end it. All three read the wall clock, because the thread does.


def _wait_for(predicate, timeout: float = 5.0) -> bool:
    """Poll until ``predicate`` holds. Returns whether it ever did.

    A bounded poll rather than a fixed sleep, so a fast machine finishes the test in
    milliseconds and a loaded one is still given several seconds. A fixed sleep would
    have to be long enough for the worst machine, which makes the suite slow for
    everybody to protect against a flake.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def test_a_flusher_writes_without_being_asked(factory):
    limiter = TwoLayerRateLimiter(InMemoryRateCounter(), factory, retention=HOUR)
    limiter.check("sign_up", "ada@example.com", LIMIT, now=datetime.now())
    flusher = BackgroundFlusher(limiter, interval_seconds=0.05)

    flusher.start()
    try:
        assert _wait_for(lambda: stored(factory) != {}), "no flush ever reached the store"
        assert flusher.last_error is None
    finally:
        flusher.stop()


def test_stopping_flushes_once_more_so_a_clean_shutdown_loses_nothing(factory):
    """The final flush happens on the *calling* thread, not the departing one.

    That is what makes the guarantee something a caller can rely on: a process that has
    returned from ``stop`` knows the counts are stored, rather than knowing that a
    thread was asked nicely. The interval here is an hour, so the timer never fires and
    the only thing that can have written the row is the shutdown flush.
    """
    limiter = TwoLayerRateLimiter(InMemoryRateCounter(), factory, retention=HOUR)
    limiter.check("sign_up", "ada@example.com", LIMIT, now=datetime.now())
    flusher = BackgroundFlusher(limiter, interval_seconds=3600)

    flusher.start()
    flusher.stop()

    assert stored(factory)[("sign_up", "ada@example.com")][1] == 1


def test_a_failing_flush_does_not_end_the_loop_and_does_not_escape_shutdown(factory):
    """A limiter that stopped persisting must not also stop limiting - or stop the app.

    Two failures are ruled out. The loop must carry on, because a control that quietly
    switched itself off after one locked database is worse than one that never
    persisted at all: it would look like it was working. And ``stop`` must not raise,
    because this runs inside a lifespan's shutdown, where an exception replaces
    whatever the application was doing with a stack trace about counters.
    """
    limiter = TwoLayerRateLimiter(
        InMemoryRateCounter(), _RefusesToWrite(factory), retention=HOUR
    )
    limiter.check("sign_up", "ada@example.com", LIMIT, now=datetime.now())
    flusher = BackgroundFlusher(limiter, interval_seconds=0.01)

    flusher.start()
    try:
        assert _wait_for(lambda: flusher.last_error is not None)
        first = flusher.last_error
        # Forgetting the error is how a second attempt is observed: the loop must set it
        # again, which it can only do by having tried again.
        flusher.last_error = None
        assert _wait_for(lambda: flusher.last_error is not None), (
            "the loop ended after the first failure"
        )
        assert flusher.last_error is not first
    finally:
        flusher.stop()  # must not raise, even though its final flush fails


def test_a_flusher_refuses_an_interval_that_would_not_flush(factory):
    """Zero is not a flusher with a very short interval, it is no flusher.

    ``Event.wait(0)`` returns immediately, so a zero interval would be a thread that
    never sleeps - spinning on the database as fast as the disk allows, and doing it on
    a background thread where nobody would see it. The caller that means "do not flush"
    has to build no flusher at all, which is what the lifespan does.
    """
    limiter = TwoLayerRateLimiter(InMemoryRateCounter(), factory, retention=HOUR)

    with pytest.raises(ValueError):
        BackgroundFlusher(limiter, interval_seconds=0)


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class _RefusesToStart:
    """A unit of work factory that cannot open one, like an unreachable database."""

    def start(self):
        raise RuntimeError("the database is not reachable")


class _RefusesToWrite:
    """A factory whose unit of work reads fine and fails on the write.

    Not a mock of the repository: what it replaces is the *disk*, and the failure it
    simulates is the one this design has a specific answer for - a write that raises
    after the increments have already been read out of the hot layer.
    """

    def __init__(self, factory):
        self._factory = factory

    def start(self):
        return _FailingUnitOfWork(self._factory.start())


class _FailingUnitOfWork:
    """A unit of work whose only difference is that ``rate_limits.add`` raises."""

    def __init__(self, inner):
        self._inner = inner
        self.rate_limits = _FailingRepository(inner.rate_limits)

    def commit(self):
        return self._inner.commit()

    def rollback(self):
        return self._inner.rollback()


class _FailingRepository:
    def __init__(self, inner):
        self._inner = inner

    def live(self, now, longest):
        return self._inner.live(now, longest)

    def forget_lapsed(self, now, longest):
        return self._inner.forget_lapsed(now, longest)

    def add(self, pending):
        raise RuntimeError("the database is locked")
