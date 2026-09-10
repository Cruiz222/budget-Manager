from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.domain.money.currency import Currency
from app.domain.money.destination import Destination
from app.domain.money.destinationKind import DestinationKind
from app.domain.money.money import Money
from app.domain.planning.cadence import Cadence
from app.domain.planning.exception import (
    EmptyPlanInstructionsError,
    InvalidCompletedRunsError,
    InvalidPlanEndDateError,
    InvalidPlanIDError,
    InvalidPlanInstructionsError,
    InvalidPlanScheduleError,
    InvalidPlanSourceError,
    InvalidPlanStatusError,
    InvalidPlanWalletIDError,
    MixedInstructionCurrenciesError,
    PlanAlreadyFinishedError,
    PlanNotActiveError,
    PlanNotPausedError,
    ReleaseRequiresLockedSourceError,
)
from app.domain.planning.instruction import Instruction
from app.domain.planning.plannedAction import PlannedAction
from app.domain.planning.planSource import PlanSource
from app.domain.planning.planStatus import PlanStatus
from app.domain.planning.savingsPlan import SavingsPlan
from app.domain.planning.schedule import Schedule

NGN = Currency.NGN

BANK_DESTINATION = Destination(
    kind=DestinationKind.BANK_ACCOUNT,
    identifier="0123456789",
    name="Chinedu Okafor",
    details={"bank_code": "058"},
)


def payout(amount: str, label: str = "salary") -> Instruction:
    return Instruction(
        action=PlannedAction.PAYOUT,
        amount=Money(Decimal(amount), NGN),
        label=label,
        destination=BANK_DESTINATION,
    )


def release(amount: str, label: str = "emergency") -> Instruction:
    return Instruction(
        action=PlannedAction.RELEASE,
        amount=Money(Decimal(amount), NGN),
        label=label,
    )


class TestConstruction:
    def test_a_plan_starts_active_with_no_runs_recorded(self, build_plan):
        plan = build_plan()

        assert plan.status is PlanStatus.ACTIVE
        assert plan.completed_runs == 0
        assert isinstance(plan.plan_id, UUID)
        assert isinstance(plan.wallet_id, UUID)
        assert plan.ends_on is None

    def test_a_plan_holds_a_wallet_id_not_a_wallet(self, build_plan):
        """The aggregate boundary: references across aggregates are by identity."""
        plan = build_plan()

        assert isinstance(plan.wallet_id, UUID)

    def test_instructions_are_exposed_as_an_immutable_sequence(self, build_plan):
        plan = build_plan()

        assert isinstance(plan.instructions, tuple)
        with pytest.raises(AttributeError):
            plan.instructions.append(payout("500"))

    def test_a_plan_with_no_instructions_is_rejected(self, build_plan):
        with pytest.raises(EmptyPlanInstructionsError):
            build_plan(instructions=())

    def test_instructions_must_be_a_tuple(self, build_plan):
        """A list would be mutable by whoever still holds a reference to it."""
        with pytest.raises(InvalidPlanInstructionsError):
            build_plan(instructions=[payout("500")])

    def test_a_non_instruction_in_the_tuple_is_rejected(self, build_plan):
        with pytest.raises(InvalidPlanInstructionsError):
            build_plan(instructions=(payout("500"), "pay the rent"))

    def test_a_plan_may_hold_several_instructions(self, build_plan):
        plan = build_plan(
            instructions=(
                payout("2000", "salary"),
                payout("1500", "rent"),
                payout("500", "data"),
            )
        )

        assert len(plan.instructions) == 3


