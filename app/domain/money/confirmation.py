from dataclasses import dataclass
from datetime import datetime, timedelta
import uuid

from .confirmationKind import ConfirmationKind
from .confirmationStatus import ConfirmationStatus
from .destination import Destination
from .exception import (
    InvalidConfirmationAmountError,
    InvalidConfirmationCreatedAtError,
    InvalidConfirmationExpiresAtError,
    InvalidConfirmationIDError,
    InvalidConfirmationKindError,
    InvalidConfirmationReferenceError,
    InvalidConfirmationStatusError,
    InvalidConfirmationTransactionIDError,
    InvalidConfirmationUserIDError,
    InvalidConfirmationWalletIDError,
    InvalidConfirmationWindowError,
    MissingConfirmationDestinationError,
    UnexpectedConfirmationAmountError,
    UnexpectedConfirmationDestinationError,
    UnexpectedConfirmationFundError,
)
from .money import Money

#: How long a confirmation stays usable, counted from the moment it was made.
#:
#: Fifteen minutes: long enough that somebody who opened the confirmation, walked
#: away mid-prompt and came back can still answer it, and short enough that a
#: request nobody remembers is not still live tomorrow. The window is what stops
#: a confirmation sitting in a database becoming a standing permission - which is
#: the failure mode that would make this feature *worse* than no confirmation at
#: all, because a stale authorisation is a request that has stopped meaning what
#: the person meant when they made it.
#:
#: A module constant beside the aggregate, like ``SESSION_LIFETIME``
#: (app/domain/identity/session.py:26) and for the same reason: it is a number a
#: caller can pass a different one of, so tests can construct an expired
#: confirmation without waiting or patching a clock.
CONFIRMATION_LIFETIME = timedelta(minutes=15)


def _expects(kind: ConfirmationKind) -> tuple[bool, bool, bool]:
    """Which of amount / destination / fund_name this kind carries.

    One table read by one validator, rather than three separate ``if`` chains.
    The pairing is a property of the kind, so it is written once beside the enum
    that defines the kinds - and ``_check_pairing`` below is the only reader,
    which is what keeps the table and the rule from drifting apart.

    The three columns are *required*, and for ``fund_name`` the third column
    means something weaker - see there.
    """
    return {
        # amount, destination, fund_name
        ConfirmationKind.WITHDRAWAL: (True, False, False),
        ConfirmationKind.PAYOUT_FROM_AVAILABLE: (True, True, False),
        # A pot may be named or not: naming none is the legacy pooled draw, which
        # is a real request rather than a missing field. See ``Wallet.funds``.
        ConfirmationKind.PAYOUT_FROM_LOCKED: (True, True, True),
        ConfirmationKind.CLOSE: (False, False, False),
    }[kind]


#: How each kind is named in a refusal, so the message says which request it is
#: about rather than only what is wrong with it.
_LABELS = {
    ConfirmationKind.WITHDRAWAL: "withdrawal",
    ConfirmationKind.PAYOUT_FROM_AVAILABLE: "payout from available",
    ConfirmationKind.PAYOUT_FROM_LOCKED: "payout from locked",
    ConfirmationKind.CLOSE: "close",
}


