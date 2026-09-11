from datetime import datetime

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

    **Which pot pays is an argument now, not the wallet's guess.** ``fund_id`` is
    a base-class pass-through that the wallet uses to pick the pot, and it lands
    on the ledger row as well - this is the class that finally *writes*
    ``transactions.fund_id`` on a payout, which is the half the previous phase
    left empty. Passing ``None`` still works and still means the pooled draw, but
    the only caller left that does so is a plan saved before pots could be named;
    see ``Wallet.payout_from_locked``.

    ``committed_at`` is the second new argument, and it is the one that decides a
    business pot's early payment. It is the moment the plan that ordered this
    payment was created - handed in by the use case, because the pot cannot see a
    plan. A hand-typed payout passes ``None``, which is what makes it always a
    different thing from a scheduled one: see ``Fund.pay``.

    Both travel together, and that is not a coincidence. The exemption is *for*
    scheduled payments, and a scheduled payment is exactly one that has a plan
    behind it - so ``committed_at`` is ``None`` precisely when there is no
    commitment, and a hand-typed ``payout --fund`` cannot borrow the exemption by
    naming a pot.

    **The pot arrives as an id or as a name, and the two are not redundant.**
    They are the two handles the wallet itself already offers (``fund_by_id``
    and ``fund_by_name``), because two callers hold two different things: a plan
    holds a ``fund_id``, which is what it persisted and what survives a rename,
    while a person holds a name they typed. The constructor resolves whichever it
    was given to an id before anything else happens, so the rest of the class -
    and ``WalletOperation.execute``, which writes ``fund_id`` onto the ledger row
    - sees only one shape.

    The destination is not an input this class reasons about either; it is data
    the Transaction needs, passed through ``execute()``. The rule that a payout
    must have one is enforced by the Transaction itself, so the use case does not
    restate it. The destination is *snapshotted* onto the transaction: editing or
    deleting the saved account later must not rewrite where money already went.
    """

    transaction_type = TransactionType.PAYOUT

    def __init__(
        self,
        wallet,
        transaction_repository,
        as_of: datetime,
        fund_id=None,
        fund_name: str | None = None,
        committed_at: datetime | None = None,
    ):
        if fund_id is None and fund_name is not None:
            fund_id = wallet.fund_by_name(fund_name).fund_id
        super().__init__(wallet, transaction_repository, fund_id=fund_id)
        self._as_of = as_of
        self._committed_at = committed_at

    def _apply(self, amount):
        if self.fund_id is None:
            self.wallet.payout_from_locked(amount, self._as_of)
            return
        self.wallet.payout_from_fund(
            self.fund_id, amount, self._as_of, self._committed_at
        )