class TestConstructionValidation:
    def test_an_invalid_wallet_id_is_rejected(self, build_plan):
        with pytest.raises(InvalidPlanWalletIDError):
            SavingsPlan(
                wallet_id="not-a-uuid",
                source=PlanSource.LOCKED,
                schedule=Schedule(cadence=Cadence.MONTHLY, anchor=date(2026, 1, 1)),
                _instructions=(payout("500"),),
            )

    def test_an_invalid_plan_id_is_rejected(self, build_plan):
        with pytest.raises(InvalidPlanIDError):
            SavingsPlan(
                wallet_id=uuid4(),
                source=PlanSource.LOCKED,
                schedule=Schedule(cadence=Cadence.MONTHLY, anchor=date(2026, 1, 1)),
                _instructions=(payout("500"),),
                plan_id="not-a-uuid",
            )

    def test_an_unknown_source_is_rejected(self):
        with pytest.raises(InvalidPlanSourceError):
            SavingsPlan(
                wallet_id=uuid4(),
                source="locked",
                schedule=Schedule(cadence=Cadence.MONTHLY, anchor=date(2026, 1, 1)),
                _instructions=(payout("500"),),
            )

    def test_a_non_schedule_schedule_is_rejected(self):
        with pytest.raises(InvalidPlanScheduleError):
            SavingsPlan(
                wallet_id=uuid4(),
                source=PlanSource.LOCKED,
                schedule="monthly",
                _instructions=(payout("500"),),
            )

    def test_an_unknown_status_is_rejected(self):
        with pytest.raises(InvalidPlanStatusError):
            SavingsPlan(
                wallet_id=uuid4(),
                source=PlanSource.LOCKED,
                schedule=Schedule(cadence=Cadence.MONTHLY, anchor=date(2026, 1, 1)),
                _instructions=(payout("500"),),
                status="running",
            )

    def test_a_negative_run_count_is_rejected(self):
        with pytest.raises(InvalidCompletedRunsError):
            SavingsPlan(
                wallet_id=uuid4(),
                source=PlanSource.LOCKED,
                schedule=Schedule(cadence=Cadence.MONTHLY, anchor=date(2026, 1, 1)),
                _instructions=(payout("500"),),
                completed_runs=-1,
            )

    def test_an_end_date_before_the_first_run_is_rejected(self, build_plan):
        with pytest.raises(InvalidPlanEndDateError):
            build_plan(anchor=date(2026, 6, 1), ends_on=date(2026, 5, 31))

    def test_an_end_date_of_today_is_allowed(self, build_plan):
        """A one-run plan: the anchor itself is the only occurrence."""
        plan = build_plan(anchor=date(2026, 6, 1), ends_on=date(2026, 6, 1))

        assert plan.ends_on == date(2026, 6, 1)

    def test_a_datetime_end_date_is_rejected(self, build_plan):
        with pytest.raises(InvalidPlanEndDateError):
            build_plan(ends_on=datetime(2026, 12, 31, 23, 59))


class TestCurrencyHomogeneity:
    """One plan, one currency - or "what a run costs" has no answer."""

    def test_mixing_currencies_across_instructions_is_rejected(self, build_plan):
        with pytest.raises(MixedInstructionCurrenciesError):
            build_plan(
                instructions=(
                    payout("2000"),
                    Instruction(
                        action=PlannedAction.PAYOUT,
                        amount=Money(Decimal("100"), Currency.USD),
                        label="subscription",
                        destination=BANK_DESTINATION,
                    ),
                )
            )

    def test_instructions_sharing_a_non_default_currency_are_fine(self, build_plan):
        plan = build_plan(
            instructions=(
                Instruction(
                    action=PlannedAction.PAYOUT,
                    amount=Money(Decimal("100"), Currency.USD),
                    label="subscription",
                    destination=BANK_DESTINATION,
                ),
            )
        )

        assert plan.total_to_move.currency is Currency.USD


class TestSourceAgainstAction:
    """RELEASE only means something for money that was locked."""

    def test_releasing_from_a_locked_source_is_allowed(self, build_plan):
        plan = build_plan(
            source=PlanSource.LOCKED, instructions=(release("1000"),)
        )

        assert plan.source is PlanSource.LOCKED

    def test_releasing_from_an_available_source_is_rejected(self, build_plan):
        with pytest.raises(ReleaseRequiresLockedSourceError):
            build_plan(source=PlanSource.AVAILABLE, instructions=(release("1000"),))

    def test_paying_out_from_an_available_source_is_allowed(self, build_plan):
        """Scheduling is not conditional on locking - the second half of the product rule."""
        plan = build_plan(
            source=PlanSource.AVAILABLE, instructions=(payout("1000"),)
        )

        assert plan.source is PlanSource.AVAILABLE

    def test_the_rule_only_fires_when_a_release_is_present(self, build_plan):
        """An AVAILABLE plan with payouts only - the common case - is untouched."""
        plan = build_plan(
            source=PlanSource.AVAILABLE,
            instructions=(payout("1000"), payout("2000", "rent")),
        )

        assert len(plan.instructions) == 2


