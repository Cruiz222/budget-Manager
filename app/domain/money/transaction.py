from dataclasses import dataclass
from dataclasses import field
import uuid
from datetime import datetime
from app.domain.money.transactionType import TransactionType
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.money import Money

@dataclass
class Transaction:
      transaction_id: uuid.UUID = field(default_factory=uuid.uuid4)
      wallet_id: uuid.UUID
      type:    TransactionType
      amount: Money
      status:  TransactionStatus = TransactionStatus.PENDING
      internal_reference: str
      provider_reference: str | None
      narration:  str | None
      metadata: dict[str, object]
      created_at: datetime = field(default_factory=datetime.now)
      completed_at: datetime | None = None