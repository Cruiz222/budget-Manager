from enum import Enum

class TransactionStatus(Enum):
    PENDING = "pending"
    SUCCESSFUL = "successful"
    FAILED = "failed"
    REVERSED = "reversed"