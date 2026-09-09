from decimal import Decimal
from uuid import UUID, uuid4

from app.application.deposit.deposit_money import DepositMoney
from app.application.lock.lock_funds import LockFunds
from app.application.release.release_funds import ReleaseFunds
from app.application.unit_of_work import UnitOfWorkFactory
from app.application.wallet_operation import WalletOperation
from app.application.withdraw.withdraw_money import WithdrawMoney
from app.domain.money.currency import Currency
from app.domain.money.exception import MoneyError
from app.domain.money.money import Money
from app.domain.money.transaction import Transaction
from app.domain.money.wallet import Wallet
from app.domain.money.walletStatus import WalletStatus


class WalletService:
    """Makes the wallet operations callable by wallet_id.

    Each call opens a fresh Unit of Work - one database transaction. The ledger
    row the operation writes and the wallet's new balance either commit
    together or are discarded together.

    A wallet rejection is itself a committed outcome: the operation records a
    FAILED audit row, so the service commits that row and then re-raises the
    original domain error. Only unexpected failures (database errors, bugs)
    roll everything back.
    """

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory):
        self._unit_of_work_factory = unit_of_work_factory

    def deposit(self, wallet_id, amount: Money, internal_reference: str) -> Transaction:
        return self._run(DepositMoney, wallet_id, amount, internal_reference)

    def withdraw(self, wallet_id, amount: Money, internal_reference: str) -> Transaction:
        return self._run(WithdrawMoney, wallet_id, amount, internal_reference)

    def lock(self, wallet_id, amount: Money, internal_reference: str) -> Transaction:
        return self._run(LockFunds, wallet_id, amount, internal_reference)

    def release(self, wallet_id, amount: Money, internal_reference: str) -> Transaction:
        return self._run(ReleaseFunds, wallet_id, amount, internal_reference)

    def open_wallet(self, user_id: UUID, currency: Currency) -> Wallet:
        """Open a new empty wallet for a user, in the given currency."""
        uow = self._unit_of_work_factory.start()
        try:
            wallet = Wallet(
                wallet_id=uuid4(),
                user_id=user_id,
                status=WalletStatus.ACTIVE,
                _available_balance=Money(Decimal("0"), currency),
                _locked_balance=Money(Decimal("0"), currency),
                currency=currency,
            )
            uow.wallets.save(wallet)
            uow.commit()
            return wallet
        except BaseException:
            uow.rollback()
            raise

    def get_wallet(self, wallet_id: UUID) -> Wallet:
        """Read a wallet's current state.

        Pure read: nothing is committed. Absence raises WalletNotFoundError.
        """
        uow = self._unit_of_work_factory.start()
        try:
            return uow.wallets.get_by_id(wallet_id)
        finally:
            uow.rollback()

    def _run(
        self,
        operation_cls,
        wallet_id: UUID,
        amount: Money,
        internal_reference: str,
    ) -> Transaction:
        uow = self._unit_of_work_factory.start()
        try:
            wallet = uow.wallets.get_by_id(wallet_id)
            operation: WalletOperation = operation_cls(
                wallet, uow.transactions
            )
            transaction = operation.execute(amount, internal_reference)
            # The wallet changed (or would have) - persist the aggregate's new
            # state in the same transaction as the ledger row above.
            uow.wallets.save(wallet)
        except MoneyError:
            # The wallet rejected the attempt. The operation already recorded a
            # FAILED row - commit it so the audit trail survives, then tell the
            # caller what happened.
            uow.commit()
            raise
        except BaseException:
            # Real failure: nothing may be left half-written.
            uow.rollback()
            raise
        else:
            uow.commit()
            return transaction