class TestDueDates:
    def test_a_new_plan_is_due_on_its_anchor(self, build_plan):
        plan = build_plan(anchor=date(2026, 1, 1))

        assert plan.next_due_at == date(2026, 1, 1)

    def test_the_next_due_date_advances_with_the_run_count(self, build_plan):
        plan = build_plan(anchor=date(2026, 1, 1), completed_runs=3)

        assert plan.next_due_at == date(2026, 4, 1)

    def test_a_plan_anchored_on_the_31st_stays_on_the_31st(self, build_plan):
        """The aggregate inherits the schedule's drift protection, not a copy of it."""
        plan = build_plan(anchor=date(2026, 1, 31))

        seen = [plan.next_due_at]
        for _ in range(3):
            plan.record_run()
            seen.append(plan.next_due_at)

        assert seen == [
            date(2026, 1, 31),
            date(2026, 2, 28),
            date(2026, 3, 31),
            date(2026, 4, 30),
        ]

    def test_a_plan_is_due_once_its_due_date_arrives(self, build_plan):
        plan = build_plan(anchor=date(2026, 1, 1))

        assert plan.is_due_at(date(2026, 1, 1))
        assert plan.is_due_at(date(2026, 5, 20))

    def test_a_plan_is_not_due_before_its_due_date(self, build_plan):
        plan = build_plan(anchor=date(2026, 1, 1))

        assert not plan.is_due_at(date(2025, 12, 31))

    def test_a_paused_plan_is_never_due(self, build_plan):
        """Paused means 'waiting for a human', not 'overdue'."""
        plan = build_plan(anchor=date(2026, 1, 1))
        plan.pause()

        assert not plan.is_due_at(date(2026, 6, 1))

    def test_a_cancelled_plan_is_never_due(self, build_plan):
        plan = build_plan(anchor=date(2026, 1, 1))
        plan.cancel()

        assert not plan.is_due_at(date(2026, 6, 1))


class TestRunCost:
    """The seam the scheduler needs: what a run costs, without touching a balance."""

    def test_a_single_instruction_run_costs_that_instruction(self, build_plan):
        plan = build_plan(instructions=(payout("2000"),))

        assert plan.total_to_move == Money(Decimal("2000"), NGN)

    def test_a_run_costs_the_sum_of_its_instructions(self, build_plan):
        """The headline case: 100k locked, paying 20k to each of five accounts."""
        plan = build_plan(
            instructions=tuple(payout("20000", f"salary {n}") for n in range(5))
        )

        assert plan.total_to_move == Money(Decimal("100000"), NGN)

    def test_a_run_mixing_payouts_and_releases_totals_both(self, build_plan):
        plan = build_plan(instructions=(payout("2000"), release("500")))

        assert plan.total_to_move == Money(Decimal("2500"), NGN)


class TestRunRecording:
    def test_recording_a_run_advances_the_occurrence(self, build_plan):
        plan = build_plan(anchor=date(2026, 1, 1))

        plan.record_run()

        assert plan.completed_runs == 1
        assert plan.next_due_at == date(2026, 2, 1)

    def test_recording_a_run_on_a_paused_plan_is_rejected(self, build_plan):
        """A paused plan is paused precisely so that it does not run."""
        plan = build_plan()
        plan.pause()

        with pytest.raises(PlanNotActiveError):
            plan.record_run()

    def test_a_paused_plan_does_not_lose_its_place(self, build_plan):
        """Pause then resume picks the missed payment back up; it is not skipped."""
        plan = build_plan(anchor=date(2026, 1, 1))
        plan.record_run()
        before = plan.next_due_at

        plan.pause()
        plan.resume()

        assert plan.completed_runs == 1
        assert plan.next_due_at == before


