from abc import ABC, abstractmethod
from app.domain.money.transaction import Transaction

class TransactionRepository(ABC):
    
    @abstractmethod
    def save(self, transaction: Transaction) -> Transaction :
        pass
    @abstractmethod    
    def get_by_id(self, transaction_id) -> Transaction:
        pass
    

