from dataclasses import dataclass
from dataclasses import field
import uuid
from datetime import datetime
from app.domain.money.transactionType import TransactionType
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.money import Money
from app.domain.money.exception import (
    TransactionAlreadySuccessfulError,
    TransactionAlreadyFailedError,
    TransactionAlreadyReversedError,
    InvalidTransactionStateError,
    InvalidTransactionWalletIDError,
    InvalidTransactionTypeError,
    InvalidTransactionAmountError,
    InvalidInternalReference,
    InvalidproviderReference,
    InvalidTransactionNarration,
    InvalidMetaData,
    InvalidTransactionDateStamp
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
        
        if self.status != TransactionStatus.PENDING:
            raise InvalidTransactionStateError("only pending transactions can be marked successful")
        
        self.status = TransactionStatus.SUCCESSFUL
        self.completed_at = datetime.now()


    def mark_failed(self):
        if self.status == TransactionStatus.FAILED:
            raise TransactionAlreadyFailedError 
        
        if self.status != TransactionStatus.PENDING:
            raise InvalidTransactionStateError("only pending transactions can be marked failed")

        self.status = TransactionStatus.FAILED
        self.completed_at = datetime.now() 


    def reverse(self):
        if self.status == TransactionStatus.REVERSED:
            raise TransactionAlreadyReversedError 

        if self.status != TransactionStatus.SUCCESSFUL:
            raise InvalidTransactionStateError 

        self.status = TransactionStatus.REVERSED  


    def __post_init__(self):
        if not isinstance(self.wallet_id, uuid.UUID):
            raise InvalidTransactionWalletIDError
        
        if not isinstance(self.type, TransactionType):
            raise InvalidTransactionTypeError
        
        if not isinstance(self.amount, Money):
            raise InvalidTransactionAmountError
        
        if self.amount.amount <= 0:
            raise InvalidTransactionAmountError
        
        if self.internal_reference == "":
            raise InvalidInternalReference
        
        if not isinstance(self.provider_reference, (str, type(None))):
            raise InvalidproviderReference
        
        if not isinstance(self.narration, (str, type(None))):
            raise InvalidTransactionNarration
        
        if not isinstance(self.metadata, dict):
            raise InvalidMetaData
        
        if self.status == TransactionStatus.PENDING and self.completed_at is not None:
            raise InvalidTransactionDateStamp("pending transaction must not have completed at")
        
        if self.status == TransactionStatus.SUCCESSFUL and self.completed_at is None:
            raise InvalidTransactionDateStamp("successful transaction must have valid date stamp")
        
        if self.status == TransactionStatus.FAILED and self.completed_at is None:
            raise InvalidTransactionDateStamp("failed transaction must have valid date stamp")

        if self.status == TransactionStatus.REVERSED and self.completed_at is None:
            raise InvalidTransactionDateStamp("reversed transaction must have valid date stamp")    