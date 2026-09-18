"""The hot layer's own rules, driven by a clock the test holds.

**Nothing in this file sleeps and nothing is monkeypatched**, which is the point of
``now`` being a parameter on every method rather than a ``datetime.now()`` inside
one. A window boundary is the whole subject of half these tests, and a test that
had to reach one by waiting would be slow, flaky, or both - and would be measuring
the wall clock rather than the rule.

The one exception is the concurrency test at the bottom, and it is an exception
because threads are the thing under test there rather than the clock.
"""

from datetime import datetime, timedelta
from threading import Thread

import pytest

from app.infrastructure.rate_limiting import InMemoryRateCounter, Limit, Pending

#: The moment every window in this file opens at.
#:
#: Fixed rather than ``datetime.now()``, for the reason ``tests/conftest.py`` pins
#: ``POT_MOMENT``: an assertion about a window's *length* should not depend on when
#: the suite ran, and a test that read the clock would have to know when it ran to
#: write its own expectation.
NOON = datetime(2026, 9, 17, 12, 0, 0)

HOUR = timedelta(hours=1)


def test_a_limit_permitting_no_calls_is_refused():
    """Zero calls is not a tight limit, it is a route switched off by accident.

    Refused here rather than left to mean "refuse everything", because a limit of
    zero in a policy table is far more likely to be a mistake than an intention -
    and the failure it would produce is an endpoint that answers 429 to everybody
    and looks, from outside, exactly like a limiter that works.
    """
    with pytest.raises(ValueError):
        Limit(calls=0, window=HOUR)


def test_a_window_with_no_length_is_refused():
    """A window of zero would make every check open a new one, so nothing is bounded.

    The same class of mistake as the limit of zero above and the opposite outcome:
    a limit that never refuses anything.
    """
    with pytest.raises(ValueError):
        Limit(calls=1, window=timedelta(0))


def test_the_limit_is_the_number_of_calls_allowed():
    """``Limit(calls=3)`` allows three and refuses the fourth.

    Asserted because the off-by-one is the entire difference between a limiter and
    an outage, and a bare number in a policy table cannot say which convention it
    means - see ``Limit``'s own docstring, which is where the convention is
    written down.
    """
    counter = InMemoryRateCounter()
    limit = Limit(calls=3, window=HOUR)

    verdicts = [
        counter.check_and_increment("sign_up", "ada@example.com", limit, NOON)
        for _ in range(4)
    ]

    assert [verdict.allowed for verdict in verdicts] == [True, True, True, False]


def test_a_refusal_says_how_much_of_the_window_is_left():
    """``Retry-After`` is the truth about this window, not a rounded constant.

    Twenty minutes into an hour-long window, forty minutes remain - and a caller
    told to wait forty minutes is being told something it can act on, where a
    caller told a flat "3600" would come back early and be refused again.
    """
    counter = InMemoryRateCounter()
    limit = Limit(calls=1, window=HOUR)
    counter.check_and_increment("sign_in", "ada@example.com", limit, NOON)

    refused = counter.check_and_increment(
        "sign_in", "ada@example.com", limit, NOON + timedelta(minutes=20)
    )

    assert refused.retry_after == timedelta(minutes=40)
    assert refused.retry_after_seconds == 2400


def test_the_header_never_advises_a_retry_that_would_fail_again():
    """A sub-second remainder is rounded **up** to one, never down to zero.

    Two failures are being ruled out at once, and both would be invisible without
    this test. Rounding down would tell a client that obeys the header to retry a
    fraction early, where it would be refused again - a header whose advice does
    not work. And a ``Retry-After: 0`` invites an immediate retry, which is the
    precise opposite of what a limiter is for: an obedient client would turn into a
    tight loop against the door it was asked to leave alone.
    """
    counter = InMemoryRateCounter()
    limit = Limit(calls=1, window=HOUR)
    counter.check_and_increment("sign_in", "ada@example.com", limit, NOON)

    refused = counter.check_and_increment(
        "sign_in", "ada@example.com", limit, NOON + HOUR - timedelta(microseconds=1)
    )

    assert refused.retry_after_seconds == 1


def test_a_refused_attempt_still_costs():
    """The count is spent by the *attempt*, not by the success.

    This is the rule that decides whether the limiter bounds anything. A counter
    that rolled back its increment on refusal would let a caller try forever at
    exactly the limit's rate, so the budget would bound how many requests
    *succeeded* and nothing about how many were made - which is no bound at all.

    The observable is ``pending``'s delta rather than the verdicts, because the
    verdicts look identical either way: seven calls were made, so the cold layer
    will be told seven, and only two of them were let through.
    """
    counter = InMemoryRateCounter()
    limit = Limit(calls=2, window=HOUR)

    for _ in range(2):
        assert counter.check_and_increment("sign_up", "ada@example.com", limit, NOON).allowed
    for _ in range(5):
        refused = counter.check_and_increment("sign_up", "ada@example.com", limit, NOON)
        assert refused.allowed is False

    assert [item.delta for item in counter.pending()] == [7]