@dataclass
class Confirmation:
    """A request to move money out, recorded and waiting to be answered.

    **What this is, in one line, because the word it is not is the important
    part:** a confirmation is a request *nothing has been done about*. The README
    already uses "the pending intent" for a PENDING **transaction** - a movement
    that has happened, whose wallet is already debited, and which cannot yet be
    called finished because the far end has not confirmed it. That is the
    opposite state. Two opposite states must not share a word, so this one is
    named ``Confirmation`` and its statuses (``AWAITING`` / ``CONFIRMED`` /
    ``EXPIRED``) never overlap with ``TransactionStatus``.

    **The field pairing is enforced here, and it follows ``Instruction``.** An
    enum plus nullable fields, with ``__post_init__`` refusing a combination that
    means nothing - exactly the shape ``Instruction`` uses for
    action-versus-destination (app/domain/planning/instruction.py:34-72), and
    written out again rather than shared for the reason recorded there: coupling
    two aggregates to deduplicate a few lines trades a little repetition for a
    much harder-to-move boundary. What it buys is that a malformed row cannot be
    built *or loaded*: the repository constructs one of these on the way in, so a
    hand-edited database row that carried an amount on a CLOSE would fail loudly
    at load rather than quietly authorising nothing.

    **It is mutable, like every other aggregate here** (``Wallet``,
    ``Transaction``, ``Fund``) and unlike the value objects (``Money``,
    ``Destination``, ``Instruction``). Two things about it change over its life -
    its status, once, and the transaction it produced - so freezing it would mean
    every write produced a second object and the old one stayed a valid-looking
    representation of a request that had already been spent.

    **Expiry is checked, not enforced by the store**, exactly as
    ``Session`` reads it (app/domain/identity/session.py:87-91). ``is_expired``
    answers a question and takes the moment to answer it against, so the one
    interesting case - a confirmation expiring exactly now - is reachable by a
    test rather than only by waiting.
    """

    confirmation_id: uuid.UUID
    user_id: uuid.UUID
    wallet_id: uuid.UUID
    kind: ConfirmationKind
    internal_reference: str
    status: ConfirmationStatus
    created_at: datetime
    expires_at: datetime
    amount: Money | None = None
    destination: Destination | None = None
    fund_name: str | None = None
    transaction_id: uuid.UUID | None = None

    def __post_init__(self):
        if not isinstance(self.confirmation_id, uuid.UUID):
            raise InvalidConfirmationIDError("invalid confirmation id")

        if not isinstance(self.user_id, uuid.UUID):
            raise InvalidConfirmationUserIDError("invalid user id")

        if not isinstance(self.wallet_id, uuid.UUID):
            raise InvalidConfirmationWalletIDError("invalid wallet id")

        if not isinstance(self.kind, ConfirmationKind):
            raise InvalidConfirmationKindError(
                f"kind must be a ConfirmationKind, got {type(self.kind).__name__}"
            )

        if not isinstance(self.status, ConfirmationStatus):
            raise InvalidConfirmationStatusError(
                f"status must be a ConfirmationStatus, got {type(self.status).__name__}"
            )

        # Empty is refused for the reason an empty transaction reference is: a
        # confirmation with no key is one nothing can find again, wearing the
        # shape of a request that works. It would present as a confirmation the
        # client cannot confirm.
        if not isinstance(self.internal_reference, str) or not self.internal_reference:
            raise InvalidConfirmationReferenceError(
                "internal reference must be a non-empty string"
            )

        # ``datetime`` and not ``date``, the same narrow check ``Session`` makes
        # and for the same reason: bare dates would be promoted to midnight, so
        # every confirmation made in the afternoon would appear to expire at
        # breakfast time.
        if not isinstance(self.created_at, datetime):
            raise InvalidConfirmationCreatedAtError("invalid created at")

        if not isinstance(self.expires_at, datetime):
            raise InvalidConfirmationExpiresAtError("invalid expires at")

        # A confirmation that expires before it was made is born dead, and it is
        # a bug rather than a state: nothing constructs one deliberately. Left
        # alone it would present as a prompt the user is asked and can never
        # answer. ``<=`` rather than ``<``, because a zero-length window is
        # equally unusable.
        if self.expires_at <= self.created_at:
            raise InvalidConfirmationWindowError(
                "a confirmation must expire after the moment it was made"
            )

        if self.amount is not None and not isinstance(self.amount, Money):
            raise InvalidConfirmationAmountError(
                f"amount must be a Money or None, got {type(self.amount).__name__}"
            )

        if self.destination is not None and not isinstance(
            self.destination, Destination
        ):
            raise UnexpectedConfirmationDestinationError(
                f"destination must be a Destination or None, "
                f"got {type(self.destination).__name__}"
            )

        if self.fund_name is not None and not isinstance(self.fund_name, str):
            raise UnexpectedConfirmationFundError(
                f"fund name must be a string or None, "
                f"got {type(self.fund_name).__name__}"
            )

        if self.transaction_id is not None and not isinstance(
            self.transaction_id, uuid.UUID
        ):
            raise InvalidConfirmationTransactionIDError(
                f"transaction id must be a UUID or None, "
                f"got {type(self.transaction_id).__name__}"
            )

        self._check_pairing()

    def _check_pairing(self) -> None:
        """Refuse a kind carrying a field it does not use, or missing one it does.

        Both directions, and the second is the one worth arguing for. Refusing a
        *surplus* field is obvious - a CLOSE with an amount describes a movement
        that kind never makes, and a reader would reasonably conclude money is
        about to leave. Refusing a *missing* field matters for a duller reason:
        the field the dispatch reads would be ``None`` at the moment the money
        moved, so the failure would surface as an ``AttributeError`` deep in an
        operation rather than as a refusal at the door, and it would surface
        *after* the confirmation had been spent.
        """
        label = _LABELS[self.kind]
        wants_amount, wants_destination, allows_fund = _expects(self.kind)

        if wants_amount and self.amount is None:
            raise InvalidConfirmationAmountError(
                f"a {label} confirmation must carry an amount"
            )

        if not wants_amount and self.amount is not None:
            raise UnexpectedConfirmationAmountError(
                f"a {label} confirmation must not carry an amount"
            )

        # Only reached when an amount is present, which the two branches above
        # have already settled - so this needs no ``is not None`` of its own.
        if wants_amount and self.amount.amount <= 0:
            raise InvalidConfirmationAmountError("amount must be greater than zero")

        if wants_destination and self.destination is None:
            raise MissingConfirmationDestinationError(
                f"a {label} confirmation must name a destination"
            )

        if not wants_destination and self.destination is not None:
            raise UnexpectedConfirmationDestinationError(
                f"a {label} confirmation must not name a destination"
            )

        # Required-ness is deliberately absent for the pot: naming no pot is the
        # legacy pooled draw rather than an omission, so the only question here is
        # whether this kind may name one at all.
        if self.fund_name is not None and not allows_fund:
            raise UnexpectedConfirmationFundError(
                f"a {label} confirmation must not name a pot"
            )

    # --- what the request is, once it has been answered ----------------------

    def is_expired(self, as_of: datetime) -> bool:
        """Whether this confirmation's window has closed by ``as_of``.

        ``>=`` and not ``>``, matching ``Session.is_expired``: a confirmation is
        expired *at* the instant it expires, not a moment later. The alternative
        is a request that stays answerable for an instant its owner was never
        given.
        """
        return as_of >= self.expires_at

    def status_as_of(self, as_of: datetime) -> ConfirmationStatus:
        """What to report: an ``AWAITING`` request past its window reads EXPIRED.

        **Derived rather than stored, and that is the whole design.** The
        alternative is to write ``EXPIRED`` over the row the moment something
        notices - and the only thing that notices is a reader, which would make a
        ``GET`` a write. The README already refuses that shape one aggregate over:
        decision 49 keeps sessions absolutely expiring rather than sliding partly
        *because* re-stamping on every request turns a pure read into a write.

        Storing it would also be a second record of a fact ``expires_at`` already
        holds, free to disagree with it - the same objection ``Wallet`` makes to
        a stored ``locked_balance``. Here the disagreement would be worse than
        redundant: a row saying ``EXPIRED`` with an ``expires_at`` in the future
        is a request refused for no reason, and one saying ``AWAITING`` with a
        window in the past is a prompt the user is told to answer and cannot.

        A ``CONFIRMED`` request stays ``CONFIRMED`` for ever, however long ago it
        was spent. Expiry is about whether a request may still be answered, and a
        request that was answered is not un-answered by the clock.
        """
        if self.status is ConfirmationStatus.AWAITING and self.is_expired(as_of):
            return ConfirmationStatus.EXPIRED
        return self.status

    def record(self, transaction_id: uuid.UUID) -> None:
        """Attach the ledger row this confirmation produced.

        Audit, not control flow: nothing decides anything by reading this field.
        It exists so that "which payment did this request become?" is answerable
        from the request, rather than only from the reference - which is a key
        the client chose and may have reused in a later request's shape.

        Guarded on the status, because the ordering it depends on is the one
        thing here that could go wrong silently. Recording before spending would
        leave a confirmation that names a transaction it never authorised, and
        the mistake would be invisible: the row would read exactly like a
        correctly spent one.
        """
        if not isinstance(transaction_id, uuid.UUID):
            raise InvalidConfirmationTransactionIDError(
                f"transaction id must be a UUID, got {type(transaction_id).__name__}"
            )

        if self.status is not ConfirmationStatus.CONFIRMED:
            raise InvalidConfirmationStatusError(
                "only a confirmed request can record a transaction"
            )

        if self.transaction_id is not None:
            raise InvalidConfirmationTransactionIDError(
                "this confirmation already records a transaction"
            )

        self.transaction_id = transaction_id

    # --- construction --------------------------------------------------------

    @classmethod
    def requested(
        cls,
        *,
        user_id: uuid.UUID,
        wallet_id: uuid.UUID,
        kind: ConfirmationKind,
        internal_reference: str,
        now: datetime,
        amount: Money | None = None,
        destination: Destination | None = None,
        fund_name: str | None = None,
        lifetime: timedelta = CONFIRMATION_LIFETIME,
    ) -> "Confirmation":
        """Record a request, starting its window at ``now``.

        A factory rather than something the caller assembles, for the reason
        ``Session.issue`` is one: the id, the made-at moment and the expires-at
        moment must be produced together. A caller building this by hand would
        have to remember to derive the second from the first, and the way to get
        that wrong - passing a moment in one place and not the other - produces a
        window that is nothing like ``CONFIRMATION_LIFETIME`` while looking
        exactly like one that is.

        ``now`` is passed in rather than read, so a test can age a confirmation
        without a clock patch and so one request cannot have two ideas of when it
        was made.
        """
        return cls(
            confirmation_id=uuid.uuid4(),
            user_id=user_id,
            wallet_id=wallet_id,
            kind=kind,
            internal_reference=internal_reference,
            status=ConfirmationStatus.AWAITING,
            created_at=now,
            expires_at=now + lifetime,
            amount=amount,
            destination=destination,
            fund_name=fund_name,
        )

    def __str__(self) -> str:
        return f"{self.kind.value} on wallet {self.wallet_id} ({self.status.value})"
