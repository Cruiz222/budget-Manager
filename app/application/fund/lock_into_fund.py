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
    """

    transaction_type = TransactionType.LOCK_FUNDS

    def __init__(self, wallet, transaction_repository, fund_name):
        fund = wallet.fund_by_name(fund_name)
        super().__init__(wallet, transaction_repository, fund_id=fund.fund_id)

    def _apply(self, amount):
        self.wallet.lock_into_fund(self.fund_id, amount)
