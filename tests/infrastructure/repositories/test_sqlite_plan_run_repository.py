from datetime import date

import pytest

from app.domain.planning.planRun import PlanRun
from app.domain.planning.runBlockReason import RunBlockReason
from app.domain.planning.runStatus import RunStatus
from app.infrastructure.persistence.sqlite_unit_of_work import open_sqlite_connection
from app.infrastructure.repositories.sqlite_plan_run_repository import (
    SqlitePlanRunRepository,
)
from app.infrastructure.repositories.sqlite_savings_plan_repository import (
    SqliteSavingsPlanRepository,
)
from app.infrastructure.repositories.sqlite_wallet_repository import (
    SqliteWalletRepository,
)


def build_repository(wallet, plan):
    """A run store on a fresh in-memory database, with its parents seeded.

    plan_runs.plan_id carries a foreign key, so the wallet and the plan both
    have to exist first - a run cannot outlive the plan it belongs to.
    """
    connection = open_sqlite_connection(":memory:")
    SqliteWalletRepository(connection).save(wallet)
    SqliteSavingsPlanRepository(connection).save(plan)
    return SqlitePlanRunRepository(connection)


def test_round_trips_a_successful_run(build_wallet, build_plan):
    wallet = build_wallet()
    plan = build_plan(wallet_id=wallet.wallet_id)
    repository = build_repository(wallet, plan)
    run = PlanRun(plan_id=plan.plan_id, due_at=date(2026, 4, 1), status=RunStatus.SUCCEEDED)

    repository.save(run)

    stored = repository.list_by_plan_id(plan.plan_id)[0]
    assert stored.plan_id == plan.plan_id
    assert stored.due_at == date(2026, 4, 1)
    assert stored.status is RunStatus.SUCCEEDED
    assert stored.reason is None
    assert stored.recorded_at == run.recorded_at


def test_round_trips_a_blocked_run_with_its_reason(build_wallet, build_plan):
    wallet = build_wallet()
    plan = build_plan(wallet_id=wallet.wallet_id)
    repository = build_repository(wallet, plan)

    repository.save(
        PlanRun(
            plan_id=plan.plan_id,
            due_at=date(2026, 4, 1),
            status=RunStatus.BLOCKED,
            reason=RunBlockReason.INSUFFICIENT_BALANCE,
        )
    )

    stored = repository.list_by_plan_id(plan.plan_id)[0]
    assert stored.status is RunStatus.BLOCKED
    assert stored.reason is RunBlockReason.INSUFFICIENT_BALANCE


@pytest.mark.parametrize("reason", list(RunBlockReason))
def test_every_block_reason_round_trips(reason, build_wallet, build_plan):
    wallet = build_wallet()
    plan = build_plan(wallet_id=wallet.wallet_id)
    repository = build_repository(wallet, plan)

    repository.save(
        PlanRun(
            plan_id=plan.plan_id,
            due_at=date(2026, 4, 1),
            status=RunStatus.BLOCKED,
            reason=reason,
        )
    )

    assert repository.list_by_plan_id(plan.plan_id)[0].reason is reason


def test_runs_come_back_in_occurrence_order(build_wallet, build_plan):
    wallet = build_wallet()
    plan = build_plan(wallet_id=wallet.wallet_id)
    repository = build_repository(wallet, plan)
    for day in (date(2026, 4, 1), date(2026, 1, 1), date(2026, 3, 1), date(2026, 2, 1)):
        repository.save(
            PlanRun(plan_id=plan.plan_id, due_at=day, status=RunStatus.SUCCEEDED)
        )

    stored = repository.list_by_plan_id(plan.plan_id)

    assert [run.due_at for run in stored] == [
        date(2026, 1, 1),
        date(2026, 2, 1),
        date(2026, 3, 1),
        date(2026, 4, 1),
    ]


def test_a_plan_that_has_never_run_has_no_history(build_wallet, build_plan):
    wallet = build_wallet()
    plan = build_plan(wallet_id=wallet.wallet_id)
    repository = build_repository(wallet, plan)

    assert repository.list_by_plan_id(plan.plan_id) == []


def test_only_that_plans_runs_come_back(build_wallet, build_plan):
    wallet = build_wallet()
    plan = build_plan(wallet_id=wallet.wallet_id)
    other_plan = build_plan(wallet_id=wallet.wallet_id)
    repository = build_repository(wallet, plan)
    SqliteSavingsPlanRepository(repository._connection).save(other_plan)

    repository.save(PlanRun(plan_id=plan.plan_id, due_at=date(2026, 4, 1), status=RunStatus.SUCCEEDED))
    repository.save(PlanRun(plan_id=other_plan.plan_id, due_at=date(2026, 4, 1), status=RunStatus.SUCCEEDED))

    assert [run.plan_id for run in repository.list_by_plan_id(plan.plan_id)] == [plan.plan_id]


class TestTheNaturalKey:
    """One plan has at most one run per occurrence - the reason the store is idempotent."""

    def test_re_saving_the_same_occurrence_updates_rather_than_appends(
        self, build_wallet, build_plan
    ):
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        repository = build_repository(wallet, plan)
        due_at = date(2026, 4, 1)

        repository.save(PlanRun(plan_id=plan.plan_id, due_at=due_at, status=RunStatus.SUCCEEDED))
        repository.save(PlanRun(plan_id=plan.plan_id, due_at=due_at, status=RunStatus.SUCCEEDED))

        assert len(repository.list_by_plan_id(plan.plan_id)) == 1

    def test_a_blocked_run_retried_successfully_leaves_one_coherent_row(
        self, build_wallet, build_plan
    ):
        """The story this table exists to tell: blocked in April, paid in May, one row."""
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        repository = build_repository(wallet, plan)
        due_at = date(2026, 4, 1)

        repository.save(
            PlanRun(
                plan_id=plan.plan_id,
                due_at=due_at,
                status=RunStatus.BLOCKED,
                reason=RunBlockReason.INSUFFICIENT_BALANCE,
            )
        )
        repository.save(
            PlanRun(plan_id=plan.plan_id, due_at=due_at, status=RunStatus.SUCCEEDED)
        )

        stored = repository.list_by_plan_id(plan.plan_id)
        assert len(stored) == 1
        assert stored[0].status is RunStatus.SUCCEEDED
        assert stored[0].reason is None

    def test_different_occurrences_of_one_plan_are_separate_rows(
        self, build_wallet, build_plan
    ):
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        repository = build_repository(wallet, plan)

        repository.save(PlanRun(plan_id=plan.plan_id, due_at=date(2026, 1, 1), status=RunStatus.SUCCEEDED))
        repository.save(PlanRun(plan_id=plan.plan_id, due_at=date(2026, 2, 1), status=RunStatus.SUCCEEDED))

        assert len(repository.list_by_plan_id(plan.plan_id)) == 2
