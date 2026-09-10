from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.money.currency import Currency
from app.domain.money.money import Money
from app.domain.planning.cadence import Cadence
from app.domain.planning.exception import SavingsPlanNotFoundError
from app.domain.planning.instruction import Instruction
from app.domain.planning.plannedAction import PlannedAction
from app.domain.planning.planSource import PlanSource
from app.domain.planning.planStatus import PlanStatus
from app.infrastructure.persistence.sqlite_unit_of_work import open_sqlite_connection
from app.infrastructure.repositories.sqlite_savings_plan_repository import (
    SqliteSavingsPlanRepository,
)
from app.infrastructure.repositories.sqlite_wallet_repository import (
    SqliteWalletRepository,
)

NGN = Currency.NGN


def build_repository(wallet):
    """A plan store on a fresh in-memory database, with the wallet seeded.

    The wallet row has to exist first: savings_plans.wallet_id carries a foreign
    key, and PRAGMA foreign_keys is on, so an orphan plan is refused by SQLite
    rather than silently stored.
    """
    connection = open_sqlite_connection(":memory:")
    SqliteWalletRepository(connection).save(wallet)
    return SqliteSavingsPlanRepository(connection)


def release(amount):
    return Instruction(
        action=PlannedAction.RELEASE,
        amount=Money(Decimal(amount), NGN),
        label="emergency",
    )


def payout(amount, label="salary"):
    from app.domain.money.destination import Destination
    from app.domain.money.destinationKind import DestinationKind

    return Instruction(
        action=PlannedAction.PAYOUT,
        amount=Money(Decimal(amount), NGN),
        label=label,
        destination=Destination(
            kind=DestinationKind.BANK_ACCOUNT,
            identifier="0123456789",
            name="Chinedu Okafor",
            details={"bank_code": "058"},
        ),
    )


def test_round_trips_a_plan(build_wallet, build_plan):
    wallet = build_wallet()
    plan = build_plan(
        wallet_id=wallet.wallet_id,
        anchor=date(2026, 1, 1),
        ends_on=date(2026, 12, 31),
    )
    repository = build_repository(wallet)

    repository.save(plan)

    stored = repository.get_by_id(plan.plan_id)
    assert stored.plan_id == plan.plan_id
    assert stored.wallet_id == wallet.wallet_id
    assert stored.source is PlanSource.LOCKED
    assert stored.status is PlanStatus.ACTIVE
    assert stored.completed_runs == 0
    assert stored.ends_on == date(2026, 12, 31)
    assert stored.created_at == plan.created_at


def test_instructions_survive_with_their_destinations(build_wallet, build_plan):
    wallet = build_wallet()
    plan = build_plan(wallet_id=wallet.wallet_id, instructions=(payout("2000"),))
    repository = build_repository(wallet)

    repository.save(plan)

    stored = repository.get_by_id(plan.plan_id)
    assert len(stored.instructions) == 1
    instruction = stored.instructions[0]
    assert instruction.action is PlannedAction.PAYOUT
    assert instruction.amount == Money(Decimal("2000"), NGN)
    assert instruction.label == "salary"
    assert instruction.destination.identifier == "0123456789"
    assert instruction.destination.detail("bank_code") == "058"


def test_a_release_instruction_survives_without_a_destination(build_wallet, build_plan):
    wallet = build_wallet()
    plan = build_plan(wallet_id=wallet.wallet_id, instructions=(release("1500"),))
    repository = build_repository(wallet)

    repository.save(plan)

    stored = repository.get_by_id(plan.plan_id)
    assert stored.instructions[0].action is PlannedAction.RELEASE
    assert stored.instructions[0].destination is None


def test_several_instructions_keep_their_order(build_wallet, build_plan):
    wallet = build_wallet()
    plan = build_plan(
        wallet_id=wallet.wallet_id,
        instructions=(
            payout("2000", "salary"),
            payout("1500", "rent"),
            payout("500", "data"),
        ),
    )
    repository = build_repository(wallet)

    repository.save(plan)

    stored = repository.get_by_id(plan.plan_id)
    assert [instruction.label for instruction in stored.instructions] == [
        "salary",
        "rent",
        "data",
    ]
    assert stored.total_to_move == Money(Decimal("4000"), NGN)


def test_the_schedule_round_trips(build_wallet, build_plan):
    wallet = build_wallet()
    plan = build_plan(
        wallet_id=wallet.wallet_id,
        cadence=Cadence.WEEKLY,
        anchor=date(2026, 5, 7),
    )
    repository = build_repository(wallet)

    repository.save(plan)

    stored = repository.get_by_id(plan.plan_id)
    assert stored.schedule.cadence is Cadence.WEEKLY
    assert stored.schedule.anchor == date(2026, 5, 7)


