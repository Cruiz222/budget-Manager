from decimal import Decimal
from uuid import UUID, uuid4

from app.application.deposit.deposit_money import DepositMoney
from app.application.lock.lock_funds import LockFunds
from app.application.notifications import compose
from app.application.payout.payout_from_available import PayoutFromAvailable
from app.application.payout.payout_from_locked import PayoutFromLocked
from app.application.release.release_funds import ReleaseFunds
from app.application.unit_of_work import UnitOfWork, UnitOfWorkFactory
from app.application.wallet_operation import WalletOperation
from app.application.withdraw.withdraw_money import WithdrawMoney
from app.domain.money.currency import Currency
from app.domain.money.destination import Destination
from app.domain.money.exception import MoneyError
from app.domain.money.money import Money
from app.domain.money.transaction import Transaction
from app.domain.money.wallet import Wallet
from app.domain.money.walletStatus import WalletStatus
from app.domain.notifications.notificationKind import NotificationKind


class WalletService:
    """Makes the wallet operations callable by wallet_id.

    Each call opens a fresh Unit of Work - one database transaction. The ledger
    row the operation writes and the wallet's new balance either commit
    together or are discarded together.

    A wallet rejection is itself a committed outcome: the operation records a
    FAILED audit row, so the service commits that row and then re-raises the
    original domain error. Only unexpected failures (database errors, bugs)
    roll everything back.

    **A successful operation also queues its receipt, in that same
    transaction.** The reason is the one the plan run makes (see
    ``ExecutePlanRun``), applied to a command typed at a terminal rather than to
    a schedule: the ledger row is the only record that money moved, and a row
    that committed without its receipt queued would be money the user is never
    told about with nothing left to notice the omission. So the receipt is
    written before ``commit()`` and rolls back with the money if it cannot be
    composed.

    Unlike a plan run there is no automatic retry behind that rollback - a
    command that fails is a command the user runs again - but the outcome is
    still the safe one: nothing moved, and nothing claims it did.
    """

    #: Which operations say something to the user, and what they say.
    #:
    #: A dict rather than four ``if`` statements in ``_run`` so that the answer
    #: is readable in one place, and so that changing it is a line rather than an
    #: edit to the flow every operation shares. The question "does a release send
    #: an email?" is answered by looking here, not by tracing a branch.
    #:
    #: Locking and releasing are **absent on purpose**. They move money between
    #: the wallet's own two balances, which changes nothing the owner holds, and
    #: they are performed by the person reading the mail at a terminal that has
    #: already printed the result. A receipt for a command someone just typed is
    #: not information. See ``NotificationKind`` for the full argument.
    ANNOUNCED = {
        DepositMoney: NotificationKind.WALLET_DEPOSIT,
        WithdrawMoney: NotificationKind.WALLET_WITHDRAWAL,
        PayoutFromLocked: NotificationKind.WALLET_PAYOUT,
        PayoutFromAvailable: NotificationKind.WALLET_PAYOUT,
    }

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        recipient: str | None = None,
    ):
        self._unit_of_work_factory = unit_of_work_factory
        # ``None`` means this installation has no notification address, which is
        # the ordinary state of a fresh install rather than an error: every
        # operation behaves exactly as it would otherwise and simply says nothing
        # about it. Note what this address is *not* - it is never a condition on
        # moving money, only on where the words about it go.
        self._recipient = recipient

    def deposit(self, wallet_id, amount: Money, internal_reference: str) -> Transaction:
        return self._run(DepositMoney, wallet_id, amount, internal_reference)

    def withdraw(self, wallet_id, amount: Money, internal_reference: str) -> Transaction:
        return self._run(WithdrawMoney, wallet_id, amount, internal_reference)

    def lock(self, wallet_id, amount: Money, internal_reference: str) -> Transaction:
        return self._run(LockFunds, wallet_id, amount, internal_reference)

    def release(self, wallet_id, amount: Money, internal_reference: str) -> Transaction:
        return self._run(ReleaseFunds, wallet_id, amount, internal_reference)

    def payout_from_locked(
        self,
        wallet_id,
        amount: Money,
        internal_reference: str,
        destination: Destination,
    ) -> Transaction:
        """Spend the locked balance, sending value out to an external account."""
        return self._run(
            PayoutFromLocked,
            wallet_id,
            amount,
            internal_reference,
            destination=destination,
        )

    def payout_from_available(
        self,
        wallet_id,
        amount: Money,
        internal_reference: str,
        destination: Destination,
    ) -> Transaction:
        """Spend the available balance, sending value out to an external account.

        The unlocked counterpart of payout_from_locked, and the operation a
        scheduled payout performs when the user never reserved the money. Both
        record PAYOUT; the difference is which balance funds it.
        """
        return self._run(
            PayoutFromAvailable,
            wallet_id,
            amount,
            internal_reference,
            destination=destination,
        )

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

    def transactions_for_wallet(self, wallet_id: UUID) -> list[Transaction]:
        """Read a wallet's full transaction ledger, oldest first.

        Pure read like get_wallet. Absence of the wallet itself raises
        WalletNotFoundError (an unknown wallet is different from an empty one).
        """
        uow = self._unit_of_work_factory.start()
        try:
            # Validate the wallet exists so "unknown wallet" and "no activity
            # yet" are distinguishable to the caller.
            uow.wallets.get_by_id(wallet_id)
            return uow.transactions.get_by_wallet_id(wallet_id)
        finally:
            uow.rollback()

    def freeze_wallet(self, wallet_id: UUID) -> Wallet:
        """Freeze a wallet, stopping withdrawals until it is unfrozen."""
        return self._change_status(wallet_id, Wallet.freeze)

    def unfreeze_wallet(self, wallet_id: UUID) -> Wallet:
        """Return a frozen wallet to its active state."""
        return self._change_status(wallet_id, Wallet.unfreeze)

    def _change_status(self, wallet_id: UUID, transition) -> Wallet:
        """Load a wallet, apply a status transition, persist it atomically.

        Unlike a money operation there is no ledger row - the wallet either
        moves to the new state or (on rejection) stays as it was.
        """
        uow = self._unit_of_work_factory.start()
        try:
            wallet = uow.wallets.get_by_id(wallet_id)
            transition(wallet)
            uow.wallets.save(wallet)
            uow.commit()
            return wallet
        except BaseException:
            uow.rollback()
            raise

    def _run(
        self,
        operation_cls,
        wallet_id: UUID,
        amount: Money,
        internal_reference: str,
        destination: Destination | None = None,
    ) -> Transaction:
        uow = self._unit_of_work_factory.start()
        try:
            wallet = uow.wallets.get_by_id(wallet_id)
            operation: WalletOperation = operation_cls(
                wallet, uow.transactions
            )
            transaction = operation.execute(amount, internal_reference, destination)
            # The wallet changed (or would have) - persist the aggregate's new
            # state in the same transaction as the ledger row above.
            uow.wallets.save(wallet)
            # And the receipt, before the commit that makes the money real - see
            # the class docstring. Note this is only reached when the operation
            # *returned*: a rejection went to the ``except MoneyError`` below and
            # is already reported to the user as an error line, so it needs no
            # email.
            self._announce(uow, operation_cls, wallet, transaction)
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

    def _announce(
        self,
        uow: UnitOfWork,
        operation_cls,
        wallet: Wallet,
        transaction: Transaction,
    ) -> None:
        """Queue the receipt for an operation, when that operation is announced.

        Silent for the two operations that say nothing - locking and releasing -
        and the silence is a lookup rather than a condition. Keeping the list in
        ``ANNOUNCED`` means "does this operation notify?" has one answer in one
        place, instead of being spread through the flow every operation shares.

        ``enqueue`` answers whether this call was the one that claimed the event,
        and the answer is deliberately discarded. Nothing here branches on it:
        a row that was new and a row that was already there are the same outcome
        from this caller's point of view - the event will be announced once, and
        not twice. Reading the flag and doing nothing with it would only invite
        the next reader to wonder which branch was meant.
        """
        kind = self.ANNOUNCED.get(operation_cls)
        if kind is None:
            return

        notification = compose.wallet_movement(
            kind, wallet, transaction, self._recipient
        )
        if notification is not None:
            uow.notifications.enqueue(notification)
