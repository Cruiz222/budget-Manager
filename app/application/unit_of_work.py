from abc import ABC, abstractmethod

from app.domain.repositories.plan_run_repository import PlanRunRepository
from app.domain.repositories.savings_plan_repository import SavingsPlanRepository
from app.domain.repositories.transaction_repository import TransactionRepository
from app.domain.repositories.wallet_repository import WalletRepository


class UnitOfWork(ABC):
    """Boundary of one atomic business operation.

    Every aggregate repository is tied to a single transactional store: writes
    made through ``.wallets``, ``.transactions``, ``.plans`` and ``.plan_runs``
    during the unit are not durable until ``commit()``. ``rollback()`` discards
    every write since the unit started. A concrete implementation opens one
    database transaction per unit.

    The four repositories exist on one unit - rather than one unit per aggregate
    - because a plan run has to move money *and* record what it did in the same
    breath. Saving the run and debiting the wallet are one business fact; if
    they could commit separately, a crash between them would leave a ledger
    entry with no explanation, or an explanation with no ledger entry.
    """

    wallets: WalletRepository
    transactions: TransactionRepository
    plans: SavingsPlanRepository
    plan_runs: PlanRunRepository

    @abstractmethod
    def commit(self) -> None:
        """Make all writes of this unit durable, atomically."""
        pass

    @abstractmethod
    def rollback(self) -> None:
        """Discard every write made since the unit started."""
        pass


class UnitOfWorkFactory(ABC):
    """Creates a fresh Unit of Work on demand.

    Each business operation should run in its own unit (its own connection and
    transaction), so callers depend on a factory rather than a shared instance.
    """

    @abstractmethod
    def start(self) -> UnitOfWork:
        pass
