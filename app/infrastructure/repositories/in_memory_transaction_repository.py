from app.domain.repositories.transaction_repository import TransactionRepository
from app.domain.money.exception import (
    TransactionNotFoundError
)

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