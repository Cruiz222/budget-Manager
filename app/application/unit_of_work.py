from abc import ABC, abstractmethod

from app.domain.repositories.transaction_repository import TransactionRepository
from app.domain.repositories.wallet_repository import WalletRepository


class UnitOfWork(ABC):
    """Boundary of one atomic business operation.

    The two aggregate repositories are tied to a single transactional store:
    writes made through ``.wallets`` and ``.transactions`` during the unit are
    not durable until ``commit()``. ``rollback()`` discards every write since
    the unit started. A concrete implementation opens one database transaction
    per unit.
    """

    wallets: WalletRepository
    transactions: TransactionRepository

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
