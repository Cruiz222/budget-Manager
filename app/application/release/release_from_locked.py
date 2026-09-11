from datetime import datetime

from app.application.wallet_operation import WalletOperation
from app.domain.money.transactionType import TransactionType


class ReleaseFromLocked(WalletOperation):
    """Move money out of the locked pots, back into the available balance.

    The operation a plan's RELEASE instruction reaches, and the pooled
    counterpart of ``ReleaseFromFund``. The difference between the two is
    precisely the pot: a person at a terminal types ``fund release Vacation
    5000`` and names one, and a plan now names one too - so when ``fund_id`` is
    given this delegates to ``release_from_fund`` and the pot answers for its own
    maturity date.

    ``fund_id = None`` is kept for a plan saved before pots could be named: with
    no pot to name, this hands the choice to the wallet, which spends the matured
    pots oldest first (``Wallet._draw_from_matured``). The ledger row it writes
    then carries no fund.

    **Unlike the payout, this class does not disappear** - and the reason is
    worth stating, because the two are otherwise the same shape. A payout naming
    a pot and a payout naming none are genuinely different acts, one scheduled
    and one not. A *release* naming a pot and one naming none are the same act
    with different bookkeeping: releasing is never exempt from a maturity date,
    for either kind of pot, so the pooled draw and the pot-scoped release reach
    the identical refusal. Only the ledger row differs, and only for legacy
    plans.

    Records UNLOCK_FUNDS, not something new: from the ledger's point of view -
    and from the owner's - this is the same event as a pot-scoped release. Which
    pot it came from is what ``transactions.fund_id`` now carries when there is
    one.

    Allowed on a frozen wallet, since the money does not leave - see
    ``Wallet.release_from_locked``.
    """

    transaction_type = TransactionType.UNLOCK_FUNDS

    def __init__(
        self,
        wallet,
        transaction_repository,
        as_of: datetime,
        fund_id=None,
    ):
        super().__init__(wallet, transaction_repository, fund_id=fund_id)
        self._as_of = as_of

    def _apply(self, amount):
        if self.fund_id is None:
            self.wallet.release_from_locked(amount, self._as_of)
            return
        self.wallet.release_from_fund(self.fund_id, amount, self._as_of)
