from datetime import date, datetime
from decimal import Decimal

import pytest

from app.application.planning.execute_plan_run import ExecutePlanRun
from app.application.planning.run_due_plans import RunDuePlans
from app.domain.money.currency import Currency
from app.domain.money.exception import WalletNotFoundError
from app.domain.money.money import Money
from app.domain.planning.planStatus import PlanStatus
from app.domain.planning.runBlockReason import RunBlockReason
from app.domain.planning.runStatus import RunStatus
from app.infrastructure.persistence.sqlite_unit_of_work import SqliteUnitOfWorkFactory

NGN = Currency.NGN
ANCHOR = datetime(2026, 1, 1)


def build_scheduler(tmp_path, name="scheduler.db"):
    """A tick over its own database, with a per-plan executor builder.

    The builder is the whole of the ownership story at this level: the tick hands
    it a user id and gets back an executor acting as that user, so a plan is
    always run by an executor built for its own owner. There is no single
    long-lived executor here to be granted authority over everybody - which is
    what makes ``RunDuePlans`` free of privilege rather than merely free of the
    word.
    """
    factory = SqliteUnitOfWorkFactory(str(tmp_path / name))
    return (
        RunDuePlans(
            factory,
            lambda user_id: ExecutePlanRun(factory, actor=user_id),
        ),
        factory,
    )


def seed(factory, plans_and_wallets):
    """Persist wallets and plans. ``created_at`` is set explicitly so the tick's
    ordering is deterministic - list_by_status sorts by (created_at, plan_id),
    and plan_id is a random uuid, so leaving created_at to the clock would make
    these tests pass or fail depending on which uuid sorted first."""
    uow = factory.start()
    try:
        for order, (wallet, plan) in enumerate(plans_and_wallets, start=1):
            plan.created_at = datetime(2026, 1, 1, order, 0)
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


def wallet_after(factory, wallet):
    """Read a wallet back as its own owner - which is the only way there is."""
    return read(
        factory, lambda uow: uow.wallets.get_owned(wallet.wallet_id, wallet.user_id)
    )


def plan_after(factory, plan):
    return read(factory, lambda uow: uow.plans.get_owned(plan.plan_id, plan.user_id))


def explode_when_running(scheduler, plan_id, monkeypatch):
    """Make the executor built for a plan raise when it is asked to run it.

    Written against the *builder* rather than against an executor, because there
    is no longer one executor to reach for: the tick mints a fresh one per plan
    from the factory it was given. Wrapping the factory keeps the test's shape
    the same as it always was - one plan in the batch raises, the tick does not
    finish - and it is worth noticing that the indirection is what the ownership
    rule costs here. There is no shared object left whose ``execute`` could be
    monkeypatched once for the whole tick.
    """
    build = scheduler._build_execute_plan_run

    def build_with_a_failing_plan(user_id):
        executor = build(user_id)
        real_execute = executor.execute

        def execute(requested, as_of):
            if requested == plan_id:
                raise RuntimeError("simulated infrastructure failure")
            return real_execute(requested, as_of)

        executor.execute = execute
        return executor

    monkeypatch.setattr(
        scheduler, "_build_execute_plan_run", build_with_a_failing_plan
    )


class TestTicking:
    def test_an_up_to_date_wallet_does_nothing(self, build_wallet, build_plan, tmp_path):
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id)
        scheduler, factory = build_scheduler(tmp_path)
        seed(factory, [(wallet, plan)])

        assert scheduler.execute(datetime(2025, 12, 31)) == []

    def test_every_due_plan_runs(self, build_wallet, build_plan, tmp_path):
        first_wallet = build_wallet(locked="10000")
        second_wallet = build_wallet(locked="10000")
        scheduler, factory = build_scheduler(tmp_path)
        seed(
            factory,
            [
                (first_wallet, build_plan(wallet_id=first_wallet.wallet_id)),
                (second_wallet, build_plan(wallet_id=second_wallet.wallet_id)),
            ],
        )

        runs = scheduler.execute(ANCHOR)

        assert len(runs) == 2
        assert all(run.status is RunStatus.SUCCEEDED for run in runs)

    def test_paused_plans_are_not_considered(self, build_wallet, build_plan, tmp_path):
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, status=PlanStatus.PAUSED)
        scheduler, factory = build_scheduler(tmp_path)
        seed(factory, [(wallet, plan)])

        assert scheduler.execute(ANCHOR) == []

    def test_the_tick_reports_what_it_did(self, build_wallet, build_plan, tmp_path):
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id)
        scheduler, factory = build_scheduler(tmp_path)
        seed(factory, [(wallet, plan)])

        runs = scheduler.execute(ANCHOR)

        assert [run.plan_id for run in runs] == [plan.plan_id]
        assert runs[0].due_at == ANCHOR


