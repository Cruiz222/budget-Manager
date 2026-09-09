from dataclasses import dataclass
from dataclasses import field
import uuid
from datetime import datetime
from types import MappingProxyType
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
    amount: initVar[Money]
    _amount: Money = field(init=False)
    internal_reference: str
    provider_reference: str | None = None
    narration: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    transaction_id: uuid.UUID = field(default_factory=uuid.uuid4)
    _status: TransactionStatus = field(
    default=TransactionStatus.PENDING,
    init=False
)
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None
    reversed_at: datetime | None = None


    @property
    def status(self) -> TransactionStatus:
        return self._status

    @property
    def metadata(self):
        return MappingProxyType(self._metadata) 

    @property
    def amount(self) -> Money:
        return self._amount        


    def mark_successful(self):
        if self.status == TransactionStatus.SUCCESSFUL:
            raise TransactionAlreadySuccessfulError("transaction is already successful")
        
        if self.status != TransactionStatus.PENDING:
            raise InvalidTransactionStateError("only pending transactions can be marked successful")
        
        self._status = TransactionStatus.SUCCESSFUL
        self.completed_at = datetime.now()


    def mark_failed(self):
        if self.status == TransactionStatus.FAILED:
            raise TransactionAlreadyFailedError 
        
        if self.status != TransactionStatus.PENDING:
            raise InvalidTransactionStateError("only pending transactions can be marked failed")

        self._status = TransactionStatus.FAILED
        self.completed_at = datetime.now() 


    def reverse(self):
        if self.status == TransactionStatus.REVERSED:
            raise TransactionAlreadyReversedError 

        if self.status != TransactionStatus.SUCCESSFUL:
            raise InvalidTransactionStateError 

        self.reversed_at = datetime.now()
        self._status = TransactionStatus.REVERSED   


    def __post_init__(self):
        if not isinstance(self.wallet_id, uuid.UUID):
            raise InvalidTransactionWalletIDError
        
        if not isinstance(self.type, TransactionType):
            raise InvalidTransactionTypeError
        
        if not isinstance(self._amount, Money):
            raise InvalidTransactionAmountError
        
        if self.amount.amount <= 0:
            raise InvalidTransactionAmountError

        self._amount = amount    
        
        if self.internal_reference == "":
            raise InvalidInternalReference
        
        if not isinstance(self.provider_reference, (str, type(None))):
            raise InvalidproviderReference
        
        if not isinstance(self.narration, (str, type(None))):
            raise InvalidTransactionNarration
        
        if not isinstance(self.metadata, dict):
            raise InvalidMetaData

        self.metadata = MappingProxyType(self.metadata)   
        
        if self.status == TransactionStatus.PENDING and self.completed_at is not None:
            raise InvalidTransactionDateStamp("pending transaction must not have completed at")
        
        if self.status == TransactionStatus.SUCCESSFUL and self.completed_at is None:
            raise InvalidTransactionDateStamp("successful transaction must have valid date stamp")
        
        if self.status == TransactionStatus.FAILED and self.completed_at is None:
            raise InvalidTransactionDateStamp("failed transaction must have valid date stamp")

        if self.status == TransactionStatus.REVERSED and self.completed_at is None:
            raise InvalidTransactionDateStamp("reversed transaction must have valid date stamp")    