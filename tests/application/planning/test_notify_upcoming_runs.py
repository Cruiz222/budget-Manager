from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.application.planning.execute_plan_run import ExecutePlanRun
from app.application.planning.notify_upcoming_runs import (
    NOTICE_WINDOW,
    NotifyUpcomingRuns,
)
from app.domain.money.currency import Currency
from app.domain.money.money import Money
from app.domain.planning.planStatus import PlanStatus
from app.domain.planning.runBlockReason import RunBlockReason
from app.domain.planning.runStatus import RunStatus
from app.infrastructure.persistence.sqlite_unit_of_work import SqliteUnitOfWorkFactory

NGN = Currency.NGN

#: Noon on 2 March 2026 - the headline case, and the reason the datetime
#: refactor happened at all. Every assertion below is relative to this moment,
#: because a window is only meaningful against a time of day.
NOON = datetime(2026, 3, 2, 12, 0)
HALF_PAST_ELEVEN = datetime(2026, 3, 2, 11, 30)

#: Where the queued warning is addressed. Passed in rather than read from the
#: environment, because ``NotifyUpcomingRuns`` takes its inputs as arguments -
#: reading ``os.environ`` is the CLI's job, not the use case's.
RECIPIENT = "chinedu@example.com"


class ExplodingCommit:
    """A unit of work that writes normally and then fails to commit.

    The way to ask "do these two writes really land together?" is to break the
    commit and look at what is left. A unit that delegated everything but could
    still commit could not answer it; this one forwards every repository and
    refuses only the last step, which is exactly the failure a crash between two
    separate transactions would produce.
    """

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def commit(self):
        raise RuntimeError("commit failed")


class ExplodingCommitFactory:
    def __init__(self, inner):
        self._inner = inner

    def start(self):
        return ExplodingCommit(self._inner.start())


def build_notifier(tmp_path, name="notices.db", recipient=RECIPIENT):
    factory = SqliteUnitOfWorkFactory(str(tmp_path / name))
    return NotifyUpcomingRuns(factory, recipient=recipient), factory


def seed(factory, plans_and_wallets):
    uow = factory.start()
    try:
        for wallet, plan in plans_and_wallets:
            uow.wallets.save(wallet)
            uow.plans.save(plan)
        uow.commit()
    except BaseException:
        uow.rollback()
        raise


def read(factory, accessor):
    uow = factory.start()
    try:
        return accessor(uow)
    finally:
        uow.rollback()


def stored_notices(factory, plan_id):
    """Every notice row a plan actually holds - the record, not the report."""
    return read(factory, lambda uow: uow.notices.list_by_plan_id(plan_id))


def stored_messages(factory):
    """Every message row still owed - the queue, not the report."""
    return read(factory, lambda uow: uow.outbound_messages.pending())


def advance_past(factory, plan_id):
    """Move a plan on to its next occurrence, as a successful run would.

    Done directly rather than by ticking, so these tests stay about the warning
    and do not quietly become tests of the scheduler.
    """
    uow = factory.start()
    try:
        plan = uow.plans.get_by_id(plan_id)
        plan.record_run()
        uow.plans.save(plan)
        uow.commit()
    except BaseException:
        uow.rollback()
        raise


