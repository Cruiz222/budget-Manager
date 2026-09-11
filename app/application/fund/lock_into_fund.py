from app.application.wallet_operation import WalletOperation
from app.domain.money.transactionType import TransactionType


class LockIntoFund(WalletOperation):
    """Move money from the available balance into a named pot.

    The pot-scoped successor to the old ``LockFunds``, which moved an amount
    into the locked balance without saying which pot it was for. An internal
    move: the wallet still holds every naira, so it stays allowed on a frozen
    wallet, and it tells the owner nothing they did not just type.

    Built from a pot's name, resolved against the wallet - see
    ``DepositIntoFund`` for why that resolution lives in the constructor.

    ``as_of`` is required for the same reason it is on ``DepositIntoFund``, and
    it is worth saying why a *lock* counts: the money may not come from outside,
    but it does arrive *in the pot*, and the pot's ``first_funded_at`` is about
    money arriving in it rather than about where the money came from. A pot
    funded by a lock is no more entitled to pay early than one funded by a
    deposit - see ``Wallet.lock_into_fund``.
    """

    transaction_type = TransactionType.LOCK_FUNDS

    def __init__(self, wallet, transaction_repository, fund_name, as_of):
        fund = wallet.fund_by_name(fund_name)
        super().__init__(wallet, transaction_repository, fund_id=fund.fund_id)
        self._as_of = as_of

    def _apply(self, amount):
        self.wallet.lock_into_fund(self.fund_id, amount, self._as_of)
