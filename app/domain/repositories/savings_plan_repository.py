from abc import ABC, abstractmethod

from app.domain.planning.planStatus import PlanStatus
from app.domain.planning.savingsPlan import SavingsPlan


class SavingsPlanRepository(ABC):
    """Defines what a plan store must do (the domain does not care how)."""

    @abstractmethod
    def save(self, plan: SavingsPlan) -> SavingsPlan:
        pass

    @abstractmethod
    def get_by_id(self, plan_id) -> SavingsPlan:
        """Return the plan with this id.

        Absence is unexpected here (the caller has a real plan_id in hand), so
        it raises SavingsPlanNotFoundError instead of returning None - the same
        split as WalletRepository.get_by_id.
        """
        pass

    @abstractmethod
    def get_by_wallet_id(self, wallet_id) -> list[SavingsPlan]:
        """Return every plan drawn on this wallet, oldest first.

        A wallet with no plans returns an empty list. Ordering is part of the
        contract so a caller can render the list as-is.
        """
        pass

    @abstractmethod
    def list_by_status(self, status: PlanStatus) -> list[SavingsPlan]:
        """Return every plan in the given status, oldest first.

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
