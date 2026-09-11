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
    CommittedPayoutRemovalError,
    EmptyPlanInstructionsError,
    FundRequiresLockedSourceError,
    InvalidCompletedRunsError,
    InvalidPlanEndDateError,
    InvalidPlanFundIDError,
    InvalidPlanIDError,
    InvalidPlanInstructionsError,
    InvalidPlanNameError,
    InvalidPlanScheduleError,
    InvalidPlanSourceError,
    InvalidPlanStatusError,
    InvalidPlanWalletIDError,
    IrreversibleReleasePlanError,
    MixedInstructionCurrenciesError,
    PlanAlreadyFinishedError,
    PlanNotActiveError,
    PlanNotPausedError,
    ReleasePlanRequiresEndDateError,
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
    def test_an_invalid_wallet_id_is_rejected(self):
        with pytest.raises(InvalidPlanWalletIDError):
            SavingsPlan(
                wallet_id="not-a-uuid",
                name="salary",
                source=PlanSource.LOCKED,
                schedule=Schedule(cadence=Cadence.MONTHLY, anchor=datetime(2026, 1, 1)),
                _instructions=(payout("500"),),
            )

    def test_an_invalid_plan_id_is_rejected(self):
        with pytest.raises(InvalidPlanIDError):
            SavingsPlan(
                wallet_id=uuid4(),
                name="salary",
                source=PlanSource.LOCKED,
                schedule=Schedule(cadence=Cadence.MONTHLY, anchor=datetime(2026, 1, 1)),
                _instructions=(payout("500"),),
                plan_id="not-a-uuid",
            )

    def test_an_unknown_source_is_rejected(self):
        with pytest.raises(InvalidPlanSourceError):
            SavingsPlan(
                wallet_id=uuid4(),
                name="salary",
                source="locked",
                schedule=Schedule(cadence=Cadence.MONTHLY, anchor=datetime(2026, 1, 1)),
                _instructions=(payout("500"),),
            )

    def test_a_non_schedule_schedule_is_rejected(self):
        with pytest.raises(InvalidPlanScheduleError):
            SavingsPlan(
                wallet_id=uuid4(),
                name="salary",
                source=PlanSource.LOCKED,
                schedule="monthly",
                _instructions=(payout("500"),),
            )

    def test_an_unknown_status_is_rejected(self):
        with pytest.raises(InvalidPlanStatusError):
            SavingsPlan(
                wallet_id=uuid4(),
                name="salary",
                source=PlanSource.LOCKED,
                schedule=Schedule(cadence=Cadence.MONTHLY, anchor=datetime(2026, 1, 1)),
                _instructions=(payout("500"),),
                status="running",
            )

    def test_a_negative_run_count_is_rejected(self):
        with pytest.raises(InvalidCompletedRunsError):
            SavingsPlan(
                wallet_id=uuid4(),
                name="salary",
                source=PlanSource.LOCKED,
                schedule=Schedule(cadence=Cadence.MONTHLY, anchor=datetime(2026, 1, 1)),
                _instructions=(payout("500"),),
                completed_runs=-1,
            )

    def test_an_end_date_before_the_first_run_is_rejected(self, build_plan):
        """Compared by *day*: 31 May is before 1 June even though the run is at noon."""
        with pytest.raises(InvalidPlanEndDateError):
            build_plan(anchor=datetime(2026, 6, 1, 12, 0), ends_on=date(2026, 5, 31))

    def test_an_end_date_of_today_is_allowed(self, build_plan):
        """A one-run plan: the anchor's own day is the only occurrence.

        Anchored at noon and ended the same day. Allowed, because the end is a
        *day* - if it were a moment it would be midnight on 1 June, which falls
        before the plan's only run, and this would be an error instead.
        """
        plan = build_plan(anchor=datetime(2026, 6, 1, 12, 0), ends_on=date(2026, 6, 1))

        assert plan.ends_on == date(2026, 6, 1)

    def test_a_datetime_end_date_is_rejected(self, build_plan):
        with pytest.raises(InvalidPlanEndDateError):
            build_plan(ends_on=datetime(2026, 12, 31, 23, 59))


