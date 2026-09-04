from dataclasses import dataclass
from dataclasses import field
import uuid
from datetime import datetime
from app.domain.money.transactionType import TransactionType
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.money import Money
from app.domain.money.exception import (
    TransactionAlreadySuccessfulError
)

@dataclass
class Transaction:
    wallet_id: uuid.UUID
    type: TransactionType
    amount: Money
    internal_reference: str

    provider_reference: str | None = None
    narration: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    transaction_id: uuid.UUID = field(default_factory=uuid.uuid4)
    status: TransactionStatus = TransactionStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None


    def mark_successful(self):
        if self.status == TransactionStatus.SUCCESSFUL:
            raise TransactionAlreadySuccessfulError("transaction is already successful")
        
        self.status = TransactionStatus.SUCCESSFUL
        self.completed_at = datetime.now()