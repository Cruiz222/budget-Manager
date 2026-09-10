from app.application.wallet_operation import WalletOperation
from app.domain.money.transactionType import TransactionType


class ReleaseFunds(WalletOperation):
    """Move funds from locked balance back into available balance.

    An internal move: the wallet still holds every naira, so this stays allowed
    on a frozen wallet. Money actually leaving is a payout instead.
    """

    transaction_type = TransactionType.UNLOCK_FUNDS

    def _apply(self, amount):
        self.wallet.release_funds(amount)
