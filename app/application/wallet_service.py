from uuid import UUID

from app.application.deposit.deposit_money import DepositMoney
from app.application.lock.lock_funds import LockFunds
from app.application.release.release_funds import ReleaseFunds
from app.application.withdraw.withdraw_money import WithdrawMoney
from app.application.wallet_operation import WalletOperation
from app.domain.money.money import Money
from app.domain.money.transaction import Transaction
from app.domain.repositories.transaction_repository import TransactionRepository
from app.domain.repositories.wallet_repository import WalletRepository


class WalletService:
    """Makes the wallet operations callable by wallet_id.

    Callers no longer assemble a use case by hand: give this service a
    wallet_id, an amount and an idempotency key, and it loads the wallet from
    its repository, runs the matching operation, and persists the wallet back.
    """

    def __init__(
        self,
        wallet_repository: WalletRepository,
        transaction_repository: TransactionRepository,
    ):
        self.wallet_repository = wallet_repository
        self.transaction_repository = transaction_repository

    def deposit(self, wallet_id, amount: Money, internal_reference: str) -> Transaction:
        return self._run(DepositMoney, wallet_id, amount, internal_reference)

    def withdraw(self, wallet_id, amount: Money, internal_reference: str) -> Transaction:
        return self._run(WithdrawMoney, wallet_id, amount, internal_reference)

    def lock(self, wallet_id, amount: Money, internal_reference: str) -> Transaction:
        return self._run(LockFunds, wallet_id, amount, internal_reference)

    def release(self, wallet_id, amount: Money, internal_reference: str) -> Transaction:
        return self._run(ReleaseFunds, wallet_id, amount, internal_reference)

    def _run(
        self,
        operation_cls,
        wallet_id: UUID,
        amount: Money,
        internal_reference: str,
    ) -> Transaction:
        wallet = self.wallet_repository.get_by_id(wallet_id)
        operation: WalletOperation = operation_cls(
            wallet, self.transaction_repository
        )
        transaction = operation.execute(amount, internal_reference)
        # The wallet changed (or would have) - persist the aggregate's new state.
        self.wallet_repository.save(wallet)
        return transaction