class TestClearingABacklog:
    def test_a_tick_clears_one_occurrence_per_plan(
        self, build_wallet, build_plan, tmp_path
    ):
        """Four months behind, checked in April: the tick pays January and stops."""
        wallet = build_wallet(locked="100000")
        plan = build_plan(wallet_id=wallet.wallet_id)
        scheduler, factory = build_scheduler(tmp_path)
        seed(factory, [(wallet, plan)])

        runs = scheduler.execute(datetime(2026, 4, 15))

        assert [run.due_at for run in runs] == [ANCHOR]
        assert plan_after(factory, plan).completed_runs == 1

    def test_successive_ticks_work_through_the_backlog(
        self, build_wallet, build_plan, tmp_path
    ):
        wallet = build_wallet(locked="100000")
        plan = build_plan(wallet_id=wallet.wallet_id)
        scheduler, factory = build_scheduler(tmp_path)
        seed(factory, [(wallet, plan)])

        seen = [
            [run.due_at for run in scheduler.execute(datetime(2026, 4, 15))]
            for _ in range(4)
        ]

        assert seen == [
            [datetime(2026, 1, 1)],
            [datetime(2026, 2, 1)],
            [datetime(2026, 3, 1)],
            [datetime(2026, 4, 1)],
        ]

    def test_a_tick_after_the_backlog_is_cleared_does_nothing(
        self, build_wallet, build_plan, tmp_path
    ):
        wallet = build_wallet(locked="100000")
        plan = build_plan(wallet_id=wallet.wallet_id)
        scheduler, factory = build_scheduler(tmp_path)
        seed(factory, [(wallet, plan)])
        for _ in range(4):
            scheduler.execute(datetime(2026, 4, 15))

        assert scheduler.execute(datetime(2026, 4, 15)) == []


class TestBlockedPlansStopBeingRetried:
    def test_a_blocked_plan_is_paused_and_the_next_tick_skips_it(
        self, build_wallet, build_plan, tmp_path
    ):
        """A plan that cannot be paid stalls visibly instead of failing every tick."""
        wallet = build_wallet(locked="100")
        plan = build_plan(wallet_id=wallet.wallet_id)
        scheduler, factory = build_scheduler(tmp_path)
        seed(factory, [(wallet, plan)])

        first = scheduler.execute(ANCHOR)
        second = scheduler.execute(ANCHOR)

        assert first[0].reason is RunBlockReason.INSUFFICIENT_BALANCE
        assert second == []
        assert plan_after(factory, plan).status is PlanStatus.PAUSED


class TestTheTransactionBoundary:
    """One tick is a loop, not a transaction.

    Written to state the rule from run_due_plans.py as an executable claim: if
    the whole tick shared a transaction, a plan that raised would roll back the
    plans that already succeeded - money that really moved, forgotten by the
    ledger.
    """

    def test_a_raising_plan_does_not_undo_an_already_committed_one(
        self, build_wallet, build_plan, tmp_path, monkeypatch
    ):
        good_wallet = build_wallet(locked="10000")
        bad_wallet = build_wallet(locked="10000")
        good = build_plan(wallet_id=good_wallet.wallet_id)
        bad = build_plan(wallet_id=bad_wallet.wallet_id)
        scheduler, factory = build_scheduler(tmp_path)
        # good is seeded first, so list_by_status returns it first.
        seed(factory, [(good_wallet, good), (bad_wallet, bad)])

        explode_when_running(scheduler, bad.plan_id, monkeypatch)

        with pytest.raises(RuntimeError):
            scheduler.execute(ANCHOR)

        # The good plan's payout is durable, despite the tick never finishing.
        assert wallet_after(factory, good_wallet).locked_balance == Money(
            Decimal("8000"), NGN
        )
        assert plan_after(factory, good).completed_runs == 1

    def test_the_failed_plan_left_nothing_behind(
        self, build_wallet, build_plan, tmp_path, monkeypatch
    ):
        good_wallet = build_wallet(locked="10000")
        bad_wallet = build_wallet(locked="10000")
        good = build_plan(wallet_id=good_wallet.wallet_id)
        bad = build_plan(wallet_id=bad_wallet.wallet_id)
        scheduler, factory = build_scheduler(tmp_path)
        seed(factory, [(good_wallet, good), (bad_wallet, bad)])

        explode_when_running(scheduler, bad.plan_id, monkeypatch)

        with pytest.raises(RuntimeError):
            scheduler.execute(ANCHOR)

        assert wallet_after(factory, bad_wallet).locked_balance == Money(
            Decimal("10000"), NGN
        )
        assert plan_after(factory, bad).completed_runs == 0