class TestTheWindow:
    """Thirty minutes before the run, and not a moment that means something else.

    These are the tests the whole phase hangs on, and the load-bearing one is
    ``test_at_the_due_moment_it_pays_rather_than_warns`` below. The obvious way
    to write the window is an inequality on ``due_at - as_of``, and the obvious
    mistake is to make its lower bound inclusive - at which point a tick landing
    exactly on noon emits a warning instead of moving the money.
    """

    def test_a_plan_a_month_away_warns_nothing(self, build_wallet, build_plan, tmp_path):
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, anchor=NOON)
        notifier, factory = build_notifier(tmp_path)
        seed(factory, [(wallet, plan)])

        assert notifier.execute(datetime(2026, 2, 2, 12, 0)) == []

    def test_one_minute_before_the_window_warns_nothing(
        self, build_wallet, build_plan, tmp_path
    ):
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, anchor=NOON)
        notifier, factory = build_notifier(tmp_path)
        seed(factory, [(wallet, plan)])

        assert notifier.execute(datetime(2026, 3, 2, 11, 29)) == []

    def test_the_window_opens_exactly_thirty_minutes_before(
        self, build_wallet, build_plan, tmp_path
    ):
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, anchor=NOON)
        notifier, factory = build_notifier(tmp_path)
        seed(factory, [(wallet, plan)])

        raised = notifier.execute(HALF_PAST_ELEVEN)

        assert len(raised) == 1
        assert raised[0].plan_id == plan.plan_id
        assert raised[0].due_at == NOON

    def test_a_minute_before_the_run_is_still_inside_the_window(
        self, build_wallet, build_plan, tmp_path
    ):
        """The window is a span, not an instant - a tick near the end still warns."""
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, anchor=NOON)
        notifier, factory = build_notifier(tmp_path)
        seed(factory, [(wallet, plan)])

        assert len(notifier.execute(datetime(2026, 3, 2, 11, 59))) == 1

    def test_at_the_due_moment_it_pays_rather_than_warns(
        self, build_wallet, build_plan, tmp_path
    ):
        """The lower bound is strict, and this is what says so.

        At ``as_of == due_at`` the plan is *due* - ``is_due_at`` says so, and the
        scheduler acts on it. There is no longer anything to warn about, and a
        window written as ``0 <= delta <= thirty`` would warn here instead of
        paying. The warning is worthless at this instant and the payment is the
        whole point.
        """
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, anchor=NOON)
        notifier, factory = build_notifier(tmp_path)
        seed(factory, [(wallet, plan)])

        assert notifier.execute(NOON) == []

    def test_an_overdue_plan_warns_nothing(self, build_wallet, build_plan, tmp_path):
        """"Coming up" and "already owed" are different, and only one is news.

        A plan that should have run at noon and did not is not about to fire - it
        is waiting to be paid. Telling the user it is thirty minutes away would
        be actively wrong.
        """
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, anchor=NOON)
        notifier, factory = build_notifier(tmp_path)
        seed(factory, [(wallet, plan)])

        assert notifier.execute(datetime(2026, 3, 2, 12, 5)) == []
        assert notifier.execute(datetime(2026, 3, 5, 9, 0)) == []

    def test_the_window_is_the_default_thirty_minutes(self):
        assert NOTICE_WINDOW == timedelta(minutes=30)


class TestWhatTheWarningSays:
    def test_it_names_the_plan_and_what_the_run_costs(
        self, build_wallet, build_plan, tmp_path
    ):
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, anchor=NOON, name="Salary 2026")
        notifier, factory = build_notifier(tmp_path)
        seed(factory, [(wallet, plan)])

        raised = notifier.execute(HALF_PAST_ELEVEN)

        assert raised[0].plan_name == "Salary 2026"
        assert raised[0].amount == Money(Decimal("2000"), NGN)

    def test_the_raised_moment_is_the_moment_the_tick_believed_it_was(
        self, build_wallet, build_plan, tmp_path
    ):
        """Not the wall clock - so replaying a tick produces the same row.

        ``as_of`` is what the whole application layer is built on, and taking it
        here as well is what makes a backdated tick reproducible rather than
        merely re-runnable.
        """
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, anchor=NOON)
        notifier, factory = build_notifier(tmp_path)
        seed(factory, [(wallet, plan)])

        raised = notifier.execute(HALF_PAST_ELEVEN)

        assert raised[0].raised_at == HALF_PAST_ELEVEN
        assert stored_notices(factory, plan.plan_id)[0].raised_at == HALF_PAST_ELEVEN


