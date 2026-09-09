from app.domain.money.exception import (
    InvalidAmountError,
    MoneyError,
    NegativeAmountWithdrawalError,
    ZeroAmountWithdrawalError,
)
from app.domain.money.money import Money
from app.domain.money.transaction import Transaction
from app.domain.money.transactionType import TransactionType
from app.domain.money.wallet import Wallet
from app.domain.repositories.transaction_repository import TransactionRepository


class WithdrawMoney:
    """Withdraw money from a wallet, keeping wallet and transaction in step.

    Structural mirror of DepositMoney: command validation, idempotency guard,
    PENDING-first persistence, then the wallet decides. The withdrawal errors
    (zero / negative) are the same ones the Wallet itself raises, so callers
    see a consistent vocabulary.
    """

    def __init__(self, wallet: Wallet, transaction_repository: TransactionRepository):
        self.wallet = wallet
        self.transaction_repository = transaction_repository

    def execute(self, amount: Money, internal_reference: str) -> Transaction:
        # 1. Command validation happens before any record exists. A Transaction
        #    cannot even be constructed with a non-positive amount, so zero and
        #    negative withdrawals are rejected here using the Wallet's own
        #    error granularity.
        if not isinstance(amount, Money):
            raise InvalidAmountError("withdrawal amount must be a Money")
        if amount.amount == 0:
            raise ZeroAmountWithdrawalError(
                "withdrawal amount must be greater than zero"
            )
        if amount.amount < 0:
            raise NegativeAmountWithdrawalError(
                "withdrawal amount cannot be negative"
            )

        # 2. Idempotency: replaying the same internal_reference must not
        #    withdraw the money twice.
        existing = self.transaction_repository.get_by_internal_reference(
            internal_reference
        )
        if existing is not None:
            return existing

        # 3. Record intent BEFORE touching money.
        transaction = Transaction(
            wallet_id=self.wallet.wallet_id,
            type=TransactionType.WITHDRAWAL,
            amount=amount,
            internal_reference=internal_reference,
        )
        self.transaction_repository.save(transaction)

        try:
            # 4. The wallet decides: closed / frozen / wrong currency /
            #    insufficient funds all raise here, before any mutation.
            self.wallet.withdraw(amount)
        except MoneyError:
            # 4a. The wallet rejected the withdrawal - record the attempt as
            #     FAILED for the audit trail, then surface the original error.
            transaction.mark_failed()
            self.transaction_repository.save(transaction)
            raise

        # 4b. The money moved - the transaction is complete.
        transaction.mark_successful()
        self.transaction_repository.save(transaction)

        return transaction
