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

    @abstractmethod
    def get_by_wallet_id(self, wallet_id) -> list[Transaction]:
        """Return every transaction on a wallet's ledger, oldest first.

        A wallet with no transactions returns an empty list. Ordering is part
        of the contract so a caller can render the ledger as-is.
        """
        pass

    @abstractmethod
    def get_by_provider_reference(self, provider_reference: str) -> Transaction | None:
        """Return the transaction recorded under a provider key, or None.

        A provider reference is the pay-out processor's own idempotency key
        (e.g. a Paystack reference), so at most one transaction carries it.
        Not-found returns None, mirroring get_by_internal_reference.
        """
        pass