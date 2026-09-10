import uuid
from dataclasses import dataclass, field
from datetime import date, datetime

from app.domain.money.money import Money

from .exception import (
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
from .instruction import Instruction
from .plannedAction import PlannedAction
from .planSource import PlanSource
from .planStatus import PlanStatus
from .schedule import Schedule


@dataclass
class SavingsPlan:
    """A standing instruction to move money on a repeating schedule.

    The aggregate root for the planning context. It holds a ``wallet_id``, not a
    ``Wallet``: aggregates reference each other by identity so that no code can
    reach through the reference and mutate a second aggregate inside this one's
    transaction. Reaching a wallet from a plan is possible - but only by loading
    it deliberately, which makes the boundary a thing you cross rather than a
    thing you ignore.

    Consequence worth stating plainly: the rule "a plan's currency must match its
    wallet's currency" is **not** enforced here. The plan cannot see the wallet,
    and the wallet cannot see the plan, so neither is in a position to check.
    That rule lives in the use case, which is the only place both are loaded.
    Invariants inside one aggregate belong to that aggregate; invariants that
    span two belong to the application layer.

    ``completed_runs`` is the drift guard. Every due date is derived as
    ``schedule.occurrence(completed_runs)`` rather than by adding a step to the
    last run's date, so a plan can never slowly slide off its anchor. It also
    gives pause/resume the right behaviour for free: a paused plan does not
    advance the counter, so its next run is still the one that was missed.
    """

    wallet_id: uuid.UUID
    source: PlanSource
    schedule: Schedule
    _instructions: tuple[Instruction, ...]
    plan_id: uuid.UUID = field(default_factory=uuid.uuid4)
    status: PlanStatus = PlanStatus.ACTIVE
    completed_runs: int = 0
    ends_on: date | None = None
    created_at: datetime = field(default_factory=datetime.now)

    @property
    def instructions(self) -> tuple[Instruction, ...]:
        """The plan's lines, as an immutable sequence.

        Exposed as a tuple rather than the list it was built from so that
        ``plan.instructions.append(...)`` is not merely discouraged but
        impossible - it would slip a line past every ``__post_init__`` check.
        """
        return self._instructions

    @property
    def next_due_at(self) -> date:
        """When this plan next fires. Derived from the anchor, never accumulated."""
        return self.schedule.occurrence(self.completed_runs)

    @property
    def total_to_move(self) -> Money:
        """What one run costs, in total, across every instruction.

        The plan can answer this without knowing anything about a balance - which
        is exactly the seam the scheduler needs. It can ask "what will this run
        cost?" and compare that to a wallet it loads itself, keeping the plan
        balance-agnostic as agreed.
        """
        total = self._instructions[0].amount
        for instruction in self._instructions[1:]:
            total = total + instruction.amount
        return total

    def is_due_at(self, moment: date) -> bool:
        """Whether a run is owed as of ``moment``. Only an ACTIVE plan is ever due."""
        return self.status is PlanStatus.ACTIVE and self.next_due_at <= moment

    def record_run(self):
        """Advance to the next occurrence after a run succeeded.

        Completes the plan when the next occurrence would fall past ``ends_on``.
        Called only on success: a run that was rejected must not advance the
        counter, so the missed payment stays queued.
        """
        if self.status is not PlanStatus.ACTIVE:
            raise PlanNotActiveError(
                f"a {self.status.value} plan cannot record a run"
            )

        self.completed_runs += 1

        if self.ends_on is not None and self.next_due_at > self.ends_on:
            self.status = PlanStatus.COMPLETED

    def pause(self):
        """Stop the plan from being due, without discarding its position."""
        if self.status is not PlanStatus.ACTIVE:
            raise PlanNotActiveError(f"a {self.status.value} plan cannot be paused")
        self.status = PlanStatus.PAUSED

    def resume(self):
        """Return a paused plan to service, at the occurrence it left off on."""
        if self.status is not PlanStatus.PAUSED:
            raise PlanNotPausedError(f"a {self.status.value} plan cannot be resumed")
        self.status = PlanStatus.ACTIVE

    def cancel(self):
        """End the plan for good. Terminal - unlike pause, there is no way back."""
        if self.status in (PlanStatus.CANCELLED, PlanStatus.COMPLETED):
            raise PlanAlreadyFinishedError(
                f"a {self.status.value} plan cannot be cancelled"
            )
        self.status = PlanStatus.CANCELLED

    def __post_init__(self):
        if not isinstance(self.plan_id, uuid.UUID):
            raise InvalidPlanIDError("invalid plan id")

        if not isinstance(self.wallet_id, uuid.UUID):
            raise InvalidPlanWalletIDError("invalid wallet id")

        if not isinstance(self.source, PlanSource):
            raise InvalidPlanSourceError(
                f"source must be a PlanSource, got {type(self.source).__name__}"
            )

        if not isinstance(self.schedule, Schedule):
            raise InvalidPlanScheduleError(
                f"schedule must be a Schedule, got {type(self.schedule).__name__}"
            )

        if not isinstance(self.status, PlanStatus):
            raise InvalidPlanStatusError(
                f"status must be a PlanStatus, got {type(self.status).__name__}"
            )

        if not isinstance(self.completed_runs, int) or self.completed_runs < 0:
            raise InvalidCompletedRunsError(
                f"completed_runs must be a non-negative integer, "
                f"got {self.completed_runs!r}"
            )

        if self.ends_on is not None:
            if not isinstance(self.ends_on, date) or isinstance(self.ends_on, datetime):
                raise InvalidPlanEndDateError(
                    f"ends_on must be a date or None, "
                    f"got {type(self.ends_on).__name__}"
                )
            if self.ends_on < self.schedule.anchor:
                raise InvalidPlanEndDateError(
                    "ends_on cannot fall before the plan's first run"
                )

        if not isinstance(self._instructions, tuple):
            raise InvalidPlanInstructionsError(
                f"instructions must be a tuple, got {type(self._instructions).__name__}"
            )

        if not self._instructions:
            raise EmptyPlanInstructionsError("a plan must have at least one instruction")

        for instruction in self._instructions:
            if not isinstance(instruction, Instruction):
                raise InvalidPlanInstructionsError(
                    f"every instruction must be an Instruction, "
                    f"got {type(instruction).__name__}"
                )

        # A plan moves one currency. Mixing them inside a single run would make
        # total_to_move - the number the scheduler checks a balance against -
        # meaningless, since there would be no such thing as "the" total.
        currencies = {instruction.amount.currency for instruction in self._instructions}
        if len(currencies) > 1:
            names = sorted(currency.name for currency in currencies)
            raise MixedInstructionCurrenciesError(
                f"a plan's instructions must share one currency, got {', '.join(names)}"
            )

        # Releasing to the available balance is only meaningful for money that
        # was locked. On an AVAILABLE-source plan it is a no-op with paperwork.
        if self.source is not PlanSource.LOCKED and any(
            instruction.action is PlannedAction.RELEASE
            for instruction in self._instructions
        ):
            raise ReleaseRequiresLockedSourceError(
                "a plan can only release funds it draws from the locked balance"
            )

    def __str__(self) -> str:
        return (
            f"plan {self.plan_id} ({self.status.value}, {self.schedule}, "
            f"{len(self._instructions)} instruction(s) from {self.source.value})"
        )