class TestNaming:
    """A plan has a name because a wallet with five plans is otherwise five UUIDs."""

    def test_a_plan_carries_the_name_it_was_given(self, build_plan):
        plan = build_plan(name="Rent 2026")

        assert plan.name == "Rent 2026"

    def test_a_blank_name_is_rejected(self, build_plan):
        with pytest.raises(InvalidPlanNameError):
            build_plan(name="   ")

    def test_an_empty_name_is_rejected(self, build_plan):
        with pytest.raises(InvalidPlanNameError):
            build_plan(name="")

    def test_a_non_string_name_is_rejected(self, build_plan):
        with pytest.raises(InvalidPlanNameError):
            build_plan(name=42)

    def test_two_plans_on_one_wallet_are_told_apart_by_name(self, build_plan):
        rent = build_plan(name="Rent")
        salary = build_plan(name="Salary")

        assert rent.name != salary.name


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
            source=PlanSource.LOCKED,
            instructions=(release("1000"),),
            ends_on=date(2026, 6, 1),
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


class TestTheNamedPot:
    """Which pot a plan draws on, and the half of the rule the aggregate owns.

    The pairing is checked in two places and both are needed. The *aggregate*
    refuses a pot on anything but a locked-source plan, and it must: that rule
    has to hold through hydration, through a direct construction, and through
    ``edit_instructions``, none of which pass through a service. The *use case*
    refuses a locked plan with no pot, and it must too - a plan saved before pots
    existed has no pot, and enforcing "every locked plan names one" here would
    make every row already on disk unreadable. So this class pins one half and
    ``test_plan_service`` pins the other, deliberately.
    """

    def test_a_locked_plan_may_name_a_pot(self, build_plan):
        plan = build_plan(
            source=PlanSource.LOCKED,
            instructions=(payout("1000"),),
            fund_id=uuid4(),
        )

        assert plan.fund_id is not None

    def test_a_locked_plan_may_name_no_pot(self, build_plan):
        """Not a loophole - a legacy row, which is the only thing that looks like this.

        Deliberately *not* refused here. Plans created before this phase exist on
        disk with no pot, and they still have to hydrate and still have to run;
        refusing them at construction would break every one of them at the moment
        of the upgrade. The refusal that matters to a user - "you must name the
        pot you're spending from" - happens where a human is typing, not here.
        """
        plan = build_plan(source=PlanSource.LOCKED, fund_id=None)

        assert plan.fund_id is None

    def test_an_available_plan_may_not_name_a_pot(self, build_plan):
        """An available-balance plan spends no pot, so naming one is a mistake.

        Refused rather than ignored: silently dropping the argument would leave
        the user believing their plan was tied to a pot it has no relationship
        with, and they would find out at the first run.
        """
        with pytest.raises(FundRequiresLockedSourceError):
            build_plan(
                source=PlanSource.AVAILABLE,
                instructions=(payout("1000"),),
                fund_id=uuid4(),
            )

    def test_a_fund_id_must_be_a_uuid(self, build_plan):
        with pytest.raises(InvalidPlanFundIDError):
            build_plan(source=PlanSource.LOCKED, fund_id="Supplier")


