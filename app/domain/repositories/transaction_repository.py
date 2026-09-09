from abc import ABC, abstractmethod
from app.domain.money.transaction import Transaction

class TransactionRepository(ABC):

    @abstractmethod
    def save(self, transaction: Transaction) -> Transaction :
        pass
    @abstractmethod
    def get_by_id(self, transaction_id) -> Transaction:
        pass

    @abstractmethod
    def get_by_internal_reference(self, internal_reference: str) -> Transaction | None:
        """Return the transaction recorded under this idempotency key, or None.

        Not-found is an *expected* outcome here (it drives duplicate checks),
        so it returns None instead of raising like get_by_id.
        """
        pass