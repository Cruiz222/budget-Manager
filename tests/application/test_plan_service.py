from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.plan_service import PlanService
from app.domain.money.currency import Currency
from app.domain.money.destination import Destination
from app.domain.money.destinationKind import DestinationKind
from app.domain.money.exception import CurrencyMismatchError, WalletNotFoundError
from app.domain.money.money import Money
from app.domain.planning.cadence import Cadence
from app.domain.planning.exception import (
    EmptyPlanInstructionsError,
    IrreversibleReleasePlanError,
    PlanAlreadyFinishedError,
    PlanNotActiveError,
    PlanNotPausedError,
    SavingsPlanNotFoundError,
)
from app.domain.planning.instruction import Instruction
from app.domain.planning.plannedAction import PlannedAction
from app.domain.planning.planSource import PlanSource
from app.domain.planning.planStatus import PlanStatus
from app.domain.planning.schedule import Schedule
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)

NGN = Currency.NGN
USD = Currency.USD
ANCHOR = datetime(2026, 1, 1)

DESTINATION = Destination(
    kind=DestinationKind.BANK_ACCOUNT,
    identifier="0123456789",
    name="Chinedu Okafor",
    details={"bank_code": "058"},
)


def build_service(tmp_path, name="plan_service.db"):
    factory = SqliteUnitOfWorkFactory(str(tmp_path / name))
    return PlanService(factory), factory


def save_wallet(factory, wallet):
    uow = factory.start()
    try:
        uow.wallets.save(wallet)
        uow.commit()
    except BaseException:
        uow.rollback()
        raise
    return wallet


def payout(amount: str, currency: Currency = NGN, label: str = "salary") -> Instruction:
    return Instruction(
        action=PlannedAction.PAYOUT,
        amount=Money(Decimal(amount), currency),
        label=label,
        destination=DESTINATION,
    )


def release(amount: str, currency: Currency = NGN, label: str = "unlock") -> Instruction:
    return Instruction(
        action=PlannedAction.RELEASE,
        amount=Money(Decimal(amount), currency),
        label=label,
    )


def create(service, wallet_id, instructions=None, **overrides):
    return service.create_plan(
        wallet_id=wallet_id,
        name=overrides.pop("name", "Salary 2026"),
        source=overrides.pop("source", PlanSource.LOCKED),
        schedule=overrides.pop("schedule", Schedule(Cadence.MONTHLY, ANCHOR)),
        instructions=instructions if instructions is not None else (payout("2000"),),
        **overrides,
    )


class TestCreatingAPlan:
    def test_creates_and_persists(self, tmp_path, build_wallet):
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet())

        plan = create(service, wallet.wallet_id, name="Salary 2026")

        assert service.get_plan(plan.plan_id).name == "Salary 2026"

    def test_a_plan_may_exist_before_it_is_affordable(self, tmp_path, build_wallet):
        """An empty wallet does not stop a plan being created.

        This is the product rule written as an executable claim. Saving towards
        something means the plan comes first and the money arrives later; a
        creator that checked the balance would make the feature impossible to
        use the way it is meant to be used. The shortfall is discovered at run
        time, and recorded as a blocked run.
        """
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet(available="0", locked="0"))

        plan = create(service, wallet.wallet_id)

        assert plan.total_to_move == Money(Decimal("2000"), NGN)
        assert service.get_plan(plan.plan_id).status is PlanStatus.ACTIVE

    def test_a_release_plan_records_its_end_date(self, tmp_path, build_wallet):
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet())

        plan = create(
            service,
            wallet.wallet_id,
            instructions=(release("1000"),),
            ends_on=date(2027, 1, 1),
        )

        assert service.get_plan(plan.plan_id).ends_on == date(2027, 1, 1)


class TestTheCurrencyRule:
    """The one invariant neither aggregate can hold, because it spans both.

    It lives here, in the use case, for the reason SavingsPlan's docstring gives:
    the plan cannot see its wallet and the wallet has never heard of a plan, so
    the only place both are loaded is the place that can check.
    """

    def test_a_plan_in_another_currency_is_refused(self, tmp_path, build_wallet):
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet(currency=USD))

        with pytest.raises(CurrencyMismatchError):
            create(service, wallet.wallet_id, instructions=(payout("2000", NGN),))

    def test_a_refused_plan_leaves_nothing_behind(self, tmp_path, build_wallet):
        """The check runs *before* the save, so a refusal writes nothing.

        Worth pinning down: the difference between "refused" and "saved and then
        refused" is invisible until something reads the store, and by then the
        plan has already been ticked over.
        """
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet(currency=USD))

        with pytest.raises(CurrencyMismatchError):
            create(service, wallet.wallet_id, instructions=(payout("2000", NGN),))

        assert service.plans_for_wallet(wallet.wallet_id) == []

    def test_a_plan_in_the_wallets_currency_is_accepted(self, tmp_path, build_wallet):
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet(currency=USD))

        plan = create(service, wallet.wallet_id, instructions=(payout("2000", USD),))

        assert plan.total_to_move.currency is USD