def test_the_window_rolls_over_and_hands_the_budget_back():
    counter = InMemoryRateCounter()
    limit = Limit(calls=1, window=HOUR)

    assert counter.check_and_increment("sign_up", "ada@example.com", limit, NOON).allowed
    assert not counter.check_and_increment(
        "sign_up", "ada@example.com", limit, NOON + timedelta(minutes=59)
    ).allowed
    assert counter.check_and_increment(
        "sign_up", "ada@example.com", limit, NOON + HOUR
    ).allowed, "a call at exactly one window later opens a new window"


def test_a_new_window_does_not_carry_the_old_windows_calls():
    """Rollover replaces the entry, so the finished window's delta goes with it.

    Deliberate rather than incidental - see the module docstring. The window it
    belonged to is over, so no check will ever consult it again, and persisting it
    would mean the table held rows nothing would ever read. What is *not* lost is
    the current window, which is the one that can still refuse somebody.
    """
    counter = InMemoryRateCounter()
    limit = Limit(calls=1, window=HOUR)
    counter.check_and_increment("sign_up", "ada@example.com", limit, NOON)

    counter.check_and_increment("sign_up", "ada@example.com", limit, NOON + HOUR)

    assert [
        (item.window_started_at, item.delta) for item in counter.pending()
    ] == [(NOON + HOUR, 1)]


def test_a_subject_and_a_bucket_are_both_part_of_the_key():
    """Neither a different address nor a different door spends the other's budget.

    Both halves matter and they fail differently. If the subject were not part of
    the key, one abusive address would exhaust the budget for everybody. If the
    bucket were not, a caller hammering the reset door would close the sign-up door
    - and the second failure is the worse one, because it is not a caller
    attacking itself.
    """
    counter = InMemoryRateCounter()
    limit = Limit(calls=1, window=HOUR)

    assert counter.check_and_increment("sign_up", "ada@example.com", limit, NOON).allowed
    assert counter.check_and_increment("sign_up", "grace@example.com", limit, NOON).allowed
    assert counter.check_and_increment("sign_in", "ada@example.com", limit, NOON).allowed

    assert not counter.check_and_increment(
        "sign_up", "ada@example.com", limit, NOON
    ).allowed


def test_marking_flushed_advances_by_what_was_written():
    """``mark_flushed`` records a delta taken, not the current count.

    Advancing to the *current* count would be the natural-looking mistake, and it
    would silently drop any increment that arrived while the cold write was in
    flight - a race whose window is a disk write long and whose symptom is a budget
    that is slightly too generous. The second half of this test is what pins it:
    one more call after the mark must be offered as ``1``, not swallowed as part of
    a total that was already reported.
    """
    counter = InMemoryRateCounter()
    limit = Limit(calls=10, window=HOUR)
    counter.check_and_increment("sign_up", "ada@example.com", limit, NOON)

    written = counter.pending()
    assert [item.delta for item in written] == [1]
    counter.mark_flushed(written)
    assert counter.pending() == []

    counter.check_and_increment("sign_up", "ada@example.com", limit, NOON)
    assert [item.delta for item in counter.pending()] == [1]


def test_an_increment_that_arrived_during_the_write_is_offered_again():
    """The read and the mark are deliberately two calls, and this is why.

    ``pending`` does not drain and ``mark_flushed`` does not read, so a write that
    fails - or a request that lands mid-write - leaves the books recoverable. The
    failure this rules out is the one a single drain-and-mark would produce: an
    insert that raised after the increments had already been discarded, losing
    counts that the cold layer never received.
    """
    counter = InMemoryRateCounter()
    limit = Limit(calls=10, window=HOUR)
    counter.check_and_increment("sign_up", "ada@example.com", limit, NOON)

    in_flight = counter.pending()
    counter.check_and_increment("sign_up", "ada@example.com", limit, NOON)
    counter.mark_flushed(in_flight)

    assert [item.delta for item in counter.pending()] == [1]


def test_marking_a_window_that_has_rolled_does_not_charge_the_new_one():
    """Increments from a finished window are dropped rather than added to the new one.

    The mark arrives after an awaited disk write, so the window can roll in
    between. Charging them to the new window would mean a caller who was limited at
    the end of one window started the next one already part-spent - a limiter that
    over-counts, which refuses somebody who has done nothing.
    """
    counter = InMemoryRateCounter()
    limit = Limit(calls=1, window=HOUR)
    counter.check_and_increment("sign_up", "ada@example.com", limit, NOON)
    in_flight = counter.pending()

    counter.check_and_increment("sign_up", "ada@example.com", limit, NOON + HOUR)
    counter.mark_flushed(in_flight)

    pending = counter.pending()
    assert [(item.window_started_at, item.delta) for item in pending] == [
        (NOON + HOUR, 1)
    ]


