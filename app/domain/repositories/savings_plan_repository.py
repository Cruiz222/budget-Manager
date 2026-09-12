from abc import ABC, abstractmethod

from app.domain.planning.planStatus import PlanStatus
from app.domain.planning.savingsPlan import SavingsPlan


class SavingsPlanRepository(ABC):
    """Defines what a plan store must do (the domain does not care how)."""

    @abstractmethod
    def save(self, plan: SavingsPlan) -> SavingsPlan:
        pass

    @abstractmethod
    def get_owned(self, plan_id, user_id) -> SavingsPlan:
        """Return this owner's plan, or raise SavingsPlanNotFoundError.

        The same rule as ``WalletRepository.get_owned``, and a foreign plan is
        reported exactly as a missing one - same error, no branch between them.

        Scoping plans as well as wallets is not belt-and-braces. A plan reaches
        its wallet by id, so a caller who could read any plan could name any
        wallet through it; and a plan carries the amount, the cadence and the
        destination account, which is a good deal more than "a wallet exists".
        """
        pass

    @abstractmethod
    def get_by_wallet_id(self, wallet_id, user_id) -> list[SavingsPlan]:
        """Return this owner's plans drawn on this wallet, oldest first.

        Ordering is part of the contract so a caller can render the list as-is,
        and a wallet with no plans returns an empty list.

        Scoped by owner for the reason above, even though every caller reaches
        it having already proved the wallet is theirs - ``PlanService`` calls
        ``wallets.get_owned`` first, precisely so it can tell "no such wallet"
        from "wallet with no plans". That preceding check is what makes the
        answer *correct*; this argument is what makes it *safe*, so that a future
        caller which forgets the first still cannot read a stranger's plans.

        The two together are worth keeping rather than collapsing into one: the
        ownership proof and the empty-list case are different questions, and a
        single call that answered both would have to pick one error to raise.
        """
        pass

    @abstractmethod
    def list_by_status(self, status: PlanStatus) -> list[SavingsPlan]:
        """Return every plan in the given status, oldest first.

        **This is the one read in the codebase that crosses owners, and it is
        not a bypass - it is discovery.** A scheduler's job is to find work
        belonging to people it has not been introduced to; there is no actor to
        scope this by, because the actor does not exist until the query has
        answered. What keeps it honest is what it returns and who may call it:
        plans carry their ``user_id``, every wallet read that follows is scoped
        to that owner, and it is reachable only from the scheduler family.
        ``PlanService`` deliberately does not expose it - nothing a user can
        reach enumerates other users' plans.

        This - rather than a "find the overdue plans" query - is what a
        scheduler has to use, and the reason is worth knowing. A plan's next due
        date is *derived* in the domain:

            schedule.occurrence(completed_runs)

        A value the domain computes cannot be asked about in SQL. Storing a
        materialised ``next_due_at`` column would make the query cheap and would
        also make the drift guard optional - two sources of truth for when a
        plan is due, free to disagree. This design refuses that trade: the
        caller fetches the active set and filters it with ``is_due_at``.

        The limitation is real and scales with the number of *active* plans, not
        with history. If it ever bites, the honest fix is a materialised column
        written by the same method that advances the counter.
        """
        pass
