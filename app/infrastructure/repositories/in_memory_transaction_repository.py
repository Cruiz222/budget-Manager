from app.domain.repositories.transaction_repository import TransactionRepository

class InMemoryTransactionRepository(TransactionRepository):
    def __init__(self):
        self.transactions = {}