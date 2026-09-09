from app.application.wallet_operation import WalletOperation
from app.domain.money.transactionType import TransactionType


class LockFunds(WalletOperation):
    """Move funds from available balance into locked balance."""

    transaction_type = TransactionType.LOCK_FUNDS

    def _apply(self, amount):
        self.wallet.lock_funds(amount)
