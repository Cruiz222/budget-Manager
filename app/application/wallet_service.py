from collections.abc import Callable
from dataclasses import dataclass
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
from app.domain.identity.tier import (
    check_credit,
    check_outflow,
    limit_day_bounds,
    tier_for,
)
from app.domain.money.confirmation import Confirmation
from app.domain.money.confirmationKind import ConfirmationKind
from app.domain.money.confirmationStatus import ConfirmationStatus
from app.domain.money.currency import Currency
from app.domain.money.destination import Destination
from app.domain.money.exception import (
    ConfirmationAlreadyUsedError,
    ConfirmationExpiredError,
    ConfirmationNotFoundError,
    InvalidConfirmationWindowError,
    MoneyError,
    WalletHasActivePlansError,
)
from app.domain.money.fund import Fund
from app.domain.money.fundKind import FundKind
from app.domain.money.money import Money
from app.domain.money.reference import scoped_reference
from app.domain.money.transaction import Transaction
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.transactionType import OUTBOUND_TYPES, TransactionType
from app.domain.money.wallet import Wallet
from app.domain.money.walletStatus import WalletStatus
from app.domain.notifications.notificationKind import NotificationKind
from app.domain.planning.planStatus import PlanStatus


@dataclass(frozen=True)
class RequestedConfirmation:
    """A recorded request, and whether *this* call is the one that recorded it.

    Two fields rather than one, and the second carries no information about the
    request at all - which is exactly why it is here instead of on the aggregate.
    ``created`` is a fact about the call, not about the row: the same request
    returned to a retry has to answer "was a new one made?", and there is no way
    to read that off a ``Confirmation``, because a confirmed request and a
    freshly-created one are the same object shape.

    **The presentation turns it into a status code and nothing else.** 201 when
    ``created``, 200 when not, which is how a client that retried a request whose
    response it never saw can tell "here is your request again" from "here is a
    new one". It is deliberately not a field on the wire: a body that said
    ``"created": false`` would invite a client to branch on a fact about one
    call, and the same request read back later has no such fact to report.

    The CLI reads ``created`` not at all, and that is correct - it shows the
    person the record either way, which is what makes a reused ``--ref`` safe
    rather than silently wrong.
    """

    confirmation: Confirmation
    created: bool


