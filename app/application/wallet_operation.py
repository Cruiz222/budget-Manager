from abc import ABC, abstractmethod
import uuid
from collections.abc import Callable
from datetime import datetime

from app.domain.money.destination import Destination
from app.domain.money.exception import (
    InvalidAmountError,
    MoneyError,
    ReferenceAlreadyRefusedError,
)
from app.domain.money.money import Money
from app.domain.money.transaction import Transaction
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.transactionType import TransactionType
from app.domain.money.wallet import Wallet
from app.domain.repositories.transaction_repository import TransactionRepository


class WalletOperation(ABC):
    """Shared skeleton for a single-wallet money operation.

    Deposit, withdrawal, lock, release and payout all run the same guarded flow,
    so the flow lives here once instead of being copy-pasted:

        validate amount
        -> deduplicate by internal_reference
        -> record the attempt as PENDING
        -> apply through the wallet (it decides)
        -> record SUCCESSFUL, or FAILED on rejection and re-raise

    Concrete use cases supply only the things that differ:
      - transaction_type:  the TransactionType recorded on the ledger
      - _apply():          the wallet method that performs the operation
      - settles_immediately: whether the last step above happens at all

    This is the Template Method pattern: the base class owns the algorithm and
    lets subclasses fill in the variable steps.

    **The last step is conditional, and that is the newest thing here.** An
    operation whose far end is outside this system cannot honestly report that it
    succeeded, because nothing here has contacted that far end. Those operations
    stop at PENDING with the wallet already debited - the money is *held* - and
    the row they leave is a claim about an intention rather than about a fact.
    See ``settles_immediately``.

    ``destination`` is passed straight through to the Transaction, which is
    where the rule about it lives: a payout must carry one, every other type
    must not. The base does not police it - it only delivers it.

    ``fund_id`` is the same shape of pass-through, for the same reason. A
    pot-scoped operation records which pot it moved money through, and the base
    has no opinion about when that must be set: the ledger's rules about it are
    the Transaction's, and there is currently only one - that a fund id, if
    present, is a UUID. Note the asymmetry with ``destination``, which *does*
    have a rule tying it to the transaction type: a payout has no single pot in
    its *shape*, only in its facts. One plan's payout names a pot and another's
    does not, and both are PAYOUTs - so "PAYOUT implies a fund" would be a rule
    the code cannot keep.
    """

    #: Transaction type recorded for this operation (set by each subclass).
    transaction_type: TransactionType

    #: Whether this operation can call itself finished, or must wait for an
    #: outside party to confirm it (set by each subclass; ``True`` for most).
    #:
    #: ``True`` means the movement completes here: both balances it touches are
    #: inside this system, so the instant the wallet has moved the money the
    #: operation is a fact, and the ledger says SUCCESSFUL.
    #:
    #: ``False`` means the movement crosses the system's edge. A withdrawal and a
    #: payout both end at a bank account nobody here has contacted, so the most
    #: this code can honestly record is that it *intends* to send the money and
    #: has taken it out of the owner's reach in the meantime. The transaction
    #: stays PENDING and the wallet is still debited - the funds are held, not
    #: spent - and a later phase settles it against the provider's own word.
    #:
    #: **Why the debit happens anyway, rather than at settlement.** Reserving the
    #: money at intent time is what stops it being spent twice: two payout
    #: requests each checked against an untouched balance would both pass, and
    #: the wallet would overdraw on money it had promised away. "Available" has
    #: to keep meaning available, and this is the only reading of it that does.
    #:
    #: The default is ``True`` deliberately, so the safe-for-this-phase answer is
    #: what a new operation inherits. A new operation that crosses the edge has
    #: to say so; one that forgets is immediately wrong in the loud direction,
    #: leaving a SUCCESSFUL row where a caller can see it, rather than silently
    #: withholding money from a user who thinks it moved.
    settles_immediately: bool = True

    #: An extra refusal to apply to the amount, before the wallet is asked to move
    #: it. ``None`` means no ceiling faces this operation.
    #:
    #: **Set by whoever knows the limits, not by the operation.** An operation is
    #: built with the wallet in hand and knows nothing about the person who owns
    #: it, which is exactly the rule the tier checks are: a ceiling is a fact
    #: about the *account* (see ``app.domain.identity.tier``), and the one layer
    #: that can load a wallet and a profile at once is the application service
    #: that builds this object. So ``WalletService._run`` attaches one and this
    #: class only calls it. An attribute here rather than a constructor argument
    #: because the nine concrete operations have nine different constructors and
    #: not one of them has anything to say about it - a parameter would be nine
    #: signatures forwarded for a fact none of them knows.
    #:
    #: **It runs inside ``execute``'s ``try``, and that placement is the whole of
    #: its value.** Invoked between the PENDING row and ``_apply``, a breach it
    #: raises is caught by the ``except MoneyError`` below, recorded as FAILED and
    #: re-raised - so a limit refusal reaches the ledger like every other refusal,
    #: which is ``TierLimitExceededError``'s argument for sitting under
    #: ``MoneyError``. Checked one layer up, before ``execute`` was called, the
    #: refusal would leave no row at all and nobody afterwards could say how many
    #: movements a cap had turned away.
    #:
    #: **It runs before ``_apply`` rather than inside it**, so the wallet has not
    #: been touched and a refusal is not a half-made movement. What the guard
    #: reads is therefore the wallet *before* this movement - which is what both
    #: ceilings are stated against: a balance cap is about what would be held,
    #: and a daily total is about what has already gone.
    guard: Callable[[Money], None] | None = None

    #: The moment this movement belongs to, stamped onto the row it writes.
    #:
    #: **A movement judged in a limit-day has to be recorded in that limit-day.**
    #: The daily cap is summed from the ledger's ``created_at`` (see
    #: ``TransactionRepository.outflow_total_between``), so a row stamped by the
    #: wall clock while its ceiling was read from the caller's own moment leaves
    #: the two frames disagreeing - and the disagreement is *silent*, because a
    #: total that misses a row reads as a smaller total rather than as an error.
    #: The cap would still be enforced; it would just be enforced against the
    #: wrong day, and every movement judged in a day that is not the clock's would
    #: see an empty one.
    #:
    #: Set by whoever read the limit-day, which is the same layer that sets
    #: ``guard`` and for the same reason: only the application service knows which
    #: day a movement is being judged in. ``None`` means no moment was supplied
    #: and ``Transaction`` falls back to the clock, which is all a deposit or an
    #: internal move needs - neither is read by a day-bounded query. That is the
    #: same asymmetry ``WalletService._ceiling`` states, arriving at the row
    #: instead of the verdict: only a movement *out* is judged against a day, so
    #: only a movement out is stamped against one.
    #:
    #: This is not a new convention, it is the existing one applied here. Every
    #: other use case in this codebase that is handed an ``as_of`` stamps what it
    #: writes with it, because that is what makes a replayed tick compose the same
    #: rows (see ``NotifyUpcomingRuns``) and what lets a catch-up run record the
    #: moment a plan was *due* rather than the moment the scheduler noticed. The
    #: template was the outlier, and the daily cap is the first thing to depend on
    #: the rule it was breaking.
    now: datetime | None = None

    def __init__(
        self,
        wallet: Wallet,
        transaction_repository: TransactionRepository,
        fund_id: uuid.UUID | None = None,
    ):
        self.wallet = wallet
        self.transaction_repository = transaction_repository
        self.fund_id = fund_id

    def execute(
        self,
        amount: Money,
        internal_reference: str,
        destination: Destination | None = None,
    ) -> Transaction:
        # 1. Command validation happens before any record exists.
        self._validate_amount(amount)

        # 2. Idempotency: a processed internal_reference must not run twice.
        existing = self.transaction_repository.get_by_internal_reference(
            internal_reference
        )
        if existing is not None:
            # A FAILED row is a *refusal*, not an outcome, and the lookup above
            # answers with a row whatever its status - so returning it would
            # report a movement that never happened as one that did, and on an
            # announced operation would compose a receipt for it. So the retry
            # is refused instead. See ``ReferenceAlreadyRefusedError`` for why
            # the row cannot be re-opened and why a fresh key is the way to ask
            # again.
            #
            # PENDING and SUCCESSFUL are the real dedupe hit: the movement is
            # under way or already done, the row *is* the outcome, and a client
            # retrying after a lost response gets the same answer it would have
            # got the first time - which is what makes this door retryable.
            if existing.status is TransactionStatus.FAILED:
                raise ReferenceAlreadyRefusedError(
                    f"the reference {internal_reference!r} already names a "
                    f"refused {self.transaction_type.value}; a retry needs a "
                    f"fresh reference"
                )
            return existing

        # 3. Record intent BEFORE touching the wallet. A payout reaches here
        #    with a destination; every other type reaches here without one. The
        #    Transaction rejects the wrong combination before anything is saved.
        transaction = Transaction(
            wallet_id=self.wallet.wallet_id,
            type=self.transaction_type,
            amount=amount,
            internal_reference=internal_reference,
            destination=destination,
            fund_id=self.fund_id,
            # The moment this movement is being judged in, when one was supplied
            # - see ``now``. ``None`` here is not "no moment" but "the caller had
            # no day to give", and ``Transaction`` reads it as the clock.
            created_at=self.now,
        )
        self.transaction_repository.save(transaction)

        try:
            # 3b. Any ceiling that faces this movement, asked after the row above
            #     exists so that a refusal is recorded rather than merely raised.
            #     See ``guard``.
            if self.guard is not None:
                self.guard(amount)

            # 4. The wallet decides, raising before mutating on rejection.
            self._apply(amount)
        except MoneyError:
            # 4a. Rejected - record FAILED for the audit trail, then surface
            #     the original error.
            transaction.mark_failed()
            self.transaction_repository.save(transaction)
            raise

        # 4b. The operation succeeded. For most operations that is the end of it
        #     and the row becomes SUCCESSFUL. For one that crosses the system's
        #     edge it is not, and the PENDING row written at step 3 is already the
        #     finished record - see ``settles_immediately``. Note there is nothing
        #     left to do in that case: the wallet was debited by ``_apply`` above,
        #     so the row and the balance already agree with each other.
        if self.settles_immediately:
            transaction.mark_successful()
            self.transaction_repository.save(transaction)

        return transaction

    def _validate_amount(self, amount: Money) -> None:
        """Reject amounts no operation can record. Most operations treat zero
        and negative the same; WithdrawMoney overrides this for its finer
        zero/negative error split."""
        if not isinstance(amount, Money):
            raise InvalidAmountError("amount must be a Money")
        if amount.amount <= 0:
            raise InvalidAmountError("amount must be greater than zero")

    @abstractmethod
    def _apply(self, amount: Money) -> None:
        """Perform the wallet operation. Must raise a MoneyError on rejection
        before mutating any balance."""
