from dataclasses import dataclass, field, InitVar
import uuid
from datetime import datetime
from types import MappingProxyType
from app.domain.money.transactionType import TransactionType
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.money import Money
from app.domain.money.destination import Destination
from app.domain.money.exception import (
    TransactionAlreadySuccessfulError,
    TransactionAlreadyFailedError,
    TransactionAlreadyReversedError,
    InvalidTransactionStateError,
    InvalidTransactionStatusError,
    InvalidTransactionWalletIDError,
    InvalidTransactionTypeError,
    InvalidTransactionAmountError,
    InvalidTransactionDestinationError,
    MissingDestinationError,
    UnexpectedDestinationError,
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
    _destination: Destination | None

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
    status: TransactionStatus = TransactionStatus.PENDING,
    destination: Destination | None = None,
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

        self._destination = destination

        self._transaction_id = transaction_id or uuid.uuid4()

        # The default is PENDING (a brand-new transaction). The persistence
        # layer passes an explicit status (SUCCESSFUL / FAILED / REVERSED) when
        # re-hydrating a stored transaction. Restoring state via the constructor
        # is NOT a live transition, so it bypasses the guards in mark_*().
        self._status = status

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

    @property
    def destination(self) -> Destination | None:
        return self._destination                                   


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

        if not isinstance(self.internal_reference, str) or self.internal_reference.strip() == "":
            raise InvalidInternalReference

        if not isinstance(self.provider_reference, (str, type(None))):
            raise InvalidproviderReference

        if not isinstance(self.narration, (str, type(None))):
            raise InvalidTransactionNarration
        
        if not isinstance(self._metadata, dict):
            raise InvalidMetaData

        if not isinstance(self.status, TransactionStatus):
            raise InvalidTransactionStatusError("invalid transaction status")

        # --- Lifecycle / date-stamp invariants ---
        # PENDING: no completion yet.
        # SUCCESSFUL / FAILED: left PENDING, so completed_at is required.
        # REVERSED: was SUCCESSFUL, so completed_at is required and reversed_at
        #           records when it was reversed.
        if self.status == TransactionStatus.PENDING and self.completed_at is not None:
            raise InvalidTransactionDateStamp(
                "pending transaction must not have completed_at"
            )

        if self.status == TransactionStatus.SUCCESSFUL and self.completed_at is None:
            raise InvalidTransactionDateStamp(
                "successful transaction must have completed_at"
            )

        if self.status == TransactionStatus.FAILED and self.completed_at is None:
            raise InvalidTransactionDateStamp(
                "failed transaction must have completed_at"
            )

        if self.status == TransactionStatus.REVERSED and self.completed_at is None:
            raise InvalidTransactionDateStamp(
                "reversed transaction must have completed_at"
            )

        if self.status == TransactionStatus.REVERSED and self.reversed_at is None:
            raise InvalidTransactionDateStamp(
                "reversed transaction must have reversed_at"
            )

        if self.status != TransactionStatus.REVERSED and self.reversed_at is not None:
            raise InvalidTransactionDateStamp(
                "only reversed transactions may have reversed_at"
            )

        # --- Destination invariant ---
        # A payout sends money to an external account, so it must record where
        # it went - a payout with no destination is a payment to nowhere. No
        # other type has a counterparty (a deposit's far end is not the wallet's
        # business; lock/unlock never leave), so carrying one would be a bug.
        # Both directions are enforced, exactly as the date-stamp rules above
        # are: an impossible record cannot be constructed in the first place.
        if self.destination is not None and not isinstance(self.destination, Destination):
            raise InvalidTransactionDestinationError(
                f"destination must be a Destination, not "
                f"{type(self.destination).__name__}"
            )

        if self.type is TransactionType.PAYOUT and self.destination is None:
            raise MissingDestinationError(
                "a payout transaction must record a destination"
            )

        if self.type is not TransactionType.PAYOUT and self.destination is not None:
            raise UnexpectedDestinationError(
                f"a {self.type.value} transaction must not carry a destination"
            )