@dataclass(frozen=True)
class ConfirmedOperation:
    """What one answered confirmation produced.

    Three things, and the third is sometimes absent on purpose. ``transaction``
    is ``None`` for a ``CLOSE``, which moves no money and so writes no ledger row
    - the same asymmetry ``close_wallet`` always had, carried through rather than
    papered over with a synthetic row.

    The wallet comes back because it is the thing a person actually asks about
    next, and this is the only place it can come from: the balance after the
    movement exists only inside the unit that made it, so a caller that had the
    transaction and not the wallet would have to make a second read to find out
    what the operation did.

    The confirmation comes back **as it was persisted**, re-read rather than
    assembled from what the caller hoped happened. See ``WalletService.confirm``.
    """

    confirmation: Confirmation
    wallet: Wallet
    transaction: Transaction | None


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

    # --- money leaving: the confirmed path ----------------------------------
    #
    # Three operations move money out of a wallet for good - a withdrawal, a
    # payout, and the close that ends the wallet itself. Every one of them is now
    # reached in two steps: a **confirmation** is recorded, and then it is
    # answered. There is deliberately no one-step spelling of any of them.
    #
    # **Why the four operations below are private, rather than public methods
    # that take a confirmation.** The obvious design is
    # ``withdraw(wallet_id, amount, ref, confirmation)`` - a required argument, so
    # an unconfirmed call cannot be written. It is not enough, and the reasons are
    # the ones this codebase has already made twice:
    #
    # - A required argument is not a *checked* one. ``Confirmation`` is an
    #   ordinary dataclass, so a caller can build one by hand, name a
    #   ``confirmation_id`` that is not in the database, and move money with it.
    #   Nothing would have been recorded and nothing spent. Closing that hole
    #   means validating the argument against the store - at which point the
    #   argument was really an id, and the operation should have been reading the
    #   record instead of being handed a copy of it.
    # - Passing both a confirmation and the amount it authorises is two records
    #   of one fact with a "they must agree" rule, which is the trap ``Wallet``
    #   names for a stored ``locked_balance``: the day they disagree, the bug is
    #   in the gap. Here the gap would be a confirmation for 500 authorising a
    #   withdrawal of 500,000.
    #
    # So the operations read their arguments *from the record*, and there is no
    # longer any spelling of them that a caller outside this class can reach.
    # This is the shape ``open_wallet`` already took when it lost its ``user_id``
    # argument - "the argument was the one place a caller could have named
    # somebody else, so it is gone" - applied to money leaving instead of to
    # ownership.
    #
    # **The scheduler does not come through here, and does not need to.**
    # ``ExecutePlanRun`` builds its operations directly and confirms nothing;
    # see the README. A confirmation is for a person deciding, and a run that
    # fires at 3am has no person in it.

    def request_confirmation(
        self,
        wallet_id: UUID,
        kind: ConfirmationKind,
        now: datetime,
        internal_reference: str | None = None,
        amount: Money | None = None,
        destination: Destination | None = None,
        fund_name: str | None = None,
    ) -> RequestedConfirmation:
        """Record a request to move money out. **Nothing is moved.**

        The first of the two steps, and it is deliberately inert: no balance is
        read for the decision, no ledger row is written, and the wallet is
        touched only to prove the caller owns it. Everything this returns is a
        request that could still be refused when it is answered, which is why the
        balance is not checked here.

        **The absent balance check is the design, not an omission.** A check at
        this moment cannot be relied on - the balance at *confirm* time is the one
        that decides - so it would be a second, weaker copy of the wallet's own
        rule, free to disagree with it. And its absence is a feature rather than
        a gap: a request made against an empty wallet is still answerable once
        the wallet has been topped up, which is exactly what somebody who is
        about to be paid would want.

        **A reference already in use returns the request it already names.**
        ``add`` is a claim - an insert that reports whether it was the one that
        happened - so a client that retried a request whose response it never saw
        gets *its own request* back rather than a second one. That matters more
        here than it did one table down: a duplicated confirmation is harmless on
        its own, but the reference it would share is what the ledger row
        underneath deduplicates on, and the chain is what keeps a spent
        reference from ever being read back as an outcome.

        ``now`` is passed in rather than read, for the reason every use case here
        takes a moment: the adapter knows what time it is, and a request that
        cannot be told when it was made cannot be tested for its window.
        """
        uow = self._unit_of_work_factory.start()
        try:
            # Through the one actor-scoped door, so somebody else's wallet is a
            # not-found before a request is built against it. The wallet itself is
            # otherwise unread: only ``wallet_id`` is used.
            self._wallet(uow, wallet_id)

            confirmation = Confirmation.requested(
                user_id=self._actor,
                wallet_id=wallet_id,
                kind=kind,
                internal_reference=(
                    internal_reference if internal_reference is not None
                    else str(uuid4())
                ),
                now=now,
                amount=amount,
                destination=destination,
                fund_name=fund_name,
            )

            if uow.confirmations.add(confirmation):
                uow.commit()
                return RequestedConfirmation(confirmation, created=True)

            # The key was taken. Note this is not a check-then-write: the claim
            # above *is* the decision, and this branch is the answer to it. A
            # ``find`` first would have had a gap between the looking and the
            # writing, and two retries arriving in that gap would both have
            # believed they were first.
            existing = uow.confirmations.find(
                confirmation.wallet_id, confirmation.internal_reference
            )
            if existing is None:
                # Unreachable: ``add`` only reports false when the unique key is
                # already there, and that row cannot be deleted in between - the
                # lookup is a read inside the same transaction that saw the
                # conflict. Refused rather than returned as None, because a
                # caller handed None here would go on to confirm nothing.
                raise ConfirmationNotFoundError(
                    f"the reference {confirmation.internal_reference!r} is taken "
                    "but names no request"
                )
            uow.rollback()
            return RequestedConfirmation(existing, created=False)
        except BaseException:
            uow.rollback()
            raise

    def confirm(self, confirmation_id: UUID, as_of: datetime) -> ConfirmedOperation:
        """Answer a recorded request: **this is where the money moves.**

        The second of the two steps, and the only door to the four operations
        below. It reads the request, refuses it if it cannot be answered, and
        then dispatches on its ``kind`` to the same operation the direct call
        always used - so no rule holds on this path and not on the other, because
        there is no other.

        **The moment a request is refused is the moment it is spent, for the
        three operations that write a ledger row.** A confirm against a frozen
        wallet records a FAILED row and spends the request with it, in one
        transaction, so the client must ask again. That looks harsh and it is the
        safe direction: a refused attempt that left the request answerable would
        re-enter the ledger with the same reference the refusal had already
        written, be handed the old FAILED row back, move nothing and report
        success. Spending it is what keeps a spent reference from ever being read
        as an outcome. The rule is not "an attempt spends it" - it is *the spend
        is committed whenever the refusal is recorded*.

        **A refused close is the exception, and it is the rule above read
        correctly rather than an inconsistency.** ``_close_wallet`` writes no
        ledger row when it refuses - there is no movement to record - so there is
        no stale row and nothing to spend the request against. Its unit rolls
        back entirely, the request stays answerable, and the client can empty the
        wallet and answer it again. Which is what somebody who was told "move
        your money out first" is going to do next anyway.

        **The response is read back, not assembled.** The confirmation returned
        here is re-read from the store after the operation committed, and so is
        the wallet - so what a caller is told is what was persisted, rather than a
        second derivation from the arguments that could disagree with it. That is
        the position ``ExecutePlanRun._move_the_money`` takes about reading an
        outcome off the rows, applied to a response.
        """
        confirmation = self._answerable(confirmation_id, as_of)

        if confirmation.kind is ConfirmationKind.WITHDRAWAL:
            transaction = self._withdraw(confirmation, as_of)
        elif confirmation.kind is ConfirmationKind.PAYOUT_FROM_AVAILABLE:
            transaction = self._payout_from_available(confirmation, as_of)
        elif confirmation.kind is ConfirmationKind.PAYOUT_FROM_LOCKED:
            transaction = self._payout_from_locked(confirmation, as_of)
        else:
            # CLOSE, and the only kind that produces no ledger row.
            self._close_wallet(confirmation, as_of)
            transaction = None

        return self._confirmed_operation(confirmation_id, transaction)

    def get_confirmation(self, confirmation_id: UUID, as_of: datetime) -> Confirmation:
        """Read one of this actor's requests, and what its window means now.

        Pure read, like ``get_wallet``: nothing is committed, and a request
        belonging to somebody else is the same not-found a random id gets.

        ``as_of`` is required rather than read from the clock, because the
        interesting thing to report about a request is whether it can still be
        answered - and answering that from this server's clock would make the
        answer unaskable. Note the *status* is unaffected: a request past its
        window is still stored ``AWAITING`` and reads as ``EXPIRED`` through
        ``Confirmation.status_as_of``. Nothing is written by looking.
        """
        uow = self._unit_of_work_factory.start()
        try:
            return uow.confirmations.get_owned(confirmation_id, self._actor)
        finally:
            uow.rollback()

    def _answerable(self, confirmation_id: UUID, as_of: datetime) -> Confirmation:
        """The request, if it can still be answered - otherwise the refusal.

        A pure read, so it decides nothing on its own; the gate is
        ``ConfirmationRepository.claim``, which runs inside the money's unit and
        answers the same question atomically. This exists so that a request that
        can never be answered is refused *before* a second connection is opened
        and so that the caller is told which of the two reasons applies - which
        the claim alone could do, but only after it had already been attempted.
        """
        confirmation = self.get_confirmation(confirmation_id, as_of)
        status = confirmation.status_as_of(as_of)

        if status is ConfirmationStatus.EXPIRED:
            raise ConfirmationExpiredError(
                f"confirmation {confirmation_id} expired at "
                f"{confirmation.expires_at.isoformat()} and can no longer be answered"
            )

        if status is ConfirmationStatus.CONFIRMED:
            raise ConfirmationAlreadyUsedError(
                f"confirmation {confirmation_id} has already been answered"
            )

        return confirmation

    def _confirmed_operation(
        self, confirmation_id: UUID, transaction: Transaction | None
    ) -> ConfirmedOperation:
        """Read back the request and the wallet the run just settled on."""
        uow = self._unit_of_work_factory.start()
        try:
            confirmation = uow.confirmations.get_owned(confirmation_id, self._actor)
            wallet = self._wallet(uow, confirmation.wallet_id)
            return ConfirmedOperation(
                confirmation=confirmation, wallet=wallet, transaction=transaction
            )
        finally:
            uow.rollback()

    # --- the four operations a confirmation dispatches to -------------------
    #
    # One kind, one method, and each takes nothing but the record it came from -
    # which is what makes a confirmation unable to authorise a movement other
    # than the one it names. The parameters are read off ``confirmation`` and
    # never passed in beside it.

    def _withdraw(self, confirmation: Confirmation, as_of: datetime) -> Transaction:
        return self._run(
            WithdrawMoney,
            confirmation.wallet_id,
            confirmation.amount,
            confirmation.internal_reference,
            confirmation=confirmation,
            confirmation_as_of=as_of,
            now=as_of,
        )

    def _payout_from_available(
        self, confirmation: Confirmation, as_of: datetime
    ) -> Transaction:
        return self._run(
            PayoutFromAvailable,
            confirmation.wallet_id,
            confirmation.amount,
            confirmation.internal_reference,
            destination=confirmation.destination,
            confirmation=confirmation,
            confirmation_as_of=as_of,
            now=as_of,
        )

    def _payout_from_locked(
        self, confirmation: Confirmation, as_of: datetime
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

        Note ``as_of`` is handed to ``_run`` twice under two names, and they are
        two genuinely different questions wearing one answer: ``as_of`` judges
        whether a pot has come due, and ``confirmation_as_of`` judges whether the
        request is still inside its window. They are the same moment because one
        command is happening, and they would part company the day a request could
        be answered on behalf of a later one.
        """
        return self._run(
            PayoutFromLocked,
            confirmation.wallet_id,
            confirmation.amount,
            confirmation.internal_reference,
            destination=confirmation.destination,
            fund_name=confirmation.fund_name,
            as_of=as_of,
            confirmation=confirmation,
            confirmation_as_of=as_of,
            now=as_of,
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

    def _close_wallet(self, confirmation: Confirmation, as_of: datetime) -> Wallet:
        """Close a wallet for good - the one transition with no way back.

        **Not through ``_change_status``, and both reasons are load-bearing.**
        First, close is the only status transition with a precondition: a wallet
        may only be closed when it is empty, and ``Wallet.close`` refuses
        otherwise. Second, and less obviously, a rejection here must not write a
        ledger row - and ``_change_status`` already behaves correctly for that,
        but it has nowhere to put a second check that needs a *different
        aggregate*.

        That second check is plans. A live plan on a closed wallet would fail on
        every tick for the rest of its life, with the failure recorded in a
        ``plan_runs`` row nobody reads - the exact shape of quiet breakage the
        ``PlanRun`` records exist to prevent. ``Wallet`` cannot make this rule: it
        does not import planning, and a wallet has no idea it is being saved for.
        So the rule lives here, which is the only layer that loads both.

        **The check is inside the unit, and that is why this is not two calls to
        two services.** Evaluated outside a transaction, a plan could be created
        between the check and the close - and the wallet would close with a live
        plan on it, which is precisely the state the check exists to prevent.
        Reading ``uow.plans`` means the two reads and the write are one snapshot.
        It does not make the guarantee absolute (another unit could still commit a
        new plan immediately afterwards), but it removes the window this code is
        able to remove.

        Non-terminal means ACTIVE or PAUSED. ``PAUSED`` counts because a paused
        plan is waiting for a human and can resume; it is not finished, and a
        wallet closed under one would break the resume.

        **The rollback is what makes a refused close retryable**, and it is
        deliberate rather than incidental. Nothing here writes a ledger row when
        it refuses - there is no movement to record - so there is no stale FAILED
        row for a retry to be handed back, and therefore nothing to spend the
        confirmation against. The whole unit goes back, the claim included, and
        the request stays answerable. Emptying the wallet and confirming again is
        exactly what the refusal told the caller to do. See ``confirm``.
        """
        uow = self._unit_of_work_factory.start()
        try:
            wallet = self._wallet(uow, confirmation.wallet_id)
            claimed = self._claim(uow, confirmation, as_of)
            self._refuse_close_if_committed(uow, wallet)
            wallet.close()
            uow.wallets.save(wallet)
            if claimed is not None:
                # ``claim`` has already moved it to CONFIRMED in memory; this is
                # the write that makes that true of the row. ``transaction_id``
                # stays NULL, because close produces no ledger row and writing
                # something else here would make a reader believe one existed.
                uow.confirmations.save(claimed)
            uow.commit()
            return wallet
        except BaseException:
            uow.rollback()
            raise

    def _refuse_close_if_committed(self, uow: UnitOfWork, wallet: Wallet) -> None:
        """Refuse the close while any plan on this wallet is still live.

        Separate from ``close_wallet`` so the wallet's own rule and this one stay
        distinguishable at the call site: one is about money still inside, the
        other about a promise still outstanding, and a caller reading only
        ``close_wallet`` should be able to see that both are asked.

        The plans are read as this service's actor, through the same ownership
        check every other read uses, so a wallet belonging to somebody else still
        fails at ``_wallet`` with a not-found rather than surfacing anything about
        their plans.
        """
        active = [
            plan
            for plan in uow.plans.get_by_wallet_id(wallet.wallet_id, self._actor)
            if plan.status in (PlanStatus.ACTIVE, PlanStatus.PAUSED)
        ]
        if active:
            names = ", ".join(repr(plan.name) for plan in active)
            raise WalletHasActivePlansError(
                f"this wallet still has {len(active)} live plan(s): {names}. "
                "Cancel them before closing it."
            )

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

    def _claim(
        self,
        uow: UnitOfWork,
        confirmation: Confirmation | None,
        as_of: datetime | None,
    ) -> Confirmation | None:
        """Spend the confirmation inside the money's unit, or nothing at all.

        Returns the now-``CONFIRMED`` record so the caller can write it back
        beside the ledger row, and returns ``None`` for a call that carried no
        confirmation - which nothing in this class makes any more, but which
        keeps the helper honest about being optional rather than quietly
        pretending every call has one.

        **This is the gate, and it is inside the money's transaction on
        purpose.** The decision and the write are one statement
        (``ConfirmationRepository.claim``), so two confirms racing for one
        request cannot both find it answerable - the loser's UPDATE matches no
        row. And because it runs in this unit, a rollback here un-spends it,
        which is exactly what a refused close needs.

        ``as_of`` is required whenever a confirmation is passed, and that is an
        assertion rather than a convenience: a confirmation has a window, and
        the window cannot be judged without a moment. A call that named a
        confirmation and no moment would be one where "is this still answerable?"
        was silently decided by nothing.
        """
        if confirmation is None:
            return None
        if as_of is None:
            raise InvalidConfirmationWindowError(
                "a confirmation cannot be spent without the moment to judge its "
                "window against"
            )
        return uow.confirmations.claim(
            confirmation.confirmation_id, self._actor, confirmation.kind, as_of
        )

    def _run(
        self,
        operation_cls,
        wallet_id: UUID,
        amount: Money,
        internal_reference: str,
        destination: Destination | None = None,
        confirmation: Confirmation | None = None,
        confirmation_as_of: datetime | None = None,
        now: datetime | None = None,
        **extra,
    ) -> Transaction:
        """Open a unit, build the operation against the loaded wallet, run it.

        ``**extra`` exists because the operations no longer share one constructor
        shape. A pot-scoped operation needs to know *which* pot, and only the
        loaded wallet can answer that - so the name travels here as an argument
        and the operation resolves it itself. ``operation_cls`` is still taken
        separately, and still the real class, because ``ANNOUNCED`` is keyed on
        it; a builder callable would have hidden the very thing the lookup needs.

        ``confirmation`` and ``confirmation_as_of`` are two parameters rather
        than one, and the split is not cosmetic. ``as_of`` already travels
        through ``**extra`` into the pot-scoped constructors - ``PayoutFromLocked``,
        ``ReleaseFromLocked``, ``LockIntoFund`` - because it judges whether a pot
        has come due. Promoting it to a named parameter here would silently stop
        it reaching them, and a pot that never comes due is not a loud failure.
        So the confirmation's own moment arrives under a name of its own.

        ``confirmation_as_of`` is deliberately *not* part of ``**extra``: it is
        consumed by ``_claim`` and must never reach an operation's constructor,
        which would be a parameter the operation has no idea it accepted.

        ``now`` is the third moment this method takes, and it is the same moment
        as ``as_of`` at every call site for the reason ``_payout_from_locked``
        gives about the other two: one command is happening. It is a separate
        name because it answers a separate question - *which limit-day is this
        movement in* - and because it has to be a named parameter to be read here
        at all, while ``as_of`` still has to keep travelling through ``**extra``
        to the pot-scoped constructors that judge maturity. It is optional
        because only a movement *out* needs it: a credit is judged against the
        balance it would produce and an internal move faces no ceiling, so
        neither reads a day. An outflow that reached here without one would be a
        bug in this class rather than a caller's mistake, and ``_ceiling`` says
        so.

        **``now`` has two readers and they agree by construction.** ``_ceiling``
        computes the limit-day from it, and ``operation.now`` stamps the row that
        day total will later be summed from. One value, one day - which is the
        property the daily cap needs and the reason the stamp is set here rather
        than left to ``Transaction``'s default of the clock. See
        ``WalletOperation.now``.

        **A refusal spends the confirmation, on this path.** ``except MoneyError``
        commits - it has to, or the FAILED row it just wrote would be lost - and
        the claim went into that same unit, so both land together. A client whose
        confirm was refused must ask again, and that is the safe direction: a
        refusal that left the request answerable would let a retry re-enter the
        ledger under the same reference, be handed the old FAILED row back, move
        nothing and report success. See ``confirm`` for the full argument, and
        ``_close_wallet`` for the one operation that is not like this.
        """
        uow = self._unit_of_work_factory.start()
        try:
            wallet = self._wallet(uow, wallet_id)
            # The spend happens before the movement, in this unit, so a request
            # cannot be answered twice and cannot be answered at all if the
            # movement then fails to commit.
            claimed = self._claim(uow, confirmation, confirmation_as_of)
            operation: WalletOperation = operation_cls(
                wallet, uow.transactions, **extra
            )
            # The ceilings this movement faces, read here rather than in a use
            # case above, and *before* ``execute`` writes the row below - see
            # ``_ceiling`` for why the reading has to be this early and the
            # verdict cannot be.
            operation.guard = self._ceiling(uow, wallet, operation_cls, now)
            # The same moment again, this time onto the row ``execute`` is about
            # to write. A movement judged in a limit-day must be *recorded* in it
            # or the day total the next movement reads would not contain it - so
            # the stamp and the ceiling have to come from one value, which is why
            # this line sits beside the one above rather than near the write it
            # affects. See ``WalletOperation.now``.
            operation.now = now
            transaction = operation.execute(
                amount,
                scoped_reference(wallet.wallet_id, internal_reference),
                destination,
            )
            # The wallet changed (or would have) - persist the aggregate's new
            # state in the same transaction as the ledger row above.
            uow.wallets.save(wallet)
            if claimed is not None:
                # Point the spent request at what it produced. This is audit and
                # nothing reads it to decide anything - but it is the row that
                # answers "what did this confirmation actually buy?", and it is
                # free here because the transaction id is already in hand.
                claimed.record(transaction.transaction_id)
                uow.confirmations.save(claimed)
            # And the receipt, before the commit that makes the money real - see
            # the class docstring. Note this is only reached when the operation
            # *returned*: a rejection went to the ``except MoneyError`` below and
            # is already reported to the user as an error line, so it needs no
            # email.
            self._announce(uow, operation_cls, wallet, transaction)
        except MoneyError:
            # The wallet rejected the attempt. The operation already recorded a
            # FAILED row - commit it so the audit trail survives, then tell the
            # caller what happened. The claim above commits with it: the attempt
            # spends the confirmation.
            uow.commit()
            raise
        except BaseException:
            # Real failure: nothing may be left half-written. The claim rolls
            # back too, so a crash before the commit leaves the request
            # answerable rather than silently spent by a movement that never
            # happened.
            uow.rollback()
            raise
        else:
            uow.commit()
            return transaction

    def _ceiling(
        self,
        uow: UnitOfWork,
        wallet: Wallet,
        operation_cls,
        now: datetime | None,
    ) -> Callable[[Money], None] | None:
        """The tier ceilings this movement faces, bound to the values they compare.

        Returns a callable rather than a verdict, and the delay is the design:
        ``check_credit``/``check_outflow`` are asked by ``WalletOperation.execute``
        *after* it has written the PENDING row, so the error they raise is caught
        by the very ``except MoneyError`` every other refusal goes through and
        lands on the ledger as FAILED. A verdict reached here, one layer up,
        would be a refusal with no row - and "how many movements did this cap turn
        away this week?" is not a question prose answers. See
        ``WalletOperation.guard``.

        **What has to be read here rather than in the guard**, and the two
        directions differ in it:

        - An outflow's day total is read *now*, once, and captured - because a
          PENDING row counts toward its own day (see
          ``TransactionRepository.outflow_total_between``), so a total read at
          guard time would include the movement it is meant to judge and refuse
          at half the cap.
        - A credit's balance is read *at guard time*, off the wallet captured
          here, because that is the moment before ``_apply`` mutates it. Nothing
          between the two moments moves money - writing a ledger row does not -
          so it is the pre-movement balance either way.

        ``tier_for`` is handed whatever ``find_for_user`` returns, which is
        ``None`` for every account that has never filled a profile in. That is the
        ordinary state of every account that existed before this feature, and it
        answers ``UNVERIFIED`` rather than raising, so a missing profile is not a
        new way for a withdrawal to fail.

        **Which movement faces which ceiling is a classification, not a guess.**
        ``TransactionType.OUTBOUND_TYPES`` is the two types that carry value
        across the wallet's edge; the two internal members fall past both branches
        to ``None`` on purpose, because moving money into a pot is not spending
        it. A fifth member of that enum cannot quietly end up outside the cap -
        a test over the enum requires it to be placed on one side or the other.

        ``now`` is required for an outflow and unread by everything else. An
        outflow that reached here without one is a *bug in this class* rather than
        a caller's mistake, which is why it raises ``ValueError`` and not one of
        the domain's own classes - and why that matters: it is not a
        ``MoneyError``, so it rolls the unit back instead of committing a FAILED
        row. A refusal the caller could have avoided is recorded; a bug is not
        dressed up as one.
        """
        tier = tier_for(uow.profiles.find_for_user(wallet.user_id))
        transaction_type = operation_cls.transaction_type

        if transaction_type is TransactionType.DEPOSIT:
            # Available and locked together, because a pot is money this account
            # still holds - see ``check_credit``.
            return lambda amount: check_credit(
                tier, wallet.available_balance + wallet.locked_balance + amount
            )

        if transaction_type in OUTBOUND_TYPES:
            if now is None:
                raise ValueError(
                    f"{operation_cls.__name__} moves value out of the wallet and "
                    "was run without a moment, so the limit-day it belongs to "
                    "cannot be named"
                )
            day_start, day_end = limit_day_bounds(now)
            outflow_today = uow.transactions.outflow_total_between(
                wallet.wallet_id, day_start, day_end, wallet.currency
            )
            return lambda amount: check_outflow(tier, amount, outflow_today)

        # LOCK_FUNDS and UNLOCK_FUNDS: the wallet's own two balances, reshuffled.
        return None

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

        **Silent too unless the row actually succeeded**, which is the second
        reason to send nothing and a different one. A row in ``ANNOUNCED`` says
        the operation is worth telling the owner about; the row's status says
        whether the thing worth telling them about has happened. Every sentence
        ``compose.wallet_movement`` writes is past tense and specific - *"5000.00
        NGN left the wallet"*, *"5000.00 NGN was paid to ..."* - so a receipt
        composed here would assert a transfer that no one has made, to a person
        who may act on it.

        **The guard reads ``is not SUCCESSFUL`` rather than ``is not PENDING``,
        and the difference is a bug that was reachable.** PENDING and FAILED are
        both "not yet a fact", but only the first was being skipped - so a FAILED
        row reaching here was announced, which is the loudest possible version of
        the lie above: a receipt for a movement that was *refused*. Nothing could
        reach this with one today, because ``WalletOperation.execute`` now raises
        rather than returning a FAILED row (see ``ReferenceAlreadyRefusedError``)
        - but the guard was asking the narrower question, and the state it failed
        to exclude is the one that must never be announced. ``SettlePayment``'s
        own ``_announce`` asks it the wide way, and the two now agree.

        It would also be built from a lie about time: ``wallet_movement`` stamps
        the message with ``transaction.completed_at``, which is ``None`` until a
        transaction settles. So the check below is doing two jobs, and it is
        worth knowing that the second one exists - without it, the first pending
        withdrawal either raises inside the run's own unit and rolls the
        withdrawal back, or quietly queues a receipt with no timestamp.

        The receipt arrives in the phase that settles these movements, queued at
        the moment there is something true to say, which is also how a bank
        notification behaves: it arrives when the transfer lands, not when it is
        requested. Note the check is on the transaction and not on the operation
        class, so an operation that settles immediately but happens to be handed
        a stale PENDING row is silent too - the row is the fact.

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

        if transaction.status is not TransactionStatus.SUCCESSFUL:
            return

        notification = compose.wallet_movement(
            kind, wallet, transaction, self._recipient
        )
        if notification is not None:
            uow.notifications.enqueue(notification)