def test_the_anchor_survives_so_drift_protection_survives(build_wallet, build_plan):
    """A reloaded plan must recover the 31st exactly as the in-memory one does."""
    wallet = build_wallet()
    plan = build_plan(wallet_id=wallet.wallet_id, anchor=date(2026, 1, 31))
    repository = build_repository(wallet)
    repository.save(plan)

    stored = repository.get_by_id(plan.plan_id)
    seen = [stored.next_due_at]
    for _ in range(3):
        stored.record_run()
        seen.append(stored.next_due_at)

    assert seen == [
        date(2026, 1, 31),
        date(2026, 2, 28),
        date(2026, 3, 31),
        date(2026, 4, 30),
    ]


def test_an_open_ended_plan_round_trips_ends_on_as_none(build_wallet, build_plan):
    wallet = build_wallet()
    plan = build_plan(wallet_id=wallet.wallet_id)
    repository = build_repository(wallet)

    repository.save(plan)

    assert repository.get_by_id(plan.plan_id).ends_on is None


def test_the_run_count_survives_so_a_reloaded_plan_is_due_where_it_left_off(
    build_wallet, build_plan
):
    wallet = build_wallet()
    plan = build_plan(wallet_id=wallet.wallet_id, anchor=date(2026, 1, 1), completed_runs=3)
    repository = build_repository(wallet)

    repository.save(plan)

    stored = repository.get_by_id(plan.plan_id)
    assert stored.completed_runs == 3
    assert stored.next_due_at == date(2026, 4, 1)


def test_get_by_id_of_a_missing_plan_raises(build_wallet):
    wallet = build_wallet()
    repository = build_repository(wallet)

    with pytest.raises(SavingsPlanNotFoundError):
        repository.get_by_id(uuid4())


def test_get_by_wallet_id_returns_only_that_wallets_plans(build_wallet, build_plan):
    wallet = build_wallet()
    other_wallet = build_wallet()
    repository = build_repository(wallet)
    SqliteWalletRepository(repository._connection).save(other_wallet)

    first = build_plan(wallet_id=wallet.wallet_id)
    second = build_plan(wallet_id=wallet.wallet_id)
    stranger = build_plan(wallet_id=other_wallet.wallet_id)
    for plan in (first, second, stranger):
        repository.save(plan)

    found = repository.get_by_wallet_id(wallet.wallet_id)

    assert {plan.plan_id for plan in found} == {first.plan_id, second.plan_id}


def test_get_by_wallet_id_is_empty_for_a_wallet_without_plans(build_wallet):
    wallet = build_wallet()
    repository = build_repository(wallet)

    assert repository.get_by_wallet_id(wallet.wallet_id) == []


def test_saving_a_changed_plan_updates_the_same_row(build_wallet, build_plan):
    wallet = build_wallet()
    plan = build_plan(wallet_id=wallet.wallet_id)
    repository = build_repository(wallet)
    repository.save(plan)

    plan.record_run()
    plan.pause()
    repository.save(plan)

    stored = repository.get_by_id(plan.plan_id)
    assert stored.completed_runs == 1
    assert stored.status is PlanStatus.PAUSED
    count = repository._connection.execute(
        "SELECT COUNT(*) FROM savings_plans"
    ).fetchone()[0]
    assert count == 1


def test_list_by_status_returns_only_plans_in_that_status(build_wallet, build_plan):
    wallet = build_wallet()
    repository = build_repository(wallet)
    active = build_plan(wallet_id=wallet.wallet_id)
    paused = build_plan(wallet_id=wallet.wallet_id, status=PlanStatus.PAUSED)
    cancelled = build_plan(wallet_id=wallet.wallet_id, status=PlanStatus.CANCELLED)
    for plan in (active, paused, cancelled):
        repository.save(plan)

    found = repository.list_by_status(PlanStatus.ACTIVE)

    assert [plan.plan_id for plan in found] == [active.plan_id]


def test_list_by_status_is_empty_when_nothing_matches(build_wallet, build_plan):
    wallet = build_wallet()
    repository = build_repository(wallet)
    repository.save(build_plan(wallet_id=wallet.wallet_id))

    assert repository.list_by_status(PlanStatus.PAUSED) == []


def test_two_plans_on_one_wallet_both_survive(build_wallet, build_plan):
    wallet = build_wallet()
    repository = build_repository(wallet)
    monthly = build_plan(wallet_id=wallet.wallet_id, cadence=Cadence.MONTHLY)
    weekly = build_plan(wallet_id=wallet.wallet_id, cadence=Cadence.WEEKLY)

    repository.save(monthly)
    repository.save(weekly)

    stored = repository.get_by_wallet_id(wallet.wallet_id)
    assert {plan.schedule.cadence for plan in stored} == {Cadence.MONTHLY, Cadence.WEEKLY}