class TestItDoesNotRepeat:
    def test_a_second_tick_inside_the_window_raises_nothing(
        self, build_wallet, build_plan, tmp_path
    ):
        """The reason the record exists.

        A tick every minute from 11:30 to noon is thirty opportunities to say the
        same thing. Saying it once is the feature; saying it thirty times is the
        bug, and it is the kind of bug nobody files because it looks like the
        system working.
        """
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, anchor=NOON)
        notifier, factory = build_notifier(tmp_path)
        seed(factory, [(wallet, plan)])

        first = notifier.execute(HALF_PAST_ELEVEN)
        second = notifier.execute(datetime(2026, 3, 2, 11, 35))
        third = notifier.execute(datetime(2026, 3, 2, 11, 59))

        assert len(first) == 1
        assert second == []
        assert third == []

    def test_the_second_tick_writes_no_second_row(
        self, build_wallet, build_plan, tmp_path
    ):
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, anchor=NOON)
        notifier, factory = build_notifier(tmp_path)
        seed(factory, [(wallet, plan)])

        notifier.execute(HALF_PAST_ELEVEN)
        notifier.execute(datetime(2026, 3, 2, 11, 40))
        notifier.execute(datetime(2026, 3, 2, 11, 50))

        assert len(stored_notices(factory, plan.plan_id)) == 1

    def test_the_next_occurrence_gets_its_own_notice(
        self, build_wallet, build_plan, tmp_path
    ):
        """Once per occurrence, not once per plan.

        The warning must go quiet for the run it has already announced and wake
        up again for the next one - which is exactly what keying on
        ``(plan_id, due_at)`` buys.
        """
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, anchor=NOON, completed_runs=1)
        notifier, factory = build_notifier(tmp_path)
        seed(factory, [(wallet, plan)])
        assert len(notifier.execute(datetime(2026, 4, 2, 11, 30))) == 1
        advance_past(factory, plan.plan_id)

        raised = notifier.execute(datetime(2026, 5, 2, 11, 30))

        assert [notice.due_at for notice in raised] == [datetime(2026, 5, 2, 12, 0)]
        assert len(stored_notices(factory, plan.plan_id)) == 2


class TestWhatIsNotWarned:
    def test_a_paused_plan_warns_nothing(self, build_wallet, build_plan, tmp_path):
        """A plan a human stopped does not get announced as if it were running."""
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, anchor=NOON, status=PlanStatus.PAUSED)
        notifier, factory = build_notifier(tmp_path)
        seed(factory, [(wallet, plan)])

        assert notifier.execute(HALF_PAST_ELEVEN) == []

    def test_a_completed_plan_warns_nothing(self, build_wallet, build_plan, tmp_path):
        wallet = build_wallet(locked="10000")
        plan = build_plan(
            wallet_id=wallet.wallet_id, anchor=NOON, status=PlanStatus.COMPLETED
        )
        notifier, factory = build_notifier(tmp_path)
        seed(factory, [(wallet, plan)])

        assert notifier.execute(HALF_PAST_ELEVEN) == []

    def test_an_upcoming_plan_on_one_wallet_does_not_warn_for_another(
        self, build_wallet, build_plan, tmp_path
    ):
        first_wallet = build_wallet(locked="10000")
        second_wallet = build_wallet(locked="10000")
        soon = build_plan(wallet_id=first_wallet.wallet_id, anchor=NOON)
        later = build_plan(
            wallet_id=second_wallet.wallet_id, anchor=datetime(2026, 3, 2, 18, 0)
        )
        notifier, factory = build_notifier(tmp_path)
        seed(factory, [(first_wallet, soon), (second_wallet, later)])

        raised = notifier.execute(HALF_PAST_ELEVEN)

        assert [notice.plan_id for notice in raised] == [soon.plan_id]


