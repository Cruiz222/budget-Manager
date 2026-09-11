from app.application.wallet_operation import WalletOperation
from app.domain.money.transactionType import TransactionType


class PayoutFromLocked(WalletOperation):
    """Send money out of the wallet from its locked pots, to an external account.

    Still tiny after gaining an ``as_of``. That moment is not state the operation
    reasons about, it is the clock the *wallet* needs: a pot may only be spent
    once it has come due, and "come due" is a comparison against now. Carrying it
    here rather than reading ``datetime.now()`` inside the wallet keeps a whole
    use case on one idea of what now is - and lets a test judge maturity without
    freezing the clock.

    Which pot pays is the wallet's decision, not this class's - and in this phase
    it is a placeholder pool rule. See ``Wallet.payout_from_locked``.

    The destination is not an input this class reasons about either; it is data
    the Transaction needs, passed through ``execute()``. The rule that a payout
    must have one is enforced by the Transaction itself, so the use case does not
    restate it. The destination is *snapshotted* onto the transaction: editing or
    deleting the saved account later must not rewrite where money already went.
    """

    transaction_type = TransactionType.PAYOUT

    def __init__(self, wallet, transaction_repository, as_of):
        super().__init__(wallet, transaction_repository)
        self._as_of = as_of

    def _apply(self, amount):
        self.wallet.payout_from_locked(amount, self._as_of)