class TestIrreversibility:
    """Locking is the commitment device, so releasing is the one act you cannot take back."""

    def test_a_payout_plan_is_reversible(self, build_plan):
        assert not build_plan(instructions=(payout("2000"),)).is_irreversible

    def test_a_release_plan_is_irreversible(self, build_plan):
        plan = build_plan(
            source=PlanSource.LOCKED,
            instructions=(release("1000"),),
            ends_on=date(2026, 6, 1),
        )

        assert plan.is_irreversible

    def test_a_plan_mixing_a_payout_with_a_release_is_irreversible(self, build_plan):
        """One release is enough - the plan as a whole is now a promise."""
        plan = build_plan(
            source=PlanSource.LOCKED,
            instructions=(payout("2000"), release("500")),
            ends_on=date(2026, 6, 1),
        )

        assert plan.is_irreversible

    def test_a_release_plan_without_a_set_date_is_rejected(self, build_plan):
        """Open-ended and uncancellable together would mean locked money with no exit."""
        with pytest.raises(ReleasePlanRequiresEndDateError):
            build_plan(source=PlanSource.LOCKED, instructions=(release("1000"),))

    def test_a_release_plan_with_a_set_date_is_accepted(self, build_plan):
        plan = build_plan(
            source=PlanSource.LOCKED,
            instructions=(release("1000"),),
            ends_on=date(2026, 3, 1),
        )

        assert plan.ends_on == date(2026, 3, 1)

    def test_cancelling_a_release_plan_is_refused(self, build_plan):
        plan = build_plan(
            source=PlanSource.LOCKED,
            instructions=(release("1000"),),
            ends_on=date(2026, 6, 1),
        )

        with pytest.raises(IrreversibleReleasePlanError):
            plan.cancel()

    def test_a_refused_cancellation_leaves_the_plan_active(self, build_plan):
        """The refusal has to be total - a half-applied status change is worse than none."""
        plan = build_plan(
            source=PlanSource.LOCKED,
            instructions=(release("1000"),),
            ends_on=date(2026, 6, 1),
        )

        with pytest.raises(IrreversibleReleasePlanError):
            plan.cancel()

        assert plan.status is PlanStatus.ACTIVE

    def test_a_payout_plan_drawing_on_locked_money_can_still_be_cancelled(self, build_plan):
        """The rule is about releasing, not about which balance the money sits in."""
        plan = build_plan(
            source=PlanSource.LOCKED,
            instructions=(payout("20000"),),
            ends_on=date(2026, 6, 1),
        )

        plan.cancel()

        assert plan.status is PlanStatus.CANCELLED

    def test_a_release_plan_can_still_be_paused(self, build_plan):
        """Deferral is recoverable, termination is not - so pause survives."""
        plan = build_plan(
            source=PlanSource.LOCKED,
            instructions=(release("1000"),),
            ends_on=date(2026, 6, 1),
        )

        plan.pause()

        assert plan.status is PlanStatus.PAUSED