class TestItIsNotAGate:
    """The property this feature exists not to break.

    A warning reports. It does not delay, condition, or block anything - and the
    way that is guaranteed here is structural rather than a rule someone has to
    remember: the notifier never loads a wallet. It has no balance in hand, so
    there is nothing for a payment to be made conditional on.
    """

    def test_an_unaffordable_plan_still_gets_its_warning(
        self, build_wallet, build_plan, tmp_path
    ):
        """An empty wallet is not a reason to stay quiet.

        The warning is about *timing*, not about affordability. Deciding whether
        to warn by looking at the balance would make a courtesy into a judgement,
        and would mean the user hears about a payout only when they can already
        cover it - which is precisely when the warning matters least.
        """
        wallet = build_wallet(available="0", locked="0")
        plan = build_plan(wallet_id=wallet.wallet_id, anchor=NOON)
        notifier, factory = build_notifier(tmp_path)
        seed(factory, [(wallet, plan)])

        raised = notifier.execute(HALF_PAST_ELEVEN)

        assert len(raised) == 1
        assert raised[0].amount == Money(Decimal("2000"), NGN)

    def test_a_warning_does_not_stop_the_payout_that_follows(
        self, build_wallet, build_plan, tmp_path
    ):
        """Warn at 11:30, pay at noon. Both happen, and neither consults the other."""
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, anchor=NOON)
        notifier, factory = build_notifier(tmp_path)
        seed(factory, [(wallet, plan)])

        notifier.execute(HALF_PAST_ELEVEN)
        run = ExecutePlanRun(factory).execute(plan.plan_id, NOON)

        assert run.status is RunStatus.SUCCEEDED
        assert run.due_at == NOON

    def test_a_missed_window_does_not_stop_the_payout(
        self, build_wallet, build_plan, tmp_path
    ):
        """The process was down for the whole window; the payout runs anyway.

        This is the case that makes "automated" mean automated. No warning was
        ever raised for this occurrence, and the money moves at its set time
        regardless - a courtesy that had to happen for the payment to be allowed
        would be a gate wearing a warning's clothes.
        """
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, anchor=NOON)
        notifier, factory = build_notifier(tmp_path)
        seed(factory, [(wallet, plan)])

        # Never ticked inside the window. Straight to the due moment.
        run = ExecutePlanRun(factory).execute(plan.plan_id, NOON)

        assert run.status is RunStatus.SUCCEEDED
        assert stored_notices(factory, plan.plan_id) == []

    def test_a_blocked_run_leaves_the_notice_untouched(
        self, build_wallet, build_plan, tmp_path
    ):
        """The two records are independent, and a blocked run proves it.

        The warning happened, the run could not, and neither fact disturbs the
        other: the notice stays as it was, and the run's reason is recorded
        where reasons live.
        """
        wallet = build_wallet(available="0", locked="0")
        plan = build_plan(wallet_id=wallet.wallet_id, anchor=NOON)
        notifier, factory = build_notifier(tmp_path)
        seed(factory, [(wallet, plan)])

        notifier.execute(HALF_PAST_ELEVEN)
        run = ExecutePlanRun(factory).execute(plan.plan_id, NOON)

        assert run.status is RunStatus.BLOCKED
        assert run.reason is RunBlockReason.INSUFFICIENT_BALANCE
        assert len(stored_notices(factory, plan.plan_id)) == 1


