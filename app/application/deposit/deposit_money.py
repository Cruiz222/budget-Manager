import uuid

from app.domain.money.transaction import Transaction
from app.domain.money.transactionType import TransactionType


class DepositMoney:
    def __init__(self, wallet):
        self.wallet = wallet

    def execute(self, wallet_id, amount):
        self.wallet.apply_deposit(amount)

        transaction = Transaction(
        wallet_id=wallet_id,
        type=TransactionType.DEPOSIT,
        amount=amount,
        internal_reference=str(uuid.uuid4()),
)

        transaction.mark_successful()

        return transaction