class TestEditing:
    """Payouts are instructions, not vows - they can be rewritten until they run."""

    def test_an_active_payout_plan_can_be_edited(self, build_plan):
        plan = build_plan(instructions=(payout("2000"),))

        plan.edit_instructions((payout("3000", "salary"), payout("500", "data")))

        assert len(plan.instructions) == 2
        assert plan.total_to_move == Money(Decimal("3500"), NGN)

    def test_editing_does_not_disturb_the_plan_position(self, build_plan):
        """Editing changes the lines, not the run count - the next due moment is untouched."""
        plan = build_plan(anchor=datetime(2026, 1, 1), completed_runs=3)

        plan.edit_instructions((payout("9999"),))

        assert plan.completed_runs == 3
        assert plan.next_due_at == datetime(2026, 4, 1)

    def test_editing_to_an_empty_list_is_rejected(self, build_plan):
        plan = build_plan()

        with pytest.raises(EmptyPlanInstructionsError):
            plan.edit_instructions(())

    def test_editing_to_a_list_is_rejected(self, build_plan):
        """The same tuple rule as construction - it is the same validation."""
        plan = build_plan()

        with pytest.raises(InvalidPlanInstructionsError):
            plan.edit_instructions([payout("500")])

    def test_editing_may_not_mix_currencies(self, build_plan):
        plan = build_plan()
        usd = Instruction(
            action=PlannedAction.PAYOUT,
            amount=Money(Decimal("100"), Currency.USD),
            label="subscription",
            destination=BANK_DESTINATION,
        )

        with pytest.raises(MixedInstructionCurrenciesError):
            plan.edit_instructions((payout("2000"), usd))

    def test_editing_may_not_introduce_a_release_on_an_available_plan(self, build_plan):
        """The edit door enforces the source rule too - that is why it is one door."""
        plan = build_plan(source=PlanSource.AVAILABLE, instructions=(payout("2000"),))

        with pytest.raises(ReleaseRequiresLockedSourceError):
            plan.edit_instructions((release("500"),))

    def test_a_rejected_edit_leaves_the_original_instructions_in_place(self, build_plan):
        """Validate, then assign. A rejected candidate must not be half-applied."""
        plan = build_plan(instructions=(payout("2000"),))
        usd = Instruction(
            action=PlannedAction.PAYOUT,
            amount=Money(Decimal("100"), Currency.USD),
            label="subscription",
            destination=BANK_DESTINATION,
        )

        with pytest.raises(MixedInstructionCurrenciesError):
            plan.edit_instructions((payout("3000"), usd))

        assert plan.instructions == (payout("2000"),)
        assert plan.total_to_move == Money(Decimal("2000"), NGN)

    def test_a_release_plan_cannot_be_edited(self, build_plan):
        plan = build_plan(
            source=PlanSource.LOCKED,
            instructions=(release("1000"),),
            ends_on=date(2026, 6, 1),
        )

        with pytest.raises(IrreversibleReleasePlanError):
            plan.edit_instructions((release("2000"),))

    def test_a_paused_plan_cannot_be_edited(self, build_plan):
        plan = build_plan()
        plan.pause()

        with pytest.raises(PlanNotActiveError):
            plan.edit_instructions((payout("500"),))

    def test_a_cancelled_plan_cannot_be_edited(self, build_plan):
        plan = build_plan()
        plan.cancel()

        with pytest.raises(PlanNotActiveError):
            plan.edit_instructions((payout("500"),))

    def test_introducing_a_release_without_a_set_date_is_refused(self, build_plan):
        """Convert a payout plan into a release plan only if it already says when it ends."""
        plan = build_plan(source=PlanSource.LOCKED, instructions=(payout("2000"),))

        with pytest.raises(ReleasePlanRequiresEndDateError):
            plan.edit_instructions((release("2000"),))

    def test_introducing_a_release_with_a_set_date_is_accepted(self, build_plan):
        """With a date, the conversion is coherent - and the plan becomes irreversible."""
        plan = build_plan(
            source=PlanSource.LOCKED,
            instructions=(payout("2000"),),
            ends_on=date(2026, 6, 1),
        )

        plan.edit_instructions((release("2000"),))

        assert plan.is_irreversible
        with pytest.raises(IrreversibleReleasePlanError):
            plan.cancel()

    def test_a_committed_plan_may_not_drop_its_payout(self, build_plan):
        """Only removal is refused, and this is the removal.

        The payout line *is* the commitment, so deleting it does not edit the
        commitment - it revokes it, through the door that is supposed to be the
        safe one. ``cancel`` is the operation that ends a plan; this is that
        operation arriving without its name.
        """
        plan = build_plan(
            source=PlanSource.LOCKED,
            instructions=(payout("2000"),),
            fund_id=uuid4(),
        )

        with pytest.raises(CommittedPayoutRemovalError):
            plan.edit_instructions((release("2000"),))

    def test_a_committed_plan_may_change_where_and_how_much_it_pays(self, build_plan):
        """The other half of "only removal is refused", and it is the useful half.

        A supplier's account changes, or the price does. Both are edits to *who*
        and *how much*, neither changes *whether* the money leaves, so both are
        allowed - and they have to be, or the rule would make a plan uneditable
        the moment it named a pot.
        """
        plan = build_plan(
            source=PlanSource.LOCKED,
            instructions=(payout("2000"),),
            fund_id=uuid4(),
        )
        elsewhere = Instruction(
            action=PlannedAction.PAYOUT,
            amount=Money(Decimal("2500"), NGN),
            label="salary",
            destination=Destination(
                kind=DestinationKind.BANK_ACCOUNT,
                identifier="0987654321",
                name="Ada Nwosu",
                details={"bank_code": "058"},
            ),
        )

        plan.edit_instructions((elsewhere,))

        assert plan.instructions[0].destination.name == "Ada Nwosu"

    def test_a_committed_plan_may_not_swap_its_payout_for_a_release(self, build_plan):
        """Replacing the payout is a removal wearing a disguise.

        The rule is written against "does the new list still contain a payout?"
        rather than "is it the same object?", which is what makes this refusal
        fall out of the same check rather than needing its own.
        """
        plan = build_plan(
            source=PlanSource.LOCKED,
            instructions=(payout("2000"),),
            fund_id=uuid4(),
            ends_on=date(2026, 6, 1),
        )

        with pytest.raises(CommittedPayoutRemovalError):
            plan.edit_instructions((release("2000"),))

    def test_a_plan_with_no_pot_may_still_drop_its_payout(self, build_plan):
        """The legacy plan keeps the freedom it had, because it has no commitment.

        A plan with no pot draws on the pool, so there is no pot whose money is
        committed to a payee. Removing the payout costs nothing it cannot afford,
        and refusing it here would be enforcing the new rule against rows written
        under the old one.
        """
        plan = build_plan(
            source=PlanSource.LOCKED,
            instructions=(payout("2000"),),
            fund_id=None,
            ends_on=date(2026, 6, 1),
        )

        plan.edit_instructions((release("2000"),))

        assert plan.is_irreversible