class TestReading:
    def test_an_unknown_plan_is_an_error(self, tmp_path):
        service, _ = build_service(tmp_path)

        with pytest.raises(SavingsPlanNotFoundError):
            service.get_plan(uuid4())

    def test_a_wallet_with_no_plans_lists_none(self, tmp_path, build_wallet):
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet())

        assert service.plans_for_wallet(wallet.wallet_id) == []

    def test_an_unknown_wallet_is_an_error(self, tmp_path):
        """Distinguishable from "a wallet with no plans" - the same split the
        transaction queries make, and for the same reason: one of these means
        the plan is not there, the other means the wallet is not."""
        service, _ = build_service(tmp_path)

        with pytest.raises(WalletNotFoundError):
            service.plans_for_wallet(uuid4())

    def test_a_wallets_plans_all_come_back(self, tmp_path, build_wallet):
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet())
        first = create(service, wallet.wallet_id, name="rent")
        second = create(service, wallet.wallet_id, name="salary")

        plans = service.plans_for_wallet(wallet.wallet_id)

        # Compared as a set, not a list. The contract says oldest first, but two
        # plans created microseconds apart have created_at values that are not
        # reliably ordered by the clock - asserting the order here would make
        # this test a coin toss. Ordering belongs to the repository's own test.
        assert {plan.plan_id for plan in plans} == {first.plan_id, second.plan_id}

    def test_a_plan_with_no_runs_has_empty_history(self, tmp_path, build_wallet):
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet())
        plan = create(service, wallet.wallet_id)

        assert service.runs_for_plan(plan.plan_id) == []

    def test_runs_of_an_unknown_plan_is_an_error(self, tmp_path):
        service, _ = build_service(tmp_path)

        with pytest.raises(SavingsPlanNotFoundError):
            service.runs_for_plan(uuid4())


class TestSteering:
    def test_pause_then_resume_round_trip(self, tmp_path, build_wallet):
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet())
        plan = create(service, wallet.wallet_id)

        assert service.pause_plan(plan.plan_id).status is PlanStatus.PAUSED
        assert service.resume_plan(plan.plan_id).status is PlanStatus.ACTIVE

    def test_pausing_twice_is_refused(self, tmp_path, build_wallet):
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet())
        plan = create(service, wallet.wallet_id)
        service.pause_plan(plan.plan_id)

        with pytest.raises(PlanNotActiveError):
            service.pause_plan(plan.plan_id)

    def test_resuming_an_active_plan_is_refused(self, tmp_path, build_wallet):
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet())
        plan = create(service, wallet.wallet_id)

        with pytest.raises(PlanNotPausedError):
            service.resume_plan(plan.plan_id)

    def test_cancel_ends_the_plan(self, tmp_path, build_wallet):
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet())
        plan = create(service, wallet.wallet_id)

        assert service.cancel_plan(plan.plan_id).status is PlanStatus.CANCELLED

    def test_cancelling_a_cancelled_plan_is_refused(self, tmp_path, build_wallet):
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet())
        plan = create(service, wallet.wallet_id)
        service.cancel_plan(plan.plan_id)

        with pytest.raises(PlanAlreadyFinishedError):
            service.cancel_plan(plan.plan_id)

    def test_a_release_plan_cannot_be_cancelled(self, tmp_path, build_wallet):
        """The commitment device, reached through the service.

        The rejection is deliberately not translated here. The use case adds
        nothing to the rule, and inventing a friendlier message at this layer
        would put a second description of the rule in a second place.
        """
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet())
        plan = create(
            service,
            wallet.wallet_id,
            instructions=(release("1000"),),
            ends_on=date(2027, 1, 1),
        )

        with pytest.raises(IrreversibleReleasePlanError):
            service.cancel_plan(plan.plan_id)

        assert service.get_plan(plan.plan_id).status is PlanStatus.ACTIVE


class TestEditing:
    def test_editing_replaces_the_lines(self, tmp_path, build_wallet):
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet())
        plan = create(service, wallet.wallet_id, name="salary")

        updated = service.edit_instructions(plan.plan_id, (payout("3000", label="raise"),))

        assert updated.total_to_move == Money(Decimal("3000"), NGN)
        assert service.get_plan(plan.plan_id).total_to_move == Money(Decimal("3000"), NGN)

    def test_editing_a_release_plan_is_refused(self, tmp_path, build_wallet):
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet())
        plan = create(
            service,
            wallet.wallet_id,
            instructions=(release("1000"),),
            ends_on=date(2027, 1, 1),
        )

        with pytest.raises(IrreversibleReleasePlanError):
            service.edit_instructions(plan.plan_id, (payout("1000"),))

    def test_a_rejected_edit_leaves_the_original_lines_in_place(
        self, tmp_path, build_wallet
    ):
        """Validate-then-assign, checked from the outside.

        The aggregate checks the candidate before it assigns, so a refused edit
        is not a half-applied one. This is the observable consequence of that
        ordering, which is why it is worth a test rather than a comment.
        """
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet())
        plan = create(service, wallet.wallet_id)

        with pytest.raises(EmptyPlanInstructionsError):
            service.edit_instructions(plan.plan_id, ())

        assert service.get_plan(plan.plan_id).total_to_move == Money(
            Decimal("2000"), NGN
        )

    def test_editing_a_cancelled_plan_is_refused(self, tmp_path, build_wallet):
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet())
        plan = create(service, wallet.wallet_id)
        service.cancel_plan(plan.plan_id)

        with pytest.raises(PlanNotActiveError):
            service.edit_instructions(plan.plan_id, (payout("500"),))


def test_the_services_own_reads_see_committed_writes(tmp_path, build_wallet):
    """A smoke test for the transaction boundaries, stated as a claim.

    Every PlanService method opens and closes its own unit. If a write were left
    uncommitted, the very next read would not see it - which is exactly the bug
    this shape of test catches, without counting connections.
    """
    service, factory = build_service(tmp_path)
    wallet = save_wallet(factory, build_wallet())

    plan = create(service, wallet.wallet_id, name="salary")

    assert [one.name for one in service.plans_for_wallet(wallet.wallet_id)] == ["salary"]
    assert service.get_plan(plan.plan_id).wallet_id == wallet.wallet_id
