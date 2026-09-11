from app.application.wallet_operation import WalletOperation
from app.domain.money.transactionType import TransactionType


class ReleaseFromFund(WalletOperation):
    """Move money out of a named pot, back into the available balance.

    An internal move, so it remains allowed on a frozen wallet. What it is *not*
    allowed is early: ``Fund.release`` refuses until the pot has come due, and
    that refusal travels back through ``WalletOperation.execute`` as a
    ``MoneyError`` - which means it is recorded as a FAILED ledger row before it
    is re-raised, exactly like any other rejected operation. An attempt to
    release a sealed pot leaves a trace saying so.

    Note the two different pre-maturity refusals this can produce, because only
    the second leaves a ledger row:

      - a pot that does not exist is refused in ``__init__``, before the base
        class records anything, exactly as an unknown wallet id is refused in the
        service. Nothing happened, and nothing claims it did.
      - a pot that exists but has not come due is refused *inside* ``_apply``,
        which the base records as FAILED. That attempt is worth remembering, and
        the ledger is where the wallet already keeps every other refusal.

    ``as_of`` is carried on the operation rather than read from the clock for the
    reason the plan run carries it: the whole use case should have one idea of
    what "now" is, and a test should be able to say without freezing time.
    """

    transaction_type = TransactionType.UNLOCK_FUNDS

    def __init__(self, wallet, transaction_repository, fund_name, as_of):
        fund = wallet.fund_by_name(fund_name)
        super().__init__(wallet, transaction_repository, fund_id=fund.fund_id)
        self._as_of = as_of

    def _apply(self, amount):
        self.wallet.release_from_fund(self.fund_id, amount, self._as_of)