def usd_payout(amount: str = "100") -> Instruction:
    return Instruction(
        action=PlannedAction.PAYOUT,
        amount=Money(Decimal(amount), Currency.USD),
        label="subscription",
        destination=BANK_DESTINATION,
    )


#: Instruction lists that must be refused, with the reason each is refused.
#: Every one of these is reachable through both doors, and the whole point of
#: the class below is that neither door is the one that gets forgotten.
REJECTED_INSTRUCTION_LISTS = [
    ("empty", PlanSource.LOCKED, None, (), EmptyPlanInstructionsError),
    ("a list, not a tuple", PlanSource.LOCKED, None, [payout("500")], InvalidPlanInstructionsError),
    ("a non-instruction", PlanSource.LOCKED, None, (payout("500"), "pay the rent"), InvalidPlanInstructionsError),
    ("mixed currencies", PlanSource.LOCKED, None, (payout("2000"), usd_payout()), MixedInstructionCurrenciesError),
    ("a release on an available source", PlanSource.AVAILABLE, None, (release("500"),), ReleaseRequiresLockedSourceError),
    ("an open-ended release", PlanSource.LOCKED, None, (release("500"),), ReleasePlanRequiresEndDateError),
]


class TestTheTwoDoorsAgree:
    """Construction and editing enforce one rule, because they call one method.

    Written after both doors were found disagreeing: the edit path accepted a
    list where the constructor demanded a tuple, and would happily build an
    open-ended release plan. Two hand-written tests could have caught those
    individually, and did not - the list and the end-date rule were each checked
    on the way in and nowhere else.

    This class is the general statement of the rule, so the *next* check that
    gets added to one door has somewhere to fail loudly if it is not added to
    the other. It is a property, not an example: for every way a candidate can
    be illegal, the same candidate must be rejected identically whichever way it
    arrives.
    """

    @pytest.mark.parametrize(
        "description, source, ends_on, candidate, expected",
        REJECTED_INSTRUCTION_LISTS,
        ids=[case[0] for case in REJECTED_INSTRUCTION_LISTS],
    )
    def test_the_constructor_refuses_the_candidate(
        self, description, source, ends_on, candidate, expected, build_plan
    ):
        with pytest.raises(expected):
            build_plan(source=source, ends_on=ends_on, instructions=candidate)

    @pytest.mark.parametrize(
        "description, source, ends_on, candidate, expected",
        REJECTED_INSTRUCTION_LISTS,
        ids=[case[0] for case in REJECTED_INSTRUCTION_LISTS],
    )
    def test_editing_refuses_the_same_candidate(
        self, description, source, ends_on, candidate, expected, build_plan
    ):
        # Built with a known-good payout list, so the only illegal thing in play
        # is the candidate - otherwise this would pass for the wrong reason.
        plan = build_plan(source=source, ends_on=ends_on, instructions=(payout("2000"),))

        with pytest.raises(expected):
            plan.edit_instructions(candidate)


