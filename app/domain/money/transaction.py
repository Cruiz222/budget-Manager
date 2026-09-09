from dataclasses import dataclass, field, InitVar
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

@dataclass(init=False)
class Transaction:
    _wallet_id: uuid.UUID
    _type: TransactionType
    _amount: Money
    internal_reference: str

    provider_reference: str | None
    narration: str | None
    _metadata: dict[str, object]

    _transaction_id: uuid.UUID
    _status: TransactionStatus

    created_at: datetime
    completed_at: datetime | None
    reversed_at: datetime | None


    def __init__(
    self,
    wallet_id: uuid.UUID,
    type: TransactionType,
    amount: Money,
    internal_reference: str,
    provider_reference: str | None = None,
    narration: str | None = None,
    metadata: dict[str, object] | None = None,
    transaction_id: uuid.UUID | None = None,
    created_at: datetime | None = None,
    completed_at: datetime | None = None,
    reversed_at: datetime | None = None,
):
        self._wallet_id = wallet_id
        self._type = type
        self._amount = amount
        self._internal_reference = internal_reference

        self._provider_reference = provider_reference
        self._narration = narration
        if metadata is None:
            metadata = {}

        if not isinstance(metadata, dict):
            raise InvalidMetaData

        self._metadata = dict(metadata)

        self._transaction_id = transaction_id or uuid.uuid4()

        self._status = TransactionStatus.PENDING

        self._created_at = created_at or datetime.now()
        self._completed_at = completed_at 
        self._reversed_at = reversed_at

        self.__post_init__()

    @property
    def status(self) -> TransactionStatus:
        return self._status

    @property
    def narration(self):
        return self._narration

    @property
    def metadata(self):
        return MappingProxyType(self._metadata) 

    @property
    def amount(self) -> Money:
        return self._amount 

    @property
    def wallet_id(self):
        return self._wallet_id  

    @property
    def transaction_id(self):
        return self._transaction_id

    @property
    def type(self):
        return self._type  

    @property
    def internal_reference(self):
        return self._internal_reference

    @property
    def provider_reference(self):
        return self._provider_reference

    @property
    def created_at(self):
        return self._created_at

    @property
    def completed_at(self):
        return self._completed_at

    @property
    def reversed_at(self):
        return self._reversed_at                                   


    def mark_successful(self):
        if self.status == TransactionStatus.SUCCESSFUL:
            raise TransactionAlreadySuccessfulError("transaction is already successful")
        
        if self.status != TransactionStatus.PENDING:
            raise InvalidTransactionStateError("only pending transactions can be marked successful")
        
        self._status = TransactionStatus.SUCCESSFUL
        self._completed_at = datetime.now()


    def mark_failed(self):
        if self.status == TransactionStatus.FAILED:
            raise TransactionAlreadyFailedError 
        
        if self.status != TransactionStatus.PENDING:
            raise InvalidTransactionStateError("only pending transactions can be marked failed")

        self._status = TransactionStatus.FAILED
        self._completed_at = datetime.now() 


    def reverse(self):
        if self.status == TransactionStatus.REVERSED:
            raise TransactionAlreadyReversedError 

        if self.status != TransactionStatus.SUCCESSFUL:
            raise InvalidTransactionStateError 

        self._reversed_at = datetime.now()
        self._status = TransactionStatus.REVERSED   


    def __post_init__(self):
        if not isinstance(self.wallet_id, uuid.UUID):
            raise InvalidTransactionWalletIDError

        if not isinstance(self.type, TransactionType):
            raise InvalidTransactionTypeError

        if not isinstance(self._amount, Money):
            raise InvalidTransactionAmountError

        if self._amount.amount <= 0:
            raise InvalidTransactionAmountError

        if self.internal_reference == "":
            raise InvalidInternalReference

        if not isinstance(self.provider_reference, (str, type(None))):
            raise InvalidproviderReference

        if not isinstance(self.narration, (str, type(None))):
            raise InvalidTransactionNarration
        
        if not isinstance(self._metadata, dict):
            raise InvalidMetaData

        if self.status == TransactionStatus.PENDING and self.completed_at is not None:
            raise InvalidTransactionDateStamp(
            "pending transaction must not have completed at"
           )

        if self.status == TransactionStatus.SUCCESSFUL and self.completed_at is None:
            raise InvalidTransactionDateStamp(
                  "successful transaction must have valid date stamp"
                )

        if self.status == TransactionStatus.FAILED and self.completed_at is None:
            raise InvalidTransactionDateStamp(
                "failed transaction must have valid date stamp"
            )

        if self.status == TransactionStatus.REVERSED and self.completed_at is None:
            raise InvalidTransactionDateStamp(
                "reversed transaction must have valid date stamp"
            )