class TestItQueuesTheMessage:
    """Raising a warning is not the same as delivering one.

    Phase 4 made the warning happen once and never gate a payout, and then
    printed it to a log file. A courtesy nobody receives is not a courtesy, so
    raising one now also queues a message - and the queueing commits with the
    claim, because a claimed notice is never raised again.
    """

    def test_a_raised_warning_queues_exactly_one_message(
        self, build_wallet, build_plan, tmp_path
    ):
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, anchor=NOON)
        notifier, factory = build_notifier(tmp_path)
        seed(factory, [(wallet, plan)])

        notifier.execute(HALF_PAST_ELEVEN)

        stored = stored_messages(factory)
        assert len(stored) == 1
        assert stored[0].plan_id == plan.plan_id
        assert stored[0].due_at == NOON
        assert stored[0].recipient == RECIPIENT

    def test_the_queued_message_says_what_the_warning_says(
        self, build_wallet, build_plan, tmp_path
    ):
        """Composed at enqueue, so the words are checked where they are written.

        Nothing re-renders them on the way out, which is what makes this the
        authoritative record of what the user was owed.
        """
        wallet = build_wallet(locked="10000")
        plan = build_plan(
            wallet_id=wallet.wallet_id, anchor=NOON, name="Salary 2026"
        )
        notifier, factory = build_notifier(tmp_path)
        seed(factory, [(wallet, plan)])

        notifier.execute(HALF_PAST_ELEVEN)

        message = stored_messages(factory)[0]
        assert message.subject == "Payout of 2000.00 NGN in 30 minutes"
        assert "'Salary 2026'" in message.body
        assert "2000.00 NGN" in message.body
        assert "2026-03-02T12:00" in message.body

    def test_the_wait_is_what_is_left_not_what_the_window_is_wide(
        self, build_wallet, build_plan, tmp_path
    ):
        """A tick late in the window should quote the time actually remaining.

        The window is thirty minutes wide, but a tick at 11:45 has fifteen
        minutes to report - and telling the user thirty would be a confident
        lie about the only number in the message that matters.
        """
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, anchor=NOON)
        notifier, factory = build_notifier(tmp_path)
        seed(factory, [(wallet, plan)])

        notifier.execute(datetime(2026, 3, 2, 11, 45))

        assert stored_messages(factory)[0].subject == (
            "Payout of 2000.00 NGN in 15 minutes"
        )

    def test_the_message_is_stamped_with_the_tick_that_raised_it(
        self, build_wallet, build_plan, tmp_path
    ):
        """``created_at`` is ``as_of``, so replaying a tick composes the same row.

        Not "now" from the clock: a tick that is replayed - or whose arguments
        are pinned by a test - must produce a byte-identical message rather than
        one that differs in a timestamp nobody is looking at.
        """
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, anchor=NOON)
        notifier, factory = build_notifier(tmp_path)
        seed(factory, [(wallet, plan)])

        notifier.execute(HALF_PAST_ELEVEN)

        assert stored_messages(factory)[0].created_at == HALF_PAST_ELEVEN

    def test_the_second_tick_queues_nothing_further(
        self, build_wallet, build_plan, tmp_path
    ):
        """Saying it once is the feature; saying it thirty times is the bug.

        Two defences hold here and both are worth having: the notice claim
        refuses the second warning, and the outbox key would refuse a second
        message even if it did not. A table whose rows become emails should not
        rely on another table's discipline to keep from mailing someone twice.
        """
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, anchor=NOON)
        notifier, factory = build_notifier(tmp_path)
        seed(factory, [(wallet, plan)])

        notifier.execute(HALF_PAST_ELEVEN)
        notifier.execute(datetime(2026, 3, 2, 11, 40))
        notifier.execute(datetime(2026, 3, 2, 11, 59))

        assert len(stored_messages(factory)) == 1

    def test_the_next_occurrence_queues_its_own_message(
        self, build_wallet, build_plan, tmp_path
    ):
        """Once per occurrence, matching the notice exactly."""
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, anchor=NOON, completed_runs=1)
        notifier, factory = build_notifier(tmp_path)
        seed(factory, [(wallet, plan)])
        notifier.execute(datetime(2026, 4, 2, 11, 30))
        advance_past(factory, plan.plan_id)

        notifier.execute(datetime(2026, 5, 2, 11, 30))

        assert [message.due_at for message in stored_messages(factory)] == [
            datetime(2026, 4, 2, 12, 0),
            datetime(2026, 5, 2, 12, 0),
        ]

    def test_an_unaffordable_plan_still_queues_its_warning(
        self, build_wallet, build_plan, tmp_path
    ):
        """The courtesy stays unconditional, now that it has somewhere to go.

        It would be easy for the queueing path to grow a balance check - and
        that would be decision 19 broken by the back door, with the notifier
        still holding no wallet but the message never sent anyway.
        """
        wallet = build_wallet(available="0", locked="0")
        plan = build_plan(wallet_id=wallet.wallet_id, anchor=NOON)
        notifier, factory = build_notifier(tmp_path)
        seed(factory, [(wallet, plan)])

        notifier.execute(HALF_PAST_ELEVEN)

        assert len(stored_messages(factory)) == 1

    def test_a_quiet_tick_queues_nothing(self, build_wallet, build_plan, tmp_path):
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, anchor=NOON)
        notifier, factory = build_notifier(tmp_path)
        seed(factory, [(wallet, plan)])

        notifier.execute(datetime(2026, 3, 2, 11, 29))

        assert stored_messages(factory) == []


class TestTheClaimAndTheMessageLandTogether:
    """The invariant this whole phase exists to establish.

    A claimed notice is never raised again - that is the whole of ``PlanNotice``.
    So if the claim could commit while the message was never written, the warning
    would be lost *permanently*: no later tick would know to say it, because the
    only record that it was owed is the claim that already exists.

    Both rows are therefore written in one unit of work, and these tests are what
    say so rather than a comment.
    """

    def test_one_commit_writes_both_rows(self, build_wallet, build_plan, tmp_path):
        """The plain case, from a fresh read of the same database."""
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, anchor=NOON)
        notifier, factory = build_notifier(tmp_path)
        seed(factory, [(wallet, plan)])

        notifier.execute(HALF_PAST_ELEVEN)

        assert len(stored_notices(factory, plan.plan_id)) == 1
        assert len(stored_messages(factory)) == 1

    def test_a_failed_commit_leaves_neither_row(
        self, build_wallet, build_plan, tmp_path
    ):
        """The load-bearing test, and it is deliberately destructive.

        The commit is broken and the database is then read: if the notice and
        the message were in separate transactions, the first would have survived
        and this test would find one row rather than none. Finding neither is
        what proves they are one fact.
        """
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, anchor=NOON)
        _, factory = build_notifier(tmp_path)
        seed(factory, [(wallet, plan)])
        notifier = NotifyUpcomingRuns(
            ExplodingCommitFactory(factory), recipient=RECIPIENT
        )

        with pytest.raises(RuntimeError):
            notifier.execute(HALF_PAST_ELEVEN)

        assert stored_notices(factory, plan.plan_id) == []
        assert stored_messages(factory) == []

    def test_a_losing_claim_does_not_queue_a_second_message(
        self, build_wallet, build_plan, tmp_path
    ):
        """An overlapping tick must not post a duplicate email.

        The losing path rolls back and returns before composing anything, so the
        second tick's message is never even built - which is cheaper and safer
        than building it and relying on the conflict clause to discard it.
        """
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, anchor=NOON)
        notifier, factory = build_notifier(tmp_path)
        seed(factory, [(wallet, plan)])

        first = notifier.execute(HALF_PAST_ELEVEN)
        second = notifier.execute(datetime(2026, 3, 2, 11, 31))

        assert len(first) == 1
        assert second == []
        assert len(stored_messages(factory)) == 1


class TestWhenThereIsNowhereToSend:
    """An install with no notification address is normal, not broken.

    ``recipient=None`` means the warning still happens and is still recorded - it
    simply has nowhere to go. The tick must not fail, or a user who has not set
    up email could not use the scheduler at all.
    """

    def test_the_warning_is_still_raised(self, build_wallet, build_plan, tmp_path):
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, anchor=NOON)
        notifier, factory = build_notifier(tmp_path, recipient=None)
        seed(factory, [(wallet, plan)])

        raised = notifier.execute(HALF_PAST_ELEVEN)

        assert len(raised) == 1
        assert raised[0].plan_id == plan.plan_id

    def test_the_notice_is_still_recorded(self, build_wallet, build_plan, tmp_path):
        """So the warning does not repeat even though it cannot be sent.

        The claim is what makes the warning once-only, and it is taken whether or
        not there is a message to go with it.
        """
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, anchor=NOON)
        notifier, factory = build_notifier(tmp_path, recipient=None)
        seed(factory, [(wallet, plan)])

        notifier.execute(HALF_PAST_ELEVEN)

        assert len(stored_notices(factory, plan.plan_id)) == 1

    def test_nothing_is_queued(self, build_wallet, build_plan, tmp_path):
        """The invariant is about messages, and there is no message here.

        "A message is never orphaned from its claim" is not broken by a claim
        with no message - it is a statement about the thing that does not exist.
        """
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, anchor=NOON)
        notifier, factory = build_notifier(tmp_path, recipient=None)
        seed(factory, [(wallet, plan)])

        notifier.execute(HALF_PAST_ELEVEN)

        assert stored_messages(factory) == []