class TestDueMoments:
    """A plan fires at an instant and is checked against the instant it is asked about."""

    def test_a_new_plan_is_due_on_its_anchor(self, build_plan):
        plan = build_plan(anchor=datetime(2026, 1, 1))

        assert plan.next_due_at == datetime(2026, 1, 1)

    def test_the_next_due_moment_advances_with_the_run_count(self, build_plan):
        plan = build_plan(anchor=datetime(2026, 1, 1), completed_runs=3)

        assert plan.next_due_at == datetime(2026, 4, 1)

    def test_a_plan_anchored_on_the_31st_stays_on_the_31st(self, build_plan):
        """The aggregate inherits the schedule's drift protection, not a copy of it."""
        plan = build_plan(anchor=datetime(2026, 1, 31))

        seen = [plan.next_due_at]
        for _ in range(3):
            plan.record_run()
            seen.append(plan.next_due_at)

        assert seen == [
            datetime(2026, 1, 31),
            datetime(2026, 2, 28),
            datetime(2026, 3, 31),
            datetime(2026, 4, 30),
        ]

    def test_a_plan_is_due_once_its_due_moment_arrives(self, build_plan):
        plan = build_plan(anchor=datetime(2026, 1, 1))

        assert plan.is_due_at(datetime(2026, 1, 1))
        assert plan.is_due_at(datetime(2026, 5, 20))

    def test_a_plan_is_not_due_before_its_due_moment(self, build_plan):
        plan = build_plan(anchor=datetime(2026, 1, 1))

        assert not plan.is_due_at(datetime(2025, 12, 31))

    def test_a_plan_is_not_due_an_hour_before_its_due_moment(self, build_plan):
        """The hour is real, not cosmetic - the claim the date-based model could not make."""
        plan = build_plan(anchor=datetime(2026, 3, 2, 12, 0))

        assert not plan.is_due_at(datetime(2026, 3, 2, 11, 59))
        assert plan.is_due_at(datetime(2026, 3, 2, 12, 0))

    def test_a_paused_plan_is_never_due(self, build_plan):
        """Paused means 'waiting for a human', not 'overdue'."""
        plan = build_plan(anchor=datetime(2026, 1, 1))
        plan.pause()

        assert not plan.is_due_at(datetime(2026, 6, 1))

    def test_a_cancelled_plan_is_never_due(self, build_plan):
        plan = build_plan(anchor=datetime(2026, 1, 1))
        plan.cancel()

        assert not plan.is_due_at(datetime(2026, 6, 1))


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
        plan = build_plan(
            instructions=(payout("2000"), release("500")),
            ends_on=date(2026, 6, 1),
        )

        assert plan.total_to_move == Money(Decimal("2500"), NGN)


class TestRunRecording:
    def test_recording_a_run_advances_the_occurrence(self, build_plan):
        plan = build_plan(anchor=datetime(2026, 1, 1))

        plan.record_run()

        assert plan.completed_runs == 1
        assert plan.next_due_at == datetime(2026, 2, 1)

    def test_recording_a_run_on_a_paused_plan_is_rejected(self, build_plan):
        """A paused plan is paused precisely so that it does not run."""
        plan = build_plan()
        plan.pause()

        with pytest.raises(PlanNotActiveError):
            plan.record_run()

    def test_a_paused_plan_does_not_lose_its_place(self, build_plan):
        """Pause then resume picks the missed payment back up; it is not skipped."""
        plan = build_plan(anchor=datetime(2026, 1, 1))
        plan.record_run()
        before = plan.next_due_at

        plan.pause()
        plan.resume()

        assert plan.completed_runs == 1
        assert plan.next_due_at == before


class TestEnding:
    def test_a_run_past_the_end_date_completes_the_plan(self, build_plan):
        plan = build_plan(
            anchor=datetime(2026, 1, 1),
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
            anchor=datetime(2026, 1, 1),
            ends_on=date(2026, 3, 1),
            instructions=(payout("2000"),),
        )
        for _ in range(3):
            plan.record_run()

        assert plan.status is PlanStatus.COMPLETED
        assert plan.completed_runs == 3

    def test_recording_a_run_on_a_completed_plan_is_rejected(self, build_plan):
        plan = build_plan(anchor=datetime(2026, 1, 1), ends_on=date(2026, 1, 1))
        plan.record_run()

        with pytest.raises(PlanNotActiveError):
            plan.record_run()

    def test_a_plan_without_an_end_date_keeps_going(self, build_plan):
        plan = build_plan(anchor=datetime(2026, 1, 1))

        for _ in range(24):
            plan.record_run()

        assert plan.status is PlanStatus.ACTIVE
        assert plan.completed_runs == 24


