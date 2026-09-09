from app.domain.money.exception import InvalidAmountError, MoneyError
from app.domain.money.money import Money
from app.domain.money.transaction import Transaction
from app.domain.money.transactionType import TransactionType
from app.domain.money.wallet import Wallet
from app.domain.repositories.transaction_repository import TransactionRepository


class ReleaseFunds:
    """Move funds from a wallet's locked balance back into its available balance.

    Same template as the other use cases. Releasing records a
    SCHEDULED_RELEASE transaction and lets the Wallet decide whether the move
    is allowed.
    """

    def __init__(self, wallet: Wallet, transaction_repository: TransactionRepository):
        self.wallet = wallet
        self.transaction_repository = transaction_repository

    def execute(self, amount: Money, internal_reference: str) -> Transaction:
        # 1. Command validation.
        if not isinstance(amount, Money):
            raise InvalidAmountError("release amount must be a Money")
        if amount.amount <= 0:
            raise InvalidAmountError("release amount must be greater than zero")

        # 2. Idempotency: replaying the same key must not release twice.
        existing = self.transaction_repository.get_by_internal_reference(
            internal_reference
        )
        if existing is not None:
            return existing

        # 3. Record intent BEFORE touching the wallet.
        transaction = Transaction(
            wallet_id=self.wallet.wallet_id,
            type=TransactionType.SCHEDULED_RELEASE,
            amount=amount,
            internal_reference=internal_reference,
        )
        self.transaction_repository.save(transaction)

        try:
            # 4. The wallet decides. It moves locked -> available atomically,
            #    raising before mutating on any rejection.
            self.wallet.release_funds(amount)
        except MoneyError:
            # 4a. Rejected - record FAILED for the audit trail, then surface
            #     the original error.
            transaction.mark_failed()
            self.transaction_repository.save(transaction)
            raise

        # 4b. Funds released - the transaction is complete.
        transaction.mark_successful()
        self.transaction_repository.save(transaction)

        return transaction
