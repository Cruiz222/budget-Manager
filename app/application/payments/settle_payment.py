from app.application.notifications import compose
from app.application.payments.settledPayment import SettlementOutcome, SettledPayment
from app.application.unit_of_work import UnitOfWork, UnitOfWorkFactory
from app.domain.money.transaction import Transaction
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.transactionType import TransactionType
from app.domain.money.wallet import Wallet
from app.domain.money.walletStatus import WalletStatus
from app.domain.payments.providerEvent import ProviderEvent
from app.domain.payments.providerOutcome import ProviderOutcome

#: The row types a transfer event can be about. A transfer is money going *out*,
#: and there are exactly two ways this system sends money out.
_TRANSFER_TYPES = frozenset({TransactionType.WITHDRAWAL, TransactionType.PAYOUT})


class SettlePayment:
    """Apply a provider's word about a movement, and make the ledger agree with it.

    **This is not a method on ``WalletService``, and the reason is what a webhook
    is.** Every other money-moving entry point in this codebase starts by
    resolving an actor from a token, and ``dependencies.current_actor`` is the
    only function that can produce a ``User``. A provider event carries no user.
    It carries a signature, which is a different *kind* of authority - not "who
    are you" but "who sent this" - and the answer to a second kind of authority
    is a second, narrowly named thing rather than a privilege granted to the
    first. So this class has no actor, cannot be built with one, and is reachable
    from exactly one route. There is no flag saying "skip the ownership check",
    because there is no ownership check here to skip: the wallet is loaded
    *through* the owner the ledger row names, by the same ``get_owned`` door
    everything else uses.

    **It returns refusals as values, not exceptions.** Every branch below that
    moves no money returns a ``SettledPayment`` saying so - see
    ``SettlementOutcome`` for why that is not sloppiness but the only shape a
    webhook can answer with. The one thing that does raise is a genuine failure
    (a database that did not accept the write), and that propagating is correct:
    a 500 tells the provider to retry, and a retry of a broken database is what
    you want.

    **The four movements, and the table they make.** A charge credits; a transfer
    that succeeded credits nothing, because the wallet was already debited when
    the transfer was requested; a transfer that failed gives that debit back; and
    a transfer that was reversed gives back money that had genuinely left. The
    middle two are the ones worth reading twice - see ``release_hold`` for why
    giving money back is not ``apply_deposit``, and ``ProviderEvent`` for why a
    failure and a reversal are different facts that happen to pay out the same.

    **Idempotency is the row's own status, and no table is needed for it.** A
    second ``CHARGE_SUCCEEDED`` finds a row that is no longer PENDING and stops.
    That is sufficient only because the credit and the status change are written
    in *one* unit of work: there is no instant at which the money has moved and
    the row still says PENDING, so a retry cannot arrive in a window where it
    would be applied twice. If those two writes could commit separately, this
    class would need a claim in the database the way
    ``ConfirmationRepository.claim`` is one - and the reason it does not is that
    a confirmation's window is judged against a clock while this one's whole
    question is a single column of the row it is already holding.
    """

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        *,
        recipient: str | None = None,
    ):
        self._unit_of_work_factory = unit_of_work_factory
        # The same meaning as ``WalletService``'s: ``None`` is an installation
        # with no notification address, which is the ordinary state of a fresh
        # install rather than an error. Nothing here branches on it - the
        # composition does, once, in ``_composed``.
        self._recipient = recipient

    def settle(self, outcome: ProviderOutcome) -> SettledPayment:
        """Apply one provider event. Never raises for a payment that cannot be applied.

        There is deliberately no ``as_of``. The moment a settlement happens is
        stamped by ``Transaction.mark_successful`` and its siblings, which read
        the clock themselves and always have; nothing on this path judges a
        window or a maturity date, so a moment passed in would be a parameter
        that travelled three frames and was used by nobody. The rule elsewhere in
        this codebase - that a clock is read at the edge and handed down - exists
        to make *decisions* reproducible, and there is no decision here for it to
        make reproducible.
        """
        uow = self._unit_of_work_factory.start()
        try:
            row = uow.transactions.get_by_provider_reference(outcome.reference)
            if row is None:
                return self._refused(
                    uow,
                    outcome,
                    SettlementOutcome.UNKNOWN_REFERENCE,
                    "no ledger row is filed under this reference",
                )

            if row.amount != outcome.amount:
                # A partial payment is not a thing the provider's checkout does,
                # so a disagreement is a bug or tampering rather than a smaller
                # deposit - and crediting the arrived amount would make the
                # ledger row's own amount a lie, while refusing loudly would make
                # the provider retry something that can never succeed. So:
                # acknowledge, leave the row PENDING, and let reconciliation
                # report it. That last clause used to read "(which does not exist
                # yet)", and the reason it now names a job is worth the line: the
                # reconciler asks about this row on every run and is told the same
                # amount every time, so it reports the disagreement for ever
                # rather than resolving it. Nothing in this system decides which
                # of the two figures is right, and a row that is visible on every
                # run is the honest version of that.
                return self._refused(
                    uow,
                    outcome,
                    SettlementOutcome.AMOUNT_DISAGREES,
                    f"the event says {outcome.amount} and the row says "
                    f"{row.amount}; nothing was moved",
                )

            # The only two lines in the codebase that read a wallet without
            # knowing whose it is - and they know whose it is by the end of the
            # first. ``owner_of`` returns an id and nothing else, and the wallet
            # is then loaded through the owner-scoped door, so the money is still
            # only ever read as its owner's. See ``WalletRepository.owner_of``.
            owner = uow.wallets.owner_of(row.wallet_id)
            wallet = uow.wallets.get_owned(row.wallet_id, owner)

            refusal = self._apply(outcome, row, wallet)
            if refusal is not None:
                return self._refused(uow, outcome, refusal[0], refusal[1])

            uow.transactions.save(row)
            uow.wallets.save(wallet)
            self._announce(uow, wallet, row)
        except BaseException:
            uow.rollback()
            raise
        else:
            uow.commit()
            return SettledPayment(
                outcome=self._movement_for(outcome.event),
                reference=outcome.reference,
                detail=f"{outcome.event.value} applied to a {row.type.value}",
            )

    # --- the table ----------------------------------------------------------

    def _apply(
        self, outcome: ProviderOutcome, row: Transaction, wallet: Wallet
    ) -> tuple[SettlementOutcome, str] | None:
        """Move the money for one event, or say why not. ``None`` means it moved.

        Returns the refusal rather than raising it, for the reason the class
        gives: a refusal here is an answer, and the caller turns it into a 200.
        The shape is a tuple instead of a ``SettledPayment`` because a
        ``SettledPayment`` carries the *outcome* of the whole call, and this
        function does not know which movement it is completing - it knows only
        what was refused.

        Every branch checks the row's status *before* touching the wallet, and
        the ordering matters more than it looks. ``mark_successful`` would raise
        on a settled row, which is the aggregate's own guard and stays as the
        backstop - but a guard that raises cannot tell a webhook that this is the
        ordinary, expected, already-happened case, and the fix for that is not to
        catch the exception but to ask the question first. What the guard still
        buys is the impossible case: an event that gets past the status check and
        still cannot be applied raises loudly rather than quietly half-applying.
        """
        if outcome.event is ProviderEvent.CHARGE_SUCCEEDED:
            if row.type is not TransactionType.DEPOSIT:
                return (
                    SettlementOutcome.WRONG_KIND,
                    f"a charge cannot settle a {row.type.value}",
                )
            if row.status is not TransactionStatus.PENDING:
                return self._not_pending(row)
            if wallet.status is WalletStatus.CLOSED:
                # ``apply_deposit`` would raise here, and raising is the wrong
                # answer for the reason this whole class gives: a 500 makes the
                # provider retry a payment that will never apply. It is also the
                # wrong *outcome* to fold into a refusal about the request, so it
                # gets a name of its own - money has arrived for a wallet that
                # was closed between the payer opening the link and the payer
                # paying it, and the answer to that is a person, not a retry.
                return (
                    SettlementOutcome.WALLET_CLOSED,
                    "the wallet was closed after this deposit was asked for; "
                    "the money arrived and nothing can credit it",
                )
            # What arrived is what the event says, which the check above proved
            # is what the row asked for. Credit arrives-money rather than the
            # row's number so that a future change to the agreement rule shows up
            # here as an inconsistency rather than as a silently correct debit.
            wallet.apply_deposit(outcome.amount)
            row.mark_successful()
            return None

        if outcome.event is ProviderEvent.TRANSFER_SUCCEEDED:
            if row.type not in _TRANSFER_TYPES:
                return (
                    SettlementOutcome.WRONG_KIND,
                    f"a transfer cannot settle a {row.type.value}",
                )
            if row.status is not TransactionStatus.PENDING:
                return self._not_pending(row)
            # No balance moves. The wallet was debited when the transfer was
            # requested - that is what "held" means - so the money leaving and
            # the row finishing are the same event seen from two sides.
            row.mark_successful()
            return None

        if outcome.event is ProviderEvent.TRANSFER_FAILED:
            if row.type not in _TRANSFER_TYPES:
                return (
                    SettlementOutcome.WRONG_KIND,
                    f"a transfer cannot settle a {row.type.value}",
                )
            if row.status is not TransactionStatus.PENDING:
                return self._not_pending(row)
            # The other side of the same coin: the hold goes back, because the
            # movement it was held for never happened.
            wallet.release_hold(row.amount)
            row.mark_failed()
            return None

        # TRANSFER_REVERSED, and the difference from FAILED is the status it
        # expects. Money that was returned by the bank after landing belongs to a
        # row that had succeeded; a row that never left is a *failed* transfer,
        # and reversing it would be recording an undo of something that did not
        # happen.
        if row.type not in _TRANSFER_TYPES:
            return (
                SettlementOutcome.WRONG_KIND,
                f"a reversal cannot settle a {row.type.value}",
            )
        if row.status is not TransactionStatus.SUCCESSFUL:
            return (
                SettlementOutcome.ALREADY_SETTLED,
                f"only a settled transfer can be reversed, and this one is "
                f"{row.status.value}",
            )
        # The row's amount, not the event's: what comes back is the hold that was
        # taken, and ``reverse`` below is what makes the row agree with that.
        wallet.release_hold(row.amount)
        row.reverse()
        return None

    def _not_pending(self, row: Transaction) -> tuple[SettlementOutcome, str]:
        """The refusal for an event about a row that is already finished.

        One helper rather than three copies of the same sentence, because the
        three call sites want to say exactly the same thing and the only
        difference is which movement they were about - which ``row`` already
        carries.
        """
        return (
            SettlementOutcome.ALREADY_SETTLED,
            f"this {row.type.value} is already {row.status.value}",
        )

    @staticmethod
    def _movement_for(event: ProviderEvent) -> SettlementOutcome:
        """The outcome name for an event that was applied.

        A dictionary rather than a chain, so that a fifth ``ProviderEvent`` is a
        ``KeyError`` at the first webhook rather than a branch that falls through
        to whichever member happened to be last. The lookup cannot fail today:
        ``_apply`` above handles exactly these four and returns a refusal for
        anything it does not recognise.
        """
        return _MOVEMENTS[event]

    def _refused(
        self,
        uow: UnitOfWork,
        outcome: ProviderOutcome,
        settlement: SettlementOutcome,
        detail: str,
    ) -> SettledPayment:
        """Close the unit writing nothing, and report why.

        The rollback is not a formality. Every refusal below happens *after* the
        unit has read a row, and a unit left open holds a SQLite read transaction
        that the next writer on this database has to wait for. Reading and
        discarding is the common case for a webhook - a replay, an event about
        something this deployment has never heard of - so leaking a transaction
        there would be leaking one on the highest-traffic path there is.
        """
        uow.rollback()
        return SettledPayment(
            outcome=settlement, reference=outcome.reference, detail=detail
        )

    # --- the receipt --------------------------------------------------------

    def _announce(
        self, uow: UnitOfWork, wallet: Wallet, row: Transaction
    ) -> None:
        """Queue the receipt for a movement that has now actually happened.

        **This is decision 101 being kept.** A pending movement sends no receipt,
        because every sentence a receipt writes is past tense and specific -
        ``WalletService._announce`` skips a PENDING row and its docstring
        promises that "the receipt arrives in the phase that settles these
        movements". This is that phase, and the promise was made about exactly
        the row now in hand.

        **Silent unless the row is SUCCESSFUL**, which is the same guard for a
        different reason: a failed transfer did not pay anybody, and
        ``compose.wallet_movement`` would happily write "5000.00 NGN was paid to
        Chinedu Okafor" for it. A reversal is silent for the sharper version of
        that reason - the money *was* paid and then came back, so what the
        receipt would say is true and no longer the whole truth, which is worse
        than saying nothing. Both are recorded in the README as an open item: the
        owner is not told their payout failed, and this is where that would be
        fixed.

        The kind comes from ``compose`` rather than from a table here, so that
        "which ledger row earns which receipt" has one answer in one file - the
        file that writes the words.
        """
        if row.status is not TransactionStatus.SUCCESSFUL:
            return

        kind = compose.receipt_kind_for_transaction_type(row.type)
        if kind is None:
            return

        notification = compose.wallet_movement(kind, wallet, row, self._recipient)
        if notification is not None:
            uow.notifications.enqueue(notification)


#: One entry per ``ProviderEvent``, and the type checker would not catch a
#: missing one - ``_apply`` is what guarantees every member is handled, and this
#: mapping's keys are read only after it has returned. Kept beside that method
#: rather than in ``SettlementOutcome``, because it is the translation from the
#: provider's vocabulary into ours and that translation belongs where the
#: provider is being read.
_MOVEMENTS = {
    ProviderEvent.CHARGE_SUCCEEDED: SettlementOutcome.DEPOSIT_CREDITED,
    ProviderEvent.TRANSFER_SUCCEEDED: SettlementOutcome.TRANSFER_SETTLED,
    ProviderEvent.TRANSFER_FAILED: SettlementOutcome.HOLD_RELEASED,
    ProviderEvent.TRANSFER_REVERSED: SettlementOutcome.PAYMENT_REVERSED,
}
