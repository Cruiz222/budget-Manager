from app.application.wallet_operation import WalletOperation
from app.domain.money.transactionType import TransactionType


class ReleaseFunds(WalletOperation):
    """Move funds from locked balance back into available balance."""

    transaction_type = TransactionType.SCHEDULED_RELEASE

    def _apply(self, amount):
        self.wallet.release_funds(amount)
