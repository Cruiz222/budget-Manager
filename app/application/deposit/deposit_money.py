from app.application.wallet_operation import WalletOperation
from app.domain.money.transactionType import TransactionType


class DepositMoney(WalletOperation):
    """Deposit money into the wallet's available balance."""

    transaction_type = TransactionType.DEPOSIT

    def _apply(self, amount):
        self.wallet.apply_deposit(amount)