class TestEnding:
    def test_a_run_past_the_end_date_completes_the_plan(self, build_plan):
        plan = build_plan(
            anchor=date(2026, 1, 1),
            ends_on=date(2026, 3, 1),
            instructions=(payout("2000"),),
        )

        plan.record_run()  # Jan run done; next is Feb, still inside
        assert plan.status is PlanStatus.ACTIVE
        plan.record_run()  # Feb run done; next is Mar 1, still inside
        assert plan.status is PlanStatus.ACTIVE
        plan.record_run()  # Mar run done; next would be Apr 1, past the end
        assert plan.status is PlanStatus.COMPLETED

    def test_an_ended_plan_reports_its_true_run_count(self, build_plan):
        plan = build_plan(
            anchor=date(2026, 1, 1),
            ends_on=date(2026, 3, 1),
            instructions=(payout("2000"),),
        )
        for _ in range(3):
            plan.record_run()

        assert plan.status is PlanStatus.COMPLETED
        assert plan.completed_runs == 3

    def test_recording_a_run_on_a_completed_plan_is_rejected(self, build_plan):
        plan = build_plan(anchor=date(2026, 1, 1), ends_on=date(2026, 1, 1))
        plan.record_run()

        with pytest.raises(PlanNotActiveError):
            plan.record_run()

    def test_a_plan_without_an_end_date_keeps_going(self, build_plan):
        plan = build_plan(anchor=date(2026, 1, 1))

        for _ in range(24):
            plan.record_run()

        assert plan.status is PlanStatus.ACTIVE
        assert plan.completed_runs == 24


class TestLifecycle:
    def test_pausing_an_active_plan_pauses_it(self, build_plan):
        plan = build_plan()

        plan.pause()

        assert plan.status is PlanStatus.PAUSED

    def test_pausing_a_paused_plan_is_rejected(self, build_plan):
        plan = build_plan()
        plan.pause()

        with pytest.raises(PlanNotActiveError):
            plan.pause()

    def test_resuming_a_paused_plan_restores_it(self, build_plan):
        plan = build_plan()
        plan.pause()

        plan.resume()

        assert plan.status is PlanStatus.ACTIVE

    def test_resuming_an_active_plan_is_rejected(self, build_plan):
        plan = build_plan()

        with pytest.raises(PlanNotPausedError):
            plan.resume()

    def test_cancelling_an_active_plan_cancels_it(self, build_plan):
        plan = build_plan()

        plan.cancel()

        assert plan.status is PlanStatus.CANCELLED

    def test_cancelling_a_paused_plan_is_allowed(self, build_plan):
        """A paused plan waits for a human, and this is the human giving up on it."""
        plan = build_plan()
        plan.pause()

        plan.cancel()

        assert plan.status is PlanStatus.CANCELLED

    def test_cancelling_a_cancelled_plan_is_rejected(self, build_plan):
        plan = build_plan()
        plan.cancel()

        with pytest.raises(PlanAlreadyFinishedError):
            plan.cancel()

    def test_cancelling_a_completed_plan_is_rejected(self, build_plan):
        """Terminal is terminal - a completed plan cannot be quietly reopened."""
        plan = build_plan(anchor=date(2026, 1, 1), ends_on=date(2026, 1, 1))
        plan.record_run()

        with pytest.raises(PlanAlreadyFinishedError):
            plan.cancel()

    def test_a_cancelled_plan_cannot_be_resumed(self, build_plan):
        plan = build_plan()
        plan.cancel()

        with pytest.raises(PlanNotPausedError):
            plan.resume()


def test_str_summarises_the_plan(build_plan):
    plan = build_plan(anchor=date(2026, 1, 1))

    assert "active" in str(plan)
    assert "monthly from 2026-01-01" in str(plan)
    assert "1 instruction(s)" in str(plan)
    assert "locked" in str(plan)
