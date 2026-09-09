from app.application.wallet_operation import WalletOperation
from app.domain.money.exception import (
    InvalidAmountError,
    NegativeAmountWithdrawalError,
    ZeroAmountWithdrawalError,
)
from app.domain.money.money import Money
from app.domain.money.transactionType import TransactionType


class WithdrawMoney(WalletOperation):
    """Withdraw money from the wallet's available balance."""

    transaction_type = TransactionType.WITHDRAWAL

    def _validate_amount(self, amount):
        # The Wallet distinguishes a zero withdrawal from a negative one, so
        # this operation keeps that finer error vocabulary.
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

    def _apply(self, amount):
        self.wallet.withdraw(amount)
