from app.application.wallet_operation import WalletOperation
from app.domain.money.exception import (
    InvalidAmountError,
    NegativeAmountWithdrawalError,
    ZeroAmountWithdrawalError,
)
from app.domain.money.money import Money
from app.domain.money.transactionType import TransactionType


class WithdrawMoney(WalletOperation):
    """Move money out of the wallet's available balance, towards the owner.

    **It does not settle here**, which is the surprising part of a class this
    short. The wallet is debited - the money leaves the owner's reach, and a
    second withdrawal of the same amount is refused against the reduced balance -
    but the transaction is left PENDING, because the far end of a withdrawal is a
    bank account this code has never spoken to. What has happened is that the
    owner's money is held; what has not happened is a transfer.

    So this is the type ``Transaction`` refuses to give a ``destination``: a
    withdrawal says money left, and a payout says money left *and here is where it
    went*. Neither can say it arrived. See ``settles_immediately``.
    """

    transaction_type = TransactionType.WITHDRAWAL
    settles_immediately = False

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
