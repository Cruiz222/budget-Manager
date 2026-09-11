from app.application.wallet_operation import WalletOperation
from app.domain.money.transactionType import TransactionType


class DepositIntoFund(WalletOperation):
    """Bring money in from outside, straight into a named pot.

    Recorded as a plain ``DEPOSIT``, not a deposit-plus-lock. The money never
    touches the available balance, so there is no second movement to record, and
    the single row is the honest description of what happened.

    This is the operation the original request was about - "it should still be
    able to accept additions or further deposits" - and it is the only fund
    operation that says anything to the owner. See ``WalletService.ANNOUNCED``.

    **It is built from a name, not an id.** The caller has the name - it came off
    a command line - and only the wallet can turn one into the other, so the
    wallet handed to ``__init__`` does the resolving. Resolving here rather than
    in the caller keeps the reason local: this class is what writes ``fund_id``
    onto the ledger, so this class is what must have one. The base class is
    untouched - it still takes the id it records, and the subclass supplies it.

    Resolving in ``__init__`` does mean an unknown pot is reported before
    ``execute`` validates the amount, so ``fund deposit W Vacation -5`` says
    "no fund named 'Vacation'" rather than "amount must be greater than zero"
    when both are wrong. That is the right order: the amount is well-formed on
    its own terms, while the pot depends on wallet state that has to be consulted
    either way, and doing it once up front is what leaves ``self.fund_id``
    settled for every later step.

    **``as_of`` is required, and it is the moment money arrives.** This is the
    operation that stamps a pot's ``first_funded_at`` - the anchor the
    business-pot exemption is judged against - so the moment cannot be read from
    the clock inside the wallet. A deposit that stamped "now" would make the
    anti-temptation rule untestable, and worse, would make it depend on which
    layer happened to read the clock. See ``Fund.authorises_early_payout``.
    """

    transaction_type = TransactionType.DEPOSIT

    def __init__(self, wallet, transaction_repository, fund_name, as_of):
        fund = wallet.fund_by_name(fund_name)
        super().__init__(wallet, transaction_repository, fund_id=fund.fund_id)
        self._as_of = as_of

    def _apply(self, amount):
        self.wallet.deposit_into_fund(self.fund_id, amount, self._as_of)
