"""Use cases for savings plans: create, inspect, steer, edit.

The plan equivalent of ``WalletService``. Each method opens its own Unit of Work
and commits or rolls back as one transaction.
"""

import uuid
from datetime import date

from app.application.unit_of_work import UnitOfWork, UnitOfWorkFactory
from app.domain.money.exception import CurrencyMismatchError
from app.domain.money.wallet import Wallet
from app.domain.planning.exception import (
    MissingPlanFundError,
    SavingsPlanNotFoundError,
    UnexpectedPlanFundError,
)
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

    def __init__(
        self, unit_of_work_factory: UnitOfWorkFactory, *, actor: uuid.UUID
    ):
        self._unit_of_work_factory = unit_of_work_factory
        # Who this service acts for, for every method it has. Required and
        # keyword-only, for the reason ``WalletService`` gives - and here it
        # scopes plans as well as wallets, so a service built for one user can
        # neither read nor steer another's.
        self._actor = actor

    # --- creating -----------------------------------------------------------

    def create_plan(
        self,
        wallet_id: uuid.UUID,
        name: str,
        source: PlanSource,
        schedule: Schedule,
        instructions: tuple[Instruction, ...],
        ends_on: date | None = None,
        fund_name: str | None = None,
    ) -> SavingsPlan:
        """Create a plan, refusing one the wallet cannot back or that names no pot.

        Two cross-aggregate rules live here rather than in either aggregate,
        because each needs both objects in hand at once and neither aggregate can
        see the other. The currency rule is the original one; the pot rule
        arrived with pots that can be named.

        **The pot rule, in four rows**, and the shape of it is a pairing rather
        than two independent choices:

            source       ``fund_name``  outcome
            locked       given          accepted - the plan draws on that pot
            locked       omitted        refused  - a locked plan must name its pot
            available    omitted        accepted
            available    given          refused  - an available plan spends no pot

        The two refusals are *not* symmetric, and the asymmetry is the product
        decision. A locked-source payout has to know which pot it takes the money
        from, now that "the locked balance" is a sum over several pots and not a
        number. An available-source plan spends the wallet's spendable balance,
        which has no pots in it at all.

        **The pot arrives as a name, and is resolved to an id here.** That is the
        same arrangement every pot-scoped operation uses (see
        ``WalletService.deposit_into_fund``): a name is what a human types, an id
        is what a row stores, and only the loaded wallet can turn one into the
        other. It matters for more than tidiness on this method, because it is
        also what makes the *order* of the two refusals right - the pairing is
        judged before the name is looked up, so ``--source available --from-fund
        Nonsense`` is refused for the pairing rather than for a name that was
        never going to be used. Resolving in the CLI instead would have meant the
        CLI knew the pairing rule, which is the duplication this method exists to
        avoid.

        **A named pot is loaded, not merely recorded.** ``wallet.fund_by_name``
        is called for exactly that reason: a plan naming a pot the wallet does
        not have should be refused here, at the moment a human typed the name,
        rather than at the first run - possibly weeks later, by a scheduler
        nobody is watching. Loading it is what turns "unknown pot" from a runtime
        surprise into a creation-time refusal, and it costs nothing, because the
        wallet is already loaded for the currency rule.

        **Why the pairing is checked here and the aggregate checks its own half.**
        ``SavingsPlan._validate_instructions`` independently refuses a ``fund_id``
        on anything but a locked-source plan, so on the creation path the
        available-with-a-pot row below is refused before the aggregate ever sees
        it and the aggregate's check is unreachable *from here*. That is not
        duplication to be tidied away: the aggregate's rule is what keeps the
        state from existing at all - through hydration, through a direct
        construction, through ``edit_instructions`` - and this one exists so the
        refusal names the two arguments the caller actually passed. Findings
        differ by door; the invariant does not.

        Note what is *still* deliberately not checked: whether the pot holds
        enough, or whether it has matured. Per the product rule, the scheduler
        does not concern itself with whether the money is there or ripe - it
        discovers that when a run fires, and records a blocked run if it is not.
        Checking here would mean a plan could not exist before it was
        affordable, which is backwards for a savings plan whose whole purpose is
        to become affordable.
        """
        # Before the unit of work opens, because these two need nothing from
        # storage and there is no transaction worth starting just to refuse one.
        if source is PlanSource.LOCKED and fund_name is None:
            raise MissingPlanFundError(
                "a plan that spends the locked balance must name the pot it "
                "draws from; pass the pot's name"
            )
        if source is not PlanSource.LOCKED and fund_name is not None:
            raise UnexpectedPlanFundError(
                "only a plan that spends the locked balance can name a pot; "
                "an available-balance plan has none to draw from"
            )

        uow = self._unit_of_work_factory.start()
        try:
            wallet = self._wallet(uow, wallet_id)

            # Raises FundNotFoundError for a pot this wallet does not have.
            fund_id = None
            if fund_name is not None:
                fund_id = wallet.fund_by_name(fund_name).fund_id

            plan = SavingsPlan(
                wallet_id=wallet_id,
                user_id=self._actor,
                name=name,
                source=source,
                schedule=schedule,
                _instructions=instructions,
                ends_on=ends_on,
                fund_id=fund_id,
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
            return self._plan(uow, plan_id)
        finally:
            uow.rollback()

    def plans_for_wallet(self, wallet_id: uuid.UUID) -> list[SavingsPlan]:
        """Every plan drawn on a wallet, oldest first.

        The wallet itself is validated first, so "no such wallet" and "a wallet
        with no plans yet" are distinguishable - the same distinction
        ``transactions_for_wallet`` makes, for the same reason.

        That first call is also the **ownership proof** for the list that
        follows, and the second call is scoped in its own right rather than
        leaning on it. The pair is not redundant: the proof is what makes the
        answer correct - it is how a wallet with no plans is told from a wallet
        that is not yours, which now raise different errors - and the filter is
        what makes it safe, so a caller who forgets the proof still cannot read a
        stranger's plans.
        """
        uow = self._unit_of_work_factory.start()
        try:
            self._wallet(uow, wallet_id)
            return uow.plans.get_by_wallet_id(wallet_id, self._actor)
        finally:
            uow.rollback()

    def runs_for_plan(self, plan_id: uuid.UUID) -> list[PlanRun]:
        """A plan's run history, earliest occurrence first.

        This is the record that answers "why did my plan stop?" - a BLOCKED run
        names its reason, and it is the only place that answer exists.
        """
        uow = self._unit_of_work_factory.start()
        try:
            self._plan(uow, plan_id)
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

    def _wallet(self, uow: UnitOfWork, wallet_id: uuid.UUID) -> Wallet:
        """The wallet this service's actor owns, or ``WalletNotFoundError``.

        The same single door ``WalletService`` routes every read through. Here it
        appears twice and each time proves something different: in
        ``create_plan`` it is what stops a plan being created against a stranger's
        wallet, and in ``plans_for_wallet`` it is what tells "no such wallet" from
        "wallet with no plans".
        """
        return uow.wallets.get_owned(wallet_id, self._actor)

    def _plan(self, uow: UnitOfWork, plan_id: uuid.UUID) -> SavingsPlan:
        """The plan this service's actor owns, or ``SavingsPlanNotFoundError``.

        Every read and every steering method starts here, so ``pause_plan`` on a
        stranger's plan is not refused - it is a plan that does not exist, which
        is the same answer ``get_plan`` gives and the same answer a plan that was
        never created gives.
        """
        return uow.plans.get_owned(plan_id, self._actor)

    def _apply(self, plan_id: uuid.UUID, transition) -> SavingsPlan:
        """Load a plan, apply a change, persist it atomically.

        Shared by every steering method because they are all the same shape:
        read, decide, write, commit. Unlike a money operation there is no ledger
        row - either the plan moves to its new state or it stays where it was.
        """
        uow = self._unit_of_work_factory.start()
        try:
            plan = self._plan(uow, plan_id)
            transition(plan)
            uow.plans.save(plan)
            uow.commit()
            return plan
        except BaseException:
            uow.rollback()
            raise


__all__ = ["PlanService", "SavingsPlanNotFoundError"]
