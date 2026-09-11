import uuid
from dataclasses import dataclass, field
from datetime import date, datetime

from app.domain.money.money import Money

from .exception import (
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

    ``completed_runs`` is the drift guard. Every due moment is derived as
    ``schedule.occurrence(completed_runs)`` rather than by adding a step to the
    last run's moment, so a plan can never slowly slide off its anchor. It also
    gives pause/resume the right behaviour for free: a paused plan does not
    advance the counter, so its next run is still the one that was missed.

    Two kinds of plan exist, and they have different rights. A plan that
    **releases** locked money is a promise the user made to themselves; it is
    irreversible, so neither ``cancel`` nor ``edit_instructions`` will touch it,
    and it must name the date the promise comes due. A plan that only **pays
    out** is an instruction, not a vow - editable and stoppable, whether it
    spends the locked balance or the available one.

    **A plan that spends the locked balance names the pot it draws on.** It has
    to: once the locked balance is a set of named pots, "spend the locked
    balance" no longer says which money leaves, and a plan that cannot say is a
    plan whose payout rule has to be invented. The reference is by identity
    (``fund_id``), not by name, for the same reason ``wallet_id`` is - a name is
    what a human types, and renaming a pot must not orphan a plan.

    Naming a pot is also what makes the business-pot exemption available at all.
    Whether money may leave a business pot before its maturity date is a question
    only the pot can answer (``Fund.authorises_early_payout``), and it needs the
    plan's ``created_at`` to answer it - which is a fact this aggregate holds and
    the pot cannot see.
    """

    wallet_id: uuid.UUID
    name: str
    source: PlanSource
    schedule: Schedule
    _instructions: tuple[Instruction, ...]
    #: The pot this plan draws on, or ``None``. Required for a LOCKED-source
    #: plan and refused for an AVAILABLE-source one - but note *where* each half
    #: of that rule is enforced, because the split is deliberate.
    #:
    #: "A pot may only be named by a locked plan" is checked here, in
    #: ``_validate_instructions``: it is a fact about this aggregate alone.
    #: "A locked plan must name one" is **not** checked here, and cannot be,
    #: because ``None`` is also what a plan saved before pots could be named
    #: looks like when it is loaded from the store. Enforcing it here would make
    #: every such plan unhydratable. It lives in ``PlanService.create_plan``,
    #: which is the door a *new* plan comes through.
    fund_id: uuid.UUID | None = None
    plan_id: uuid.UUID = field(default_factory=uuid.uuid4)
    status: PlanStatus = PlanStatus.ACTIVE
    completed_runs: int = 0
    # A *day*, not a moment, and deliberately so. The plan fires at an instant
    # and ends on a date: "until 2 July" means through 2 July. The two places
    # that compare it reduce the moment to its day - see ``record_run`` and
    # ``__post_init__`` - and ``Duration.end_from`` is where the day comes from.
    ends_on: date | None = None
    created_at: datetime = field(default_factory=datetime.now)

    @property
    def instructions(self) -> tuple[Instruction, ...]:
        """The plan's lines, as an immutable sequence.

        Exposed as a tuple rather than the list it was built from so that
        ``plan.instructions.append(...)`` is not merely discouraged but
        impossible - it would slip a line past every ``__post_init__`` check.
        Changing them is ``edit_instructions``, which runs the checks.
        """
        return self._instructions

    @property
    def is_irreversible(self) -> bool:
        """Whether this plan releases locked funds, and so cannot be undone.

        The rule the product rests on: locking is a commitment device, and a
        commitment you can revoke with one command is not one. So a plan holding
        a release is refused by ``cancel`` and by ``edit_instructions`` - the
        money leaves the locked balance when its set date arrives, and not
        before.

        Deferring a run is recoverable
        - the run stays queued and resume picks it back up - while cancelling
        discards it for good. Pausing forever is possible, and visible; the plan
        sits PAUSED still holding its place. That is a different act from
        cancelling, and it reads differently to anyone looking at the account.
        """
        return self._holds_a_release(self._instructions)

    @property
    def next_due_at(self) -> datetime:
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

    def is_due_at(self, moment: datetime) -> bool:
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

        # The plan's end is a *day*, so the test is against the day the next run
        # falls on - not the moment. A plan anchored at noon on 2 March and told
        # to end on 2 March has run exactly once: after recording, the next
        # occurrence is 2 April, whose day is past the end, so the plan retires.
        # Comparing the moments instead would ask whether noon on 2 April is
        # after midnight on 2 March, which is the same answer here but the wrong
        # question - and it would give the wrong answer for a plan ending on its
        # own last run's day.
        if self.ends_on is not None and self.next_due_at.date() > self.ends_on:
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
        """End the plan for good. Terminal - unlike pause, there is no way back.

        Refused outright for a plan that releases locked funds. That is not an
        extra rule bolted on: it is the whole point of locking. If the plan that
        unlocks the money could be cancelled, the lock would be a decoration.
        """
        if self.is_irreversible:
            raise IrreversibleReleasePlanError(
                "a plan that releases locked funds cannot be cancelled; "
                "its set date is the condition"
            )
        if self.status in (PlanStatus.CANCELLED, PlanStatus.COMPLETED):
            raise PlanAlreadyFinishedError(
                f"a {self.status.value} plan cannot be cancelled"
            )
        self.status = PlanStatus.CANCELLED

    def edit_instructions(self, instructions: tuple[Instruction, ...]):
        """Replace the plan's lines, for runs that have not happened yet.

        Editing is deliberately routed through the *same* validation the
        constructor uses rather than a second copy of it. Two code paths
        enforcing one invariant is a bug factory: the day the currency rule
        changes, one of them is forgotten, and the aggregate starts accepting
        plans it would never have been built with. Here there is one rule and
        one implementation, called from both doors.

        Already-recorded runs are untouched. They describe money that has
        already moved; rewriting them to match a later edit would make the
        ledger agree with the plan and disagree with reality.

        Refused for a plan that releases locked funds, and refused once the plan
        has finished - a cancelled or completed plan is history.

        **A pot-named plan may not lose its last payout.** That is the one thing
        editing may not do to a commitment, and it is a subtle thing to state:
        the payout *line* is the commitment, so deleting it is not editing the
        commitment, it is revoking it - the same act ``cancel`` performs, arriving
        through the door that is supposed to be the safe one. Its destination and
        its amount stay editable, because those change *who* and *how much*
        without changing *whether*.

        The check lives here rather than in ``_validate_instructions`` for a
        plain reason: it needs both lists. "Does the new list still contain a
        payout?" is only a question when you know the old one did, and
        ``_validate_instructions`` sees a candidate list and nothing else.
        """
        if self.status is not PlanStatus.ACTIVE:
            raise PlanNotActiveError(
                f"a {self.status.value} plan cannot be edited"
            )
        if self.is_irreversible:
            raise IrreversibleReleasePlanError(
                "a plan that releases locked funds cannot be edited; "
                "its set date is the condition"
            )
        if self.fund_id is not None and self._contains_a_payout(
            self._instructions
        ) and not self._contains_a_payout(instructions):
            raise CommittedPayoutRemovalError(
                "a plan that draws on a pot cannot have its payout removed; "
                "its destination and amount may change, but the commitment stands"
            )
        self._validate_instructions(instructions)
        self._instructions = instructions

    def __post_init__(self):
        if not isinstance(self.plan_id, uuid.UUID):
            raise InvalidPlanIDError("invalid plan id")

        if not isinstance(self.wallet_id, uuid.UUID):
            raise InvalidPlanWalletIDError("invalid wallet id")

        # A name is not decoration. Without one, a wallet holding five plans can
        # only be read as five UUIDs, and "cancel the rent plan" stops being
        # something the user can say. It is validated like a label rather than
        # like an identifier: a blank name is a name the user did not give.
        if not isinstance(self.name, str) or not self.name.strip():
            raise InvalidPlanNameError(
                f"name must be a non-empty string, got {self.name!r}"
            )

        if not isinstance(self.source, PlanSource):
            raise InvalidPlanSourceError(
                f"source must be a PlanSource, got {type(self.source).__name__}"
            )

        if self.fund_id is not None and not isinstance(self.fund_id, uuid.UUID):
            raise InvalidPlanFundIDError(
                f"fund_id must be a UUID or None, got {type(self.fund_id).__name__}"
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
            # Reduced to a day on purpose: a term ends on a day, so the end is
            # compared against the day of the first run. This is one of only two
            # places a moment and a deadline ever meet, and both do the same
            # thing. Without the .date() the comparison raises TypeError - a
            # datetime and a date do not order against each other - which is the
            # prompt to apply the rule rather than a reason to relax it.
            if self.ends_on < self.schedule.anchor.date():
                raise InvalidPlanEndDateError(
                    "ends_on cannot fall before the plan's first run"
                )

        self._validate_instructions(self._instructions)

    def _validate_instructions(self, instructions: tuple[Instruction, ...]):
        """Everything that has to be true of a plan's instruction list.

        One implementation, called by the constructor and by
        ``edit_instructions``. It takes the list as an argument rather than
        reading ``self._instructions`` so that it can check a *candidate* list
        before the aggregate commits to it - validate, then assign.

        The first draft of this method checked only the rules that mention the
        instruction list and left the rest in the constructor. Two things then
        slipped through the edit door: a list could be assigned in place of a
        tuple, and an open-ended release plan could be created by editing. Both
        checks had been *written*, and both were unreachable from the second
        door.

        The lesson is about where the cut is made. Splitting by "which checks
        mention this field" produced a seam that looked principled and was
        arbitrary; splitting by "which checks must hold together" does not. A
        plan's instructions are only valid *relative to the plan's own source
        and end date*, so those checks belong here even though they read
        ``self``. The rule: a validation method owns every rule that can be
        broken by the thing it validates.
        """
        # First, because everything below iterates: a list here would mean the
        # aggregate holds a mutable sequence, which is the exact thing the
        # tuple requirement exists to prevent.
        if not isinstance(instructions, tuple):
            raise InvalidPlanInstructionsError(
                f"instructions must be a tuple, got {type(instructions).__name__}"
            )

        if not instructions:
            raise EmptyPlanInstructionsError("a plan must have at least one instruction")

        for instruction in instructions:
            if not isinstance(instruction, Instruction):
                raise InvalidPlanInstructionsError(
                    f"every instruction must be an Instruction, "
                    f"got {type(instruction).__name__}"
                )

        # A plan moves one currency. Mixing them inside a single run would make
        # total_to_move - the number the scheduler checks a balance against -
        # meaningless, since there would be no such thing as "the" total.
        currencies = {instruction.amount.currency for instruction in instructions}
        if len(currencies) > 1:
            names = sorted(currency.name for currency in currencies)
            raise MixedInstructionCurrenciesError(
                f"a plan's instructions must share one currency, got {', '.join(names)}"
            )

        # Releasing to the available balance is only meaningful for money that
        # was locked. On an AVAILABLE-source plan it is a no-op with paperwork.
        if self.source is not PlanSource.LOCKED and any(
            instruction.action is PlannedAction.RELEASE
            for instruction in instructions
        ):
            raise ReleaseRequiresLockedSourceError(
                "a plan can only release funds it draws from the locked balance"
            )

        # A plan that releases locked funds is irreversible, so it must say when
        # the promise comes due. Without an end date it would be both
        # uncancellable and endless - locked money with no way out at all, which
        # is strictly worse than the temptation locking exists to prevent. The
        # rule is derived, not chosen: "irreversible until the set date" only
        # means something if there is a set date.
        #
        # Checked against the *candidate* list, not self._instructions, so that
        # editing a payout plan into a release plan is refused unless the plan
        # already has an end date to anchor the promise to.
        if self._holds_a_release(instructions) and self.ends_on is None:
            raise ReleasePlanRequiresEndDateError(
                "a plan that releases locked funds must set the date it ends"
            )

        # A pot is a part of the *locked* balance, so a plan that spends the
        # available balance naming one is referring to money it will never touch
        # - a rule broken by the pairing, which is why it is checked here.
        #
        # The converse - a locked plan that names no pot - is deliberately not
        # here. It cannot be: a plan saved before pots could be named loads with
        # ``fund_id = None``, so this check would refuse to hydrate the user's own
        # history. ``PlanService.create_plan`` holds that half, because it is the
        # only door a *new* plan comes through. See the field's comment.
        if self.fund_id is not None and self.source is not PlanSource.LOCKED:
            raise FundRequiresLockedSourceError(
                "a plan can only draw on a pot if it spends the locked balance"
            )

    @staticmethod
    def _contains_a_payout(instructions) -> bool:
        """Whether a list of instructions contains a payout.

        The sibling of ``_holds_a_release``, and static for the same reason: it
        is asked about the plan's own lines and about a candidate edit's, and
        neither question needs an instance.
        """
        return any(
            instruction.action is PlannedAction.PAYOUT
            for instruction in instructions
        )

    @staticmethod
    def _holds_a_release(instructions) -> bool:
        """Whether a list of instructions contains a release.

        Static, and takes the list, because it is asked about candidates as well
        as about the plan's own instructions - ``is_irreversible`` is one caller,
        validation is the other.
        """
        return any(
            instruction.action is PlannedAction.RELEASE
            for instruction in instructions
        )

    def __str__(self) -> str:
        return (
            f"plan {self.name!r} {self.plan_id} ({self.status.value}, "
            f"{self.schedule}, {len(self._instructions)} instruction(s) "
            f"from {self.source.value})"
        )
