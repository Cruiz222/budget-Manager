from decimal import Decimal

from app.domain.repositories.transaction_repository import TransactionRepository
from app.domain.money.exception import (
    TransactionNotFoundError
)
from app.domain.money.money import Money
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.transactionType import TransactionType

#: The two types that move value across the system's edge.
_OUTFLOWS = (TransactionType.WITHDRAWAL, TransactionType.PAYOUT)

#: The two statuses whose money is not in the wallet.
#:
#: Named here rather than inlined so that the rule is readable in one place: a
#: row counts when its money is not in the wallet, which is why a ``FAILED`` hold
#: (given back) and a ``REVERSED`` transfer (returned by the bank) are both out.
#: ``tests/infrastructure/repositories/test_in_memory_transaction_repository.py``
#: pins the same rule against the SQL adapter, which is the copy that matters -
#: this one exists so the in-memory store is not a second opinion.
_COUNTED = (TransactionStatus.PENDING, TransactionStatus.SUCCESSFUL)

class InMemoryTransactionRepository(TransactionRepository):
    def __init__(self):
        self.transactions = {}

    def save(self, transaction):
        self.transactions[transaction.transaction_id] = transaction
        return transaction


    def get_by_id(self, transaction_id):
        if transaction_id not in self.transactions:
            raise TransactionNotFoundError

        return self.transactions[transaction_id]


    def get_by_internal_reference(self, internal_reference):
        for transaction in self.transactions.values():
            if transaction.internal_reference == internal_reference:
                return transaction
        return None

    def get_by_wallet_id(self, wallet_id):
        ledger = [
            transaction
            for transaction in self.transactions.values()
            if transaction.wallet_id == wallet_id
        ]
        # Oldest first (by creation time), mirroring the SQL ORDER BY.
        return sorted(ledger, key=lambda transaction: transaction.created_at)

    def get_by_provider_reference(self, provider_reference):
        for transaction in self.transactions.values():
            if transaction.provider_reference == provider_reference:
                return transaction
        return None

    def list_awaiting_provider(self):
        awaiting = [
            transaction
            for transaction in self.transactions.values()
            if transaction.status is TransactionStatus.PENDING
            and transaction.provider_reference is not None
        ]
        # Oldest first, mirroring the SQL ORDER BY - and by the same tiebreak,
        # because ``created_at`` is stored at second resolution in both stores
        # and two rows written in one second must order the same way in each.
        return sorted(
            awaiting,
            key=lambda transaction: (
                transaction.created_at,
                str(transaction.transaction_id),
            ),
        )

    def outflow_total_between(self, wallet_id, start, end, currency):
        """The day's outflow, mirroring the SQL adapter's filter exactly.

        See the port for what counts and why. The mirroring is the point of this
        method rather than an implementation detail: this store keeps one row per
        transaction (``save`` overwrites), which is what the SQL store does too,
        so the same filter over the same rows gives the same total - and the
        suite's tests of the rule can be written against either.

        The comparisons are Python's rather than SQL's, and they behave
        identically here because both stores hold naive local moments and
        ``start <= created_at < end`` is the same half-open window the SQL spells
        as two comparisons.

        ``self.transactions`` is a dict, so this walks every row in the store
        rather than only the wallet's. That is fine for a store that exists to be
        small - and it is not indexed by wallet, so indexing it here would be a
        second index to keep true.
        """
        total = Money(Decimal("0.00"), currency)
        for transaction in self.transactions.values():
            if transaction.wallet_id != wallet_id:
                continue
            if transaction.type not in _OUTFLOWS:
                continue
            if transaction.status not in _COUNTED:
                continue
            if transaction.amount.currency is not currency:
                continue
            if not start <= transaction.created_at < end:
                continue
            total = total + transaction.amount
        return total