def test_seeding_adopts_a_stored_count_without_offering_it_back():
    """A warm start must not re-flush what it just read.

    If ``seed`` set ``count`` without setting ``flushed_count``, the first flush of
    the new process would write the dead process's calls a second time. The count
    would still be too high rather than too low - the safe direction - but it would
    be wrong for no reason, and it would compound on every restart.
    """
    counter = InMemoryRateCounter()
    limit = Limit(calls=4, window=HOUR)

    counter.seed(
        [Pending("sign_up", "ada@example.com", NOON, 4)]
    )

    assert counter.pending() == [], "the adopted count is not this process's to write"
    assert not counter.check_and_increment(
        "sign_up", "ada@example.com", limit, NOON
    ).allowed, "but it does count against the limit"


def test_a_seeded_count_over_the_limit_still_refuses():
    """A restart is not a way to get a fresh budget, even past the limit.

    The stored count can legitimately exceed ``calls``: a caller can spend a full
    budget and a few refusals on top of it, all of which are recorded. So the
    comparison has to be ``count > calls`` on the seeded value as well, and this is
    the assertion that says a restored count above the line is refused until its
    window rolls rather than reset to a permitted one.
    """
    counter = InMemoryRateCounter()
    limit = Limit(calls=3, window=HOUR)
    counter.seed([Pending("sign_up", "ada@example.com", NOON, 99)])

    assert not counter.check_and_increment("sign_up", "ada@example.com", limit, NOON).allowed
    assert counter.check_and_increment(
        "sign_up", "ada@example.com", limit, NOON + HOUR
    ).allowed


def test_seeding_ignores_a_row_that_counts_nothing():
    """A non-positive row describes no calls, so there is no window to open.

    Defensive rather than theoretical: the column carries ``CHECK (count > 0)``, so
    this cannot arrive from the store today. It is here because adopting such a row
    would create an entry whose window is a moment nothing happened at, and the
    only thing that entry could ever do is refuse somebody.
    """
    counter = InMemoryRateCounter()

    counter.seed([Pending("sign_up", "ada@example.com", NOON, 0)])

    assert len(counter) == 0


def test_a_lapsed_and_fully_flushed_key_is_dropped():
    """Without this the dict grows with every address anybody has ever tried.

    A subject-keyed limiter that never forgets is a slow leak dressed as a cache,
    and it leaks on exactly the traffic an attacker controls - one entry per
    invented address.
    """
    counter = InMemoryRateCounter()
    counter.seed([Pending("sign_up", "ada@example.com", NOON, 3)])

    swept = counter.forget_lapsed(NOON + 2 * HOUR, HOUR)

    assert swept == 1
    assert len(counter) == 0


def test_a_key_inside_its_window_is_kept():
    """``longest`` is the floor, not a guess: a live window must never be swept.

    Sweeping a row whose window was still running would hand its subject a fresh
    budget mid-window, which is the one thing the cold layer exists to prevent -
    and it would do it to the caller who had just earned the refusal.
    """
    counter = InMemoryRateCounter()
    counter.seed([Pending("sign_up", "ada@example.com", NOON, 3)])

    assert counter.forget_lapsed(NOON + timedelta(minutes=30), HOUR) == 0
    assert len(counter) == 1


def test_a_lapsed_key_that_has_not_been_flushed_is_kept():
    """Only fully-flushed keys are swept, because the other kind still has news.

    Dropping an unflushed key would discard counts the cold layer never received,
    which is under-counting - the direction this design does not allow. The cost of
    waiting is one more interval of a dict entry, and the entry is picked up by the
    next flush.
    """
    counter = InMemoryRateCounter()
    counter.check_and_increment("sign_up", "ada@example.com", Limit(calls=5, window=HOUR), NOON)

    swept = counter.forget_lapsed(NOON + 2 * HOUR, HOUR)

    assert swept == 0
    assert [item.delta for item in counter.pending()] == [1]


def test_concurrent_increments_sum_exactly():
    """Every endpoint is a plain ``def``, so this object is entered by threads for real.

    FastAPI runs blocking endpoints in its threadpool, which is the correct place
    for synchronous SQLite work and means several requests are inside
    ``check_and_increment`` at once. ``claude.md`` names the rule this satisfies -
    never assume a check followed by an update is atomic - and the update here is
    ``entry.count += 1``, which is a load, an add and a store rather than one step.
    Without the lock spanning the read, the comparison and the increment, threads
    interleave between those three and lose counts, and the total comes out short
    of the calls actually made.

    The assertion is exact rather than a threshold, because "close enough" is the
    symptom being tested for: a limiter that loses one increment in a thousand is a
    limiter whose number is not the number in the policy table.
    """
    counter = InMemoryRateCounter()
    limit = Limit(calls=10_000, window=HOUR)
    threads = 16
    per_thread = 250

    def hammer() -> None:
        for _ in range(per_thread):
            counter.check_and_increment("sign_up", "ada@example.com", limit, NOON)

    workers = [Thread(target=hammer) for _ in range(threads)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    assert [item.delta for item in counter.pending()] == [threads * per_thread]
    assert len(counter) == 1, "one key, whichever thread got there first"
