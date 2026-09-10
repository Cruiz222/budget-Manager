from app.application.wallet_operation import WalletOperation
from app.domain.money.transactionType import TransactionType


class PayoutFromLocked(WalletOperation):
    """Send money out of the wallet from its locked balance, to an external account.

    Still tiny after gaining a destination. The destination is not an input this
    class reasons about - it is data the Transaction needs, passed through
    ``execute()``. The rule that a payout must have one is enforced by the
    Transaction itself, so the use case does not restate it.

    The destination is *snapshotted* onto the transaction: editing or deleting
    the saved account later must not rewrite where money already went.
    """

    transaction_type = TransactionType.PAYOUT

    def _apply(self, amount):
        self.wallet.payout_from_locked(amount)
