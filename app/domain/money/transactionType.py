from enum import Enum

class TransactionType(Enum):
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    LOCK_FUNDS = "lock_funds"
    SCHEDULED_RELEASE = "scheduled_release"