class TestTheSchedulerIsNotAPrivilegedActor:
    """The load-bearing test for the whole phase, at the layer where it is hardest.

    A tick serves every user in the installation, so it is the one caller that
    cannot be built for a single actor - and the tempting answer, the one most
    systems take, is to give it an authority of its own: a system user, a skipped
    check, a flag that says "this read is internal". Every one of those is a
    bypass, and a bypass is a thing that exists at runtime whether or not anyone
    currently calls it.

    So the design gives the tick no authority at all. It reads plans across the
    installation - which is *discovery*, and returns each plan with its owner -
    and then builds one executor per plan, acting as that plan's user. This test
    is what holds that in place: two users, both plans due, one tick. It fails if
    the executor stops being built per plan, and it fails if any read inside the
    run stops being scoped - because a mis-scoped read is not a wrong answer
    here, it is a plan that cannot be found.
    """

    def test_two_users_plans_both_run_from_one_tick(
        self, build_wallet, build_plan, tmp_path, actor, stranger
    ):
        first_wallet = build_wallet(locked="10000", user_id=actor)
        second_wallet = build_wallet(locked="10000", user_id=stranger)
        first = build_plan(wallet_id=first_wallet.wallet_id, user_id=actor)
        second = build_plan(wallet_id=second_wallet.wallet_id, user_id=stranger)
        scheduler, factory = build_scheduler(tmp_path)
        seed(factory, [(first_wallet, first), (second_wallet, second)])

        runs = scheduler.execute(ANCHOR)

        assert {run.plan_id for run in runs} == {first.plan_id, second.plan_id}
        assert all(run.status is RunStatus.SUCCEEDED for run in runs)
        # Each wallet paid out of its own balance: the second plan's run read the
        # second user's wallet, not the first's, and there was never an executor
        # that could have reached either one without naming its owner.
        assert wallet_after(factory, first_wallet).locked_balance == Money(
            Decimal("8000"), NGN
        )
        assert wallet_after(factory, second_wallet).locked_balance == Money(
            Decimal("8000"), NGN
        )

    def test_a_plan_whose_owner_is_not_its_wallets_owner_is_not_run(
        self, build_wallet, build_plan, tmp_path, actor, stranger
    ):
        """The denormalized ``user_id`` is checked by the read, not trusted.

        ``savings_plans.user_id`` duplicates ``wallets.user_id`` deliberately, and
        a duplicate can disagree with its original. This is the state where it
        does: the plan claims a different owner from the wallet it draws on. The
        tick builds an executor as the plan's owner, that executor reads the
        wallet scoped to itself, and the wallet is not there.

        What it *does* mean is that no money moves and no run is recorded, which
        is the safe half. What it does not mean is that anything repairs the
        disagreement - there is no code that would. The value of asserting it is
        that the failure mode is loud and attributable rather than a run that
        pays out of a wallet belonging to somebody the plan does not name.
        """
        wallet = build_wallet(locked="10000", user_id=actor)
        plan = build_plan(wallet_id=wallet.wallet_id, user_id=stranger)
        scheduler, factory = build_scheduler(tmp_path)
        seed(factory, [(wallet, plan)])

        with pytest.raises(WalletNotFoundError):
            scheduler.execute(ANCHOR)

        assert wallet_after(factory, wallet).locked_balance == Money(
            Decimal("10000"), NGN
        )
