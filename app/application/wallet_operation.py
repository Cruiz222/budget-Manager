from abc import ABC, abstractmethod
import uuid

from app.domain.money.destination import Destination
from app.domain.money.exception import InvalidAmountError, MoneyError
from app.domain.money.money import Money
from app.domain.money.transaction import Transaction
from app.domain.money.transactionType import TransactionType
from app.domain.money.wallet import Wallet
from app.domain.repositories.transaction_repository import TransactionRepository


class WalletOperation(ABC):
    """Shared skeleton for a single-wallet money operation.

    Deposit, withdrawal, lock, release and payout all run the same guarded flow,
    so the flow lives here once instead of being copy-pasted:

        validate amount
        -> deduplicate by internal_reference
        -> record the attempt as PENDING
        -> apply through the wallet (it decides)
        -> record SUCCESSFUL, or FAILED on rejection and re-raise

    Concrete use cases supply only the things that differ:
      - transaction_type:  the TransactionType recorded on the ledger
      - _apply():          the wallet method that performs the operation

    This is the Template Method pattern: the base class owns the algorithm and
    lets subclasses fill in the variable steps.

    ``destination`` is passed straight through to the Transaction, which is
    where the rule about it lives: a payout must carry one, every other type
    must not. The base does not police it - it only delivers it.

    ``fund_id`` is the same shape of pass-through, for the same reason. A
    pot-scoped operation records which pot it moved money through, and the base
    has no opinion about when that must be set: the ledger's rules about it are
    the Transaction's, and there is currently only one - that a fund id, if
    present, is a UUID. Note the asymmetry with ``destination``, which *does*
    have a rule tying it to the transaction type. A payout in this phase has no
    single pot (see ``Wallet.payout_from_locked``), so "PAYOUT implies a fund"
    would be a rule the code cannot keep.
    """

    #: Transaction type recorded for this operation (set by each subclass).
    transaction_type: TransactionType

    def __init__(
        self,
        wallet: Wallet,
        transaction_repository: TransactionRepository,
        fund_id: uuid.UUID | None = None,
    ):
        self.wallet = wallet
        self.transaction_repository = transaction_repository
        self.fund_id = fund_id

    def execute(
        self,
        amount: Money,
        internal_reference: str,
        destination: Destination | None = None,
    ) -> Transaction:
        # 1. Command validation happens before any record exists.
        self._validate_amount(amount)

        # 2. Idempotency: a processed internal_reference must not run twice.
        existing = self.transaction_repository.get_by_internal_reference(
            internal_reference
        )
        if existing is not None:
            return existing

        # 3. Record intent BEFORE touching the wallet. A payout reaches here
        #    with a destination; every other type reaches here without one. The
        #    Transaction rejects the wrong combination before anything is saved.
        transaction = Transaction(
            wallet_id=self.wallet.wallet_id,
            type=self.transaction_type,
            amount=amount,
            internal_reference=internal_reference,
            destination=destination,
            fund_id=self.fund_id,
        )
        self.transaction_repository.save(transaction)

        try:
            # 4. The wallet decides, raising before mutating on rejection.
            self._apply(amount)
        except MoneyError:
            # 4a. Rejected - record FAILED for the audit trail, then surface
            #     the original error.
            transaction.mark_failed()
            self.transaction_repository.save(transaction)
            raise

        # 4b. The operation succeeded - the transaction is complete.
        transaction.mark_successful()
        self.transaction_repository.save(transaction)

        return transaction

    def _validate_amount(self, amount: Money) -> None:
        """Reject amounts no operation can record. Most operations treat zero
        and negative the same; WithdrawMoney overrides this for its finer
        zero/negative error split."""
        if not isinstance(amount, Money):
            raise InvalidAmountError("amount must be a Money")
        if amount.amount <= 0:
            raise InvalidAmountError("amount must be greater than zero")

    @abstractmethod
    def _apply(self, amount: Money) -> None:
        """Perform the wallet operation. Must raise a MoneyError on rejection
        before mutating any balance."""
