from app.application.wallet_operation import WalletOperation
from app.domain.money.transactionType import TransactionType


class ReleaseFromLocked(WalletOperation):
    """Move money out of the locked pots, back into the available balance.

    The operation a plan's RELEASE instruction reaches, and the pooled
    counterpart of ``ReleaseFromFund``. The difference between the two is
    precisely the pot: a person at a terminal types ``fund release Vacation
    5000`` and names one, while a plan in this phase names none - so this one
    hands the choice to the wallet, which spends the matured pots oldest first
    (``Wallet._draw_from_matured``). The ledger row it writes therefore carries
    no fund, exactly as the plan's payouts do.

    **This class is expected to disappear.** Once a plan names the pot it draws
    from, there is no caller left that cannot say which pot it means, and
    ``ReleaseFromFund`` covers every remaining case. It exists because a plan
    cannot name a pot *yet*, which is a schedule of work rather than a design.

    Records UNLOCK_FUNDS, not something new: from the ledger's point of view -
    and from the owner's - this is the same event as a pot-scoped release. Which
    pot it came from is not something this phase is able to say, and inventing a
    second type would put "was it a pot or a pool?" onto every row rather than
    leaving it for the phase that can answer it.

    Allowed on a frozen wallet, since the money does not leave - see
    ``Wallet.release_from_locked``.
    """

    transaction_type = TransactionType.UNLOCK_FUNDS

    def __init__(self, wallet, transaction_repository, as_of):
        super().__init__(wallet, transaction_repository)
        self._as_of = as_of

    def _apply(self, amount):
        self.wallet.release_from_locked(amount, self._as_of)
