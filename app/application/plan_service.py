"""Use cases for savings plans: create, inspect, steer, edit.

The plan equivalent of ``WalletService``. Each method opens its own Unit of Work
and commits or rolls back as one transaction.
"""

import uuid
from datetime import date

from app.application.unit_of_work import UnitOfWorkFactory
from app.domain.money.exception import CurrencyMismatchError
from app.domain.planning.exception import SavingsPlanNotFoundError
from app.domain.planning.instruction import Instruction
from app.domain.planning.planRun import PlanRun
from app.domain.planning.planSource import PlanSource
from app.domain.planning.savingsPlan import SavingsPlan
from app.domain.planning.schedule import Schedule


class PlanService:
    """Makes the planning use cases callable by id.

    Deliberately *not* merged into WalletService, even though both are thin
    wrappers over the same kind of unit of work. They are separate services
    because they are separate aggregates: a caller that wants to read a plan
    should not have to hold a handle on the thing that moves money. Merging them
    would also mean every plan read carries the wallet's methods around with it,
    which is how a boundary quietly stops being one.

    What they *do* share is the unit of work, and that is not a boundary
    violation - it is the mechanism. A plan run has to move money and record
    what it did in one transaction, so the repositories for both aggregates live
    on the same unit. See ``UnitOfWork``.
    """

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory):
        self._unit_of_work_factory = unit_of_work_factory

    # --- creating -----------------------------------------------------------

    def create_plan(
        self,
        wallet_id: uuid.UUID,
        name: str,
        source: PlanSource,
        schedule: Schedule,
        instructions: tuple[Instruction, ...],
        ends_on: date | None = None,
    ) -> SavingsPlan:
        """Create a plan, refusing one whose currency the wallet cannot fund.

        That refusal is the reason this method exists rather than the CLI
        constructing a ``SavingsPlan`` and saving it. The rule is:

            a plan's instructions must be denominated in the wallet's currency

        and it is a *cross-aggregate* rule - so neither aggregate can enforce it.
        The plan cannot see the wallet, the wallet has never heard of a plan, and
        giving either one a reference to the other to check it would dissolve the
        boundary that makes them separately loadable in the first place. The use
        case is the only place both are in hand at once, so the use case holds
        the rule. This is the same reasoning as the plan's own docstring, seen
        from the other side.

        Note what is deliberately *not* checked: whether the wallet's locked
        balance covers the plan. Per the product rule, the scheduler does not
        concern itself with whether the money is there - it discovers that when
        a run fires, and records a blocked run if it is not. Checking here would
        mean a plan could not exist before it was affordable, which is backwards
        for a savings plan whose whole purpose is to become affordable.
        """
        uow = self._unit_of_work_factory.start()
        try:
            wallet = uow.wallets.get_by_id(wallet_id)

            plan = SavingsPlan(
                wallet_id=wallet_id,
                name=name,
                source=source,
                schedule=schedule,
                _instructions=instructions,
                ends_on=ends_on,
            )

            # The aggregate has already guaranteed the instructions share one
            # currency, so asking the plan as a whole is enough - there is no
            # "which instruction" question left to ask.
            if plan.total_to_move.currency is not wallet.currency:
                raise CurrencyMismatchError(
                    f"a plan in {plan.total_to_move.currency.name} cannot be "
                    f"funded by a {wallet.currency.name} wallet"
                )

            uow.plans.save(plan)
            uow.commit()
            return plan
        except BaseException:
            uow.rollback()
            raise

    # --- reading ------------------------------------------------------------

    def get_plan(self, plan_id: uuid.UUID) -> SavingsPlan:
        """Read one plan. Absence raises SavingsPlanNotFoundError."""
        uow = self._unit_of_work_factory.start()
        try:
            return uow.plans.get_by_id(plan_id)
        finally:
            uow.rollback()

    def plans_for_wallet(self, wallet_id: uuid.UUID) -> list[SavingsPlan]:
        """Every plan drawn on a wallet, oldest first.

        The wallet itself is validated first, so "no such wallet" and "a wallet
        with no plans yet" are distinguishable - the same distinction
        ``transactions_for_wallet`` makes, for the same reason.
        """
        uow = self._unit_of_work_factory.start()
        try:
            uow.wallets.get_by_id(wallet_id)
            return uow.plans.get_by_wallet_id(wallet_id)
        finally:
            uow.rollback()

    def runs_for_plan(self, plan_id: uuid.UUID) -> list[PlanRun]:
        """A plan's run history, earliest occurrence first.

        This is the record that answers "why did my plan stop?" - a BLOCKED run
        names its reason, and it is the only place that answer exists.
        """
        uow = self._unit_of_work_factory.start()
        try:
            uow.plans.get_by_id(plan_id)
            return uow.plan_runs.list_by_plan_id(plan_id)
        finally:
            uow.rollback()

    # --- steering -----------------------------------------------------------

    def pause_plan(self, plan_id: uuid.UUID) -> SavingsPlan:
        """Stop a plan being due, without losing its place in the schedule."""
        return self._apply(plan_id, SavingsPlan.pause)

    def resume_plan(self, plan_id: uuid.UUID) -> SavingsPlan:
        """Return a paused plan to service, at the occurrence it left off on."""
        return self._apply(plan_id, SavingsPlan.resume)

    def cancel_plan(self, plan_id: uuid.UUID) -> SavingsPlan:
        """End a plan for good.

        Refused by the aggregate for a plan that releases locked funds. The
        rejection is not translated or softened here: the use case has nothing
        to add to the rule, and inventing a friendlier error at this layer would
        mean two places knew what the rule was.
        """
        return self._apply(plan_id, SavingsPlan.cancel)

    def edit_instructions(
        self, plan_id: uuid.UUID, instructions: tuple[Instruction, ...]
    ) -> SavingsPlan:
        """Replace a plan's lines.

        The aggregate re-validates the candidate exactly as it would at
        construction, so this method adds nothing but the transaction. That is
        the point: the rule about what a plan's instructions may be lives in one
        method, and both doors call it.
        """
        def edit(plan: SavingsPlan) -> None:
            plan.edit_instructions(instructions)

        return self._apply(plan_id, edit)

    def _apply(self, plan_id: uuid.UUID, transition) -> SavingsPlan:
        """Load a plan, apply a change, persist it atomically.

        Shared by every steering method because they are all the same shape:
        read, decide, write, commit. Unlike a money operation there is no ledger
        row - either the plan moves to its new state or it stays where it was.
        """
        uow = self._unit_of_work_factory.start()
        try:
            plan = uow.plans.get_by_id(plan_id)
            transition(plan)
            uow.plans.save(plan)
            uow.commit()
            return plan
        except BaseException:
            uow.rollback()
            raise


__all__ = ["PlanService", "SavingsPlanNotFoundError"]
