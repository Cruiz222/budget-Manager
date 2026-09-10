from datetime import date, datetime
from decimal import Decimal

import pytest

from app.application.planning.execute_plan_run import ExecutePlanRun
from app.application.planning.run_due_plans import RunDuePlans
from app.domain.money.currency import Currency
from app.domain.money.money import Money
from app.domain.planning.planStatus import PlanStatus
from app.domain.planning.runBlockReason import RunBlockReason
from app.domain.planning.runStatus import RunStatus
from app.infrastructure.persistence.sqlite_unit_of_work import SqliteUnitOfWorkFactory

NGN = Currency.NGN
ANCHOR = datetime(2026, 1, 1)


def build_scheduler(tmp_path, name="scheduler.db"):
    factory = SqliteUnitOfWorkFactory(str(tmp_path / name))
    return RunDuePlans(factory, ExecutePlanRun(factory)), factory


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


def wallet_after(factory, wallet_id):
    return read(factory, lambda uow: uow.wallets.get_by_id(wallet_id))


def plan_after(factory, plan_id):
    return read(factory, lambda uow: uow.plans.get_by_id(plan_id))


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
        assert plan_after(factory, plan.plan_id).completed_runs == 1

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
        assert plan_after(factory, plan.plan_id).status is PlanStatus.PAUSED


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

        real_execute = scheduler._execute_plan_run.execute

        def explode(plan_id, as_of):
            if plan_id == bad.plan_id:
                raise RuntimeError("simulated infrastructure failure")
            return real_execute(plan_id, as_of)

        monkeypatch.setattr(scheduler._execute_plan_run, "execute", explode)

        with pytest.raises(RuntimeError):
            scheduler.execute(ANCHOR)

        # The good plan's payout is durable, despite the tick never finishing.
        assert wallet_after(factory, good_wallet.wallet_id).locked_balance == Money(
            Decimal("8000"), NGN
        )
        assert plan_after(factory, good.plan_id).completed_runs == 1

    def test_the_failed_plan_left_nothing_behind(
        self, build_wallet, build_plan, tmp_path, monkeypatch
    ):
        good_wallet = build_wallet(locked="10000")
        bad_wallet = build_wallet(locked="10000")
        good = build_plan(wallet_id=good_wallet.wallet_id)
        bad = build_plan(wallet_id=bad_wallet.wallet_id)
        scheduler, factory = build_scheduler(tmp_path)
        seed(factory, [(good_wallet, good), (bad_wallet, bad)])

        real_execute = scheduler._execute_plan_run.execute

        def explode(plan_id, as_of):
            if plan_id == bad.plan_id:
                raise RuntimeError("simulated infrastructure failure")
            return real_execute(plan_id, as_of)

        monkeypatch.setattr(scheduler._execute_plan_run, "execute", explode)

        with pytest.raises(RuntimeError):
            scheduler.execute(ANCHOR)

        assert wallet_after(factory, bad_wallet.wallet_id).locked_balance == Money(
            Decimal("10000"), NGN
        )
        assert plan_after(factory, bad.plan_id).completed_runs == 0
