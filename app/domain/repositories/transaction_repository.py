from abc import ABC, abstractmethod
from app.domain.money.transaction import Transaction

class TransactionRepository(ABC):
    
    @abstractmethod
    def save(self, transaction: Transaction) -> Transaction :
        pass
        