class TestTheLastDayIsIncluded:
    """Decision A, as claims the aggregate has to keep.

    A plan *fires at a moment* but *ends on a day*, and the end day counts.
    ``--until 2026-07-02`` means through 2 July, so a noon run on 2 July
    happens. If the end were a moment it would be midnight on the 2nd - twelve
    hours *before* that run - and the last payment the user typed the date for
    would silently not happen.

    The failure this class pins down is quiet in both directions. Compare the
    moment against a midnight end and the plan retires a day early; compare days
    and it retires a day late. Neither raises, and neither is visible in the
    ledger, which is why the boundary is stated here rather than left to the
    happy-path tests.
    """

    def test_a_run_on_the_end_day_at_noon_still_happens(self, build_plan):
        plan = build_plan(
            anchor=datetime(2026, 3, 1, 12, 0),
            ends_on=date(2026, 3, 1),
            instructions=(payout("2000"),),
        )

        plan.record_run()

        # The noon run happened, and only then did the plan retire.
        assert plan.completed_runs == 1
        assert plan.status is PlanStatus.COMPLETED

    def test_the_plan_is_still_active_the_day_before_its_last_day(self, build_plan):
        """The test that fails if ``ends_on`` is compared as a *moment*.

        Four monthly runs from 2 March land on 2 June. The next due moment is
        noon on 2 July, which is the last day - still owed, so the plan must
        still be active. Read ``ends_on`` as midnight and noon-on-the-2nd is
        already past it, so the plan would complete a day early and 2 July would
        never be paid.
        """
        plan = build_plan(
            anchor=datetime(2026, 3, 2, 12, 0),
            ends_on=date(2026, 7, 2),
            instructions=(payout("2000"),),
        )

        for _ in range(4):
            plan.record_run()

        assert plan.status is PlanStatus.ACTIVE
        assert plan.next_due_at == datetime(2026, 7, 2, 12, 0)

    def test_a_plan_running_through_july_pays_five_times_not_four(self, build_plan):
        """The headline arithmetic of decision A, at the aggregate.

        2 March, 2 April, 2 May, 2 June and 2 July - five payments. The moment
        the fifth is recorded, the next due moment is in August, which is past
        the end, and the plan completes.
        """
        plan = build_plan(
            anchor=datetime(2026, 3, 2, 12, 0),
            ends_on=date(2026, 7, 2),
            instructions=(payout("2000"),),
        )

        for _ in range(5):
            plan.record_run()

        assert plan.completed_runs == 5
        assert plan.status is PlanStatus.COMPLETED

    def test_the_end_day_is_judged_by_the_days_the_runs_land_on(self, build_plan):
        """A late-morning plan and a midnight plan end on the same day.

        The time of day is part of the *moment* and no part of the *deadline*:
        two plans agreeing on their days must agree on when they end, whatever
        hour they fire at.
        """
        at_noon = build_plan(
            anchor=datetime(2026, 1, 1, 12, 0),
            ends_on=date(2026, 3, 1),
            instructions=(payout("2000"),),
        )
        at_midnight = build_plan(
            anchor=datetime(2026, 1, 1),
            ends_on=date(2026, 3, 1),
            instructions=(payout("2000"),),
        )

        for _ in range(3):
            at_noon.record_run()
            at_midnight.record_run()

        assert at_noon.status is at_midnight.status is PlanStatus.COMPLETED
        assert at_noon.completed_runs == at_midnight.completed_runs == 3


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
        plan = build_plan(anchor=datetime(2026, 1, 1), ends_on=date(2026, 1, 1))
        plan.record_run()

        with pytest.raises(PlanAlreadyFinishedError):
            plan.cancel()

    def test_a_cancelled_plan_cannot_be_resumed(self, build_plan):
        plan = build_plan()
        plan.cancel()

        with pytest.raises(PlanNotPausedError):
            plan.resume()


def test_str_summarises_the_plan(build_plan):
    plan = build_plan(anchor=datetime(2026, 1, 1))

    assert "active" in str(plan)
    assert "monthly from 2026-01-01" in str(plan)
    assert "1 instruction(s)" in str(plan)
    assert "locked" in str(plan)
