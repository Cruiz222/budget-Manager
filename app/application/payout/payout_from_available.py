from app.application.wallet_operation import WalletOperation
from app.domain.money.transactionType import TransactionType


class PayoutFromAvailable(WalletOperation):
    """Send money out of the wallet from its available balance, to an external account.

    The counterpart of ``PayoutFromLocked`` for money the user never reserved.
    This is the half of the product rule that does not involve locking: "without
    locking an amount, a user can still schedule payments".

    Note what this class does *not* add to the domain. It records PAYOUT and it
    hands the Transaction a destination - but the money movement is plain
    ``wallet.withdraw()``, because that method already means exactly this:
    spend the available balance. There is no ``Wallet.payout_from_available``,
    and there should not be. A destination is a *ledger* concept, not a wallet
    one - the wallet moves value between balances and out; it has no idea
    whether the far end has a name, and giving it one would be coupling the
    money domain to a concern it does not have.

    So the missing piece was never in the domain. ``withdraw`` could always move
    the money; what it could not do was say *where the money went*, because
    ``Transaction`` forbids a destination on a WITHDRAWAL. That was a gap in the
    ledger's vocabulary, and widening PAYOUT closed it.

    ``wallet.withdraw`` appears here rather than being reimplemented, which
    matters for the one error split the two share: ``WithdrawMoney`` overrides
    ``_validate_amount`` so that a zero amount and a negative amount produce
    different errors, and that override is what makes withdraw's
    ZeroAmountWithdrawalError and NegativeAmountWithdrawalError unreachable from
    this path. Both operations reject those amounts with InvalidAmountError in
    the base class, before ``_apply`` is ever called - so routing through
    ``withdraw`` cannot surface withdrawal-specific vocabulary for a payout.
    """

    transaction_type = TransactionType.PAYOUT

    def _apply(self, amount):
        self.wallet.withdraw(amount)
