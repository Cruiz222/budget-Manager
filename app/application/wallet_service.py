from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from app.application.deposit.deposit_money import DepositMoney
from app.application.fund.deposit_into_fund import DepositIntoFund
from app.application.fund.lock_into_fund import LockIntoFund
from app.application.fund.release_from_fund import ReleaseFromFund
from app.application.notifications import compose
from app.application.payout.payout_from_available import PayoutFromAvailable
from app.application.payout.payout_from_locked import PayoutFromLocked
from app.application.unit_of_work import UnitOfWork, UnitOfWorkFactory
from app.application.wallet_operation import WalletOperation
from app.application.withdraw.withdraw_money import WithdrawMoney
from app.domain.money.currency import Currency
from app.domain.money.destination import Destination
from app.domain.money.exception import MoneyError
from app.domain.money.fund import Fund
from app.domain.money.fundKind import FundKind
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
    #: A dict rather than a row of ``if`` statements in ``_run`` so that the
    #: answer is readable in one place, and so that changing it is a line rather
    #: than an edit to the flow every operation shares. The question "does a
    #: release send an email?" is answered by looking here, not by tracing a
    #: branch.
    #:
    #: Everything that only *reshuffles* the wallet's own balances is absent on
    #: purpose: locking into a pot, releasing out of one, opening one, extending
    #: one. None of them changes what the owner holds, and all of them are
    #: performed by the person reading the mail, at a terminal that has already
    #: printed the result. A receipt for a command someone just typed is not
    #: information.
    #:
    #: ``DepositIntoFund`` *is* here, and the contrast is the point: money
    #: arriving from outside is a boundary event even when it lands in a pot
    #: rather than in the available balance. See ``NotificationKind``.
    ANNOUNCED = {
        DepositMoney: NotificationKind.WALLET_DEPOSIT,
        DepositIntoFund: NotificationKind.WALLET_DEPOSIT,
        WithdrawMoney: NotificationKind.WALLET_WITHDRAWAL,
        PayoutFromLocked: NotificationKind.WALLET_PAYOUT,
        PayoutFromAvailable: NotificationKind.WALLET_PAYOUT,
    }

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        *,
        actor: UUID,
        recipient: str | None = None,
    ):
        self._unit_of_work_factory = unit_of_work_factory
        # Who this service acts for, for every method it has. Required and
        # keyword-only, so each construction site has to answer the question
        # rather than inherit silence - and note that there is no default to
        # inherit, because ``WalletRepository`` has no owner-less read left for a
        # forgotten actor to fall into. A service built for the wrong user can
        # read nothing of the right one's.
        self._actor = actor
        # ``None`` means this installation has no notification address, which is
        # the ordinary state of a fresh install rather than an error: every
        # operation behaves exactly as it would otherwise and simply says nothing
        # about it. Note what this address is *not* - it is never a condition on
        # moving money, only on where the words about it go.
        self._recipient = recipient

    # --- the two balances ---------------------------------------------------

    def deposit(self, wallet_id, amount: Money, internal_reference: str) -> Transaction:
        return self._run(DepositMoney, wallet_id, amount, internal_reference)

    def withdraw(self, wallet_id, amount: Money, internal_reference: str) -> Transaction:
        return self._run(WithdrawMoney, wallet_id, amount, internal_reference)

    def payout_from_locked(
        self,
        wallet_id,
        amount: Money,
        internal_reference: str,
        destination: Destination,
        as_of: datetime,
        fund_name: str | None = None,
    ) -> Transaction:
        """Spend the locked pots, sending value out to an external account.

        ``fund_name`` names the pot to draw on, and leaving it out is not the
        same command with a default - it is a different act. Naming one spends
        that pot and records it on the ledger. Naming none falls back to the
        pooled draw, which exists for the caller that genuinely cannot say: a
        plan saved before pots could be named, whose ``fund_id`` is ``NULL``.

        **This method never passes ``committed_at``, and that is the rule rather
        than an omission.** The business-pot exemption is for a *scheduled*
        payment - one with a plan behind it - and nothing reached from here has
        a plan. So an ad-hoc ``payout --fund`` against a sealed business pot is
        refused by the pot, before its maturity date, however the pot was named.
        The substitution is silent if you only read this signature, which is why
        it is written down.
        """
        return self._run(
            PayoutFromLocked,
            wallet_id,
            amount,
            internal_reference,
            destination=destination,
            as_of=as_of,
            fund_name=fund_name,
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

    # --- pots ---------------------------------------------------------------

    def open_fund(
        self,
        wallet_id: UUID,
        name: str,
        kind: FundKind,
        maturity_date=None,
        as_of: datetime | None = None,
    ) -> Fund:
        """Open a named pot on a wallet.

        No ledger row: opening a pot moves no money, so the only thing to persist
        is the wallet's new shape. That is why this is a method here rather than a
        ``WalletOperation`` - there is no movement to record, and nothing to
        deduplicate, because the aggregate already refuses a duplicate name.
        """
        uow = self._unit_of_work_factory.start()
        try:
            wallet = self._wallet(uow, wallet_id)
            fund = wallet.open_fund(name, kind, maturity_date, as_of)
            uow.wallets.save(wallet)
            uow.commit()
            return fund
        except BaseException:
            uow.rollback()
            raise

    def extend_fund(
        self, wallet_id: UUID, name: str, new_date, as_of: datetime
    ) -> Fund:
        """Push a pot's maturity date later - never earlier.

        Also no ledger row, for the same reason as ``open_fund``: a date changing
        moves no money. The refusal for a date that is not later, or not in the
        future, comes from the aggregate.

        ``as_of`` is required rather than defaulted to ``datetime.now()`` here,
        because "is this date in the future?" is a judgement this method makes -
        and a use case that reads its own clock cannot be asked what it would do
        at a given moment. The adapter holding the command line supplies the
        moment, which is the arrangement every other use case already has.
        """
        uow = self._unit_of_work_factory.start()
        try:
            wallet = self._wallet(uow, wallet_id)
            fund = wallet.fund_by_name(name)
            wallet.extend_fund(fund.fund_id, new_date, as_of)
            uow.wallets.save(wallet)
            uow.commit()
            return fund
        except BaseException:
            uow.rollback()
            raise

    def deposit_into_fund(
        self,
        wallet_id,
        fund_name: str,
        amount: Money,
        internal_reference: str,
        as_of: datetime,
    ) -> Transaction:
        """Bring money in from outside, straight into a named pot.

        ``as_of`` is required because this is the operation that stamps the pot's
        ``first_funded_at`` - the moment a commitment has to predate for the pot
        to authorise an early business payment.
        """
        return self._run(
            DepositIntoFund,
            wallet_id,
            amount,
            internal_reference,
            fund_name=fund_name,
            as_of=as_of,
        )

    def lock_into_fund(
        self,
        wallet_id,
        fund_name: str,
        amount: Money,
        internal_reference: str,
        as_of: datetime,
    ) -> Transaction:
        """Move money from the available balance into a named pot.

        ``as_of`` is required for the same reason as ``deposit_into_fund``: money
        arriving in a pot stamps its funding moment, and it makes no difference
        to the pot whether that money came from outside or from the wallet's own
        available balance.
        """
        return self._run(
            LockIntoFund,
            wallet_id,
            amount,
            internal_reference,
            fund_name=fund_name,
            as_of=as_of,
        )

    def release_from_fund(
        self,
        wallet_id,
        fund_name: str,
        amount: Money,
        internal_reference: str,
        as_of: datetime,
    ) -> Transaction:
        """Move money out of a named pot, refused until the pot has come due."""
        return self._run(
            ReleaseFromFund,
            wallet_id,
            amount,
            internal_reference,
            fund_name=fund_name,
            as_of=as_of,
        )

    def funds_for_wallet(self, wallet_id: UUID) -> list[Fund]:
        """Read a wallet's pots, in the order they were opened.

        Pure read like ``get_wallet``. The wallet itself is validated first so
        that "no such wallet" and "a wallet with no pots yet" stay
        distinguishable - the same distinction ``transactions_for_wallet`` makes.
        """
        uow = self._unit_of_work_factory.start()
        try:
            return list(self._wallet(uow, wallet_id).funds)
        finally:
            uow.rollback()

    # --- reading and status -------------------------------------------------

    def get_wallet(self, wallet_id: UUID) -> Wallet:
        """Read a wallet's current state.

        Pure read: nothing is committed. Absence raises WalletNotFoundError.
        """
        uow = self._unit_of_work_factory.start()
        try:
            return self._wallet(uow, wallet_id)
        finally:
            uow.rollback()

    def open_wallet(self, currency: Currency) -> Wallet:
        """Open a new empty wallet for this service's actor, in the given currency.

        The owner is not a parameter. It used to be - ``open_wallet(user_id,
        currency)`` - and the argument was the one place a caller could have
        named somebody else, so it is gone: the wallet is opened for whoever this
        service acts for, and there is no longer a spelling that opens one for
        anybody else.
        """
        uow = self._unit_of_work_factory.start()
        try:
            wallet = Wallet(
                wallet_id=uuid4(),
                user_id=self._actor,
                status=WalletStatus.ACTIVE,
                _available_balance=Money(Decimal("0"), currency),
                currency=currency,
            )
            uow.wallets.save(wallet)
            uow.commit()
            return wallet
        except BaseException:
            uow.rollback()
            raise

    def transactions_for_wallet(self, wallet_id: UUID) -> list[Transaction]:
        """Read a wallet's full transaction ledger, oldest first.

        Pure read like get_wallet. Absence of the wallet itself raises
        WalletNotFoundError (an unknown wallet is different from an empty one).
        """
        uow = self._unit_of_work_factory.start()
        try:
            # Validate the wallet exists so "unknown wallet" and "no activity
            # yet" are distinguishable to the caller.
            self._wallet(uow, wallet_id)
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
            wallet = self._wallet(uow, wallet_id)
            transition(wallet)
            uow.wallets.save(wallet)
            uow.commit()
            return wallet
        except BaseException:
            uow.rollback()
            raise

    def _wallet(self, uow: UnitOfWork, wallet_id: UUID) -> Wallet:
        """The wallet this service's actor owns, or ``WalletNotFoundError``.

        **The single door every wallet read in this class goes through**, and
        that is what makes the actor impossible to forget: there is no other way
        to reach a wallet from here, and the repository behind this call cannot
        return one belonging to somebody else. Adding a method later means
        reaching for this helper, because the alternative no longer exists.

        A wallet belonging to another user and a wallet that does not exist raise
        the same error, so no caller here can tell the two apart - which is the
        point rather than a side effect. See ``WalletRepository.get_owned``.
        """
        return uow.wallets.get_owned(wallet_id, self._actor)

    def _run(
        self,
        operation_cls,
        wallet_id: UUID,
        amount: Money,
        internal_reference: str,
        destination: Destination | None = None,
        **extra,
    ) -> Transaction:
        """Open a unit, build the operation against the loaded wallet, run it.

        ``**extra`` exists because the operations no longer share one constructor
        shape. A pot-scoped operation needs to know *which* pot, and only the
        loaded wallet can answer that - so the name travels here as an argument
        and the operation resolves it itself. ``operation_cls`` is still taken
        separately, and still the real class, because ``ANNOUNCED`` is keyed on
        it; a builder callable would have hidden the very thing the lookup needs.
        """
        uow = self._unit_of_work_factory.start()
        try:
            wallet = self._wallet(uow, wallet_id)
            operation: WalletOperation = operation_cls(
                wallet, uow.transactions, **extra
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

        Silent for every operation that only moves money between the wallet's own
        balances, and the silence is a lookup rather than a condition. Keeping the
        list in ``ANNOUNCED`` means "does this operation notify?" has one answer
        in one place, instead of being spread through the flow every operation
        shares.

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
