from app.domain.money.exception import InvalidAmountError, MoneyError
from app.domain.money.money import Money
from app.domain.money.transaction import Transaction
from app.domain.money.transactionType import TransactionType
from app.domain.money.wallet import Wallet
from app.domain.repositories.transaction_repository import TransactionRepository


class LockFunds:
    """Move funds from a wallet's available balance into its locked balance.

    Same template as DepositMoney/WithdrawMoney. Locking records a LOCK_FUNDS
    transaction and lets the Wallet decide whether the move is allowed.
    """

    def __init__(self, wallet: Wallet, transaction_repository: TransactionRepository):
        self.wallet = wallet
        self.transaction_repository = transaction_repository

    def execute(self, amount: Money, internal_reference: str) -> Transaction:
        # 1. Command validation - a Transaction cannot be built for a
        #    non-positive amount, and the Wallet rejects it the same way.
        if not isinstance(amount, Money):
            raise InvalidAmountError("lock amount must be a Money")
        if amount.amount <= 0:
            raise InvalidAmountError("lock amount must be greater than zero")

        # 2. Idempotency: replaying the same key must not lock the funds twice.
        existing = self.transaction_repository.get_by_internal_reference(
            internal_reference
        )
        if existing is not None:
            return existing

        # 3. Record intent BEFORE touching the wallet.
        transaction = Transaction(
            wallet_id=self.wallet.wallet_id,
            type=TransactionType.LOCK_FUNDS,
            amount=amount,
            internal_reference=internal_reference,
        )
        self.transaction_repository.save(transaction)

        try:
            # 4. The wallet decides. It moves available -> locked atomically,
            #    raising before mutating on any rejection.
            self.wallet.lock_funds(amount)
        except MoneyError:
            # 4a. Rejected - record FAILED for the audit trail, then surface
            #     the original error.
            transaction.mark_failed()
            self.transaction_repository.save(transaction)
            raise

        # 4b. Funds locked - the transaction is complete.
        transaction.mark_successful()
        self.transaction_repository.save(transaction)

        return transaction
