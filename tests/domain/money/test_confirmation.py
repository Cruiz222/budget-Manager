"""``Confirmation``: a request to move money out, recorded and waiting.

The heart of this phase is one sentence, and the file is arranged around it:
**a confirmation is a request nothing has been done about.** Everything here is
a consequence of taking that seriously.

  - **The kind decides the fields.** An enum plus three nullable columns is a
    shape that can say five things that mean nothing - a close with an amount, a
    withdrawal naming a bank account, a payout from available naming a pot - so
    the pairing is checked in the constructor rather than at each use. The
    four-row table is pinned one test per row, in both directions.
  - **The window is checked against a moment, not against a clock.**
    ``is_expired`` takes ``as_of``, so the boundary - the instant a request
    closes - is reachable by a test rather than only by waiting fifteen minutes.
  - **``EXPIRED`` is never stored.** ``status_as_of`` derives it, which is why
    reading a stale request is not a write, and why a ``CONFIRMED`` request is
    not un-answered by the clock.

What this file deliberately does *not* test is the spending of a confirmation.
That happens in the store, in one statement, and ``test_confirmation_store.py``
is where it is pinned - because the decision and the write being the same
operation is a property of the UPDATE rather than of this aggregate.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.money.confirmation import CONFIRMATION_LIFETIME, Confirmation
from app.domain.money.confirmationKind import ConfirmationKind
from app.domain.money.confirmationStatus import ConfirmationStatus
from app.domain.money.currency import Currency
from app.domain.money.destination import Destination
from app.domain.money.destinationKind import DestinationKind
from app.domain.money.exception import (
    ConfirmationNotFoundError,
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
from app.domain.money.money import Money

NGN = Currency.NGN

#: The moment every window below is measured from. A literal rather than
#: ``datetime.now()``, for the reason ``WalletService`` takes ``now`` as an
#: argument: a test that read the clock could not assert an exact ``expires_at``,
#: and one that could not do that could not tell a fifteen-minute window from a
#: fifteen-day one.
MADE_AT = datetime(2026, 9, 12, 10, 0, 0)

PAYEE = Destination(
    DestinationKind.BANK_ACCOUNT,
    "0123456789",
    "Chinedu Okafor",
    {"bank_code": "058"},
)


def ngn(amount: str) -> Money:
    return Money(Decimal(amount), NGN)


def a_confirmation(**overrides) -> Confirmation:
    """A withdrawal request, which is the plainest kind there is.

    Built through ``requested`` rather than by hand so that every test which
    overrides one field is still exercising the factory - the ids, the made-at
    moment and the expiry all arrive the way they arrive in production, and a
    test that broke one of them deliberately is the only place they are hand-set.
    """
    fields = {
        "user_id": uuid4(),
        "wallet_id": uuid4(),
        "kind": ConfirmationKind.WITHDRAWAL,
        "internal_reference": "ref-1",
        "now": MADE_AT,
        "amount": ngn("500.00"),
    }
    fields.update(overrides)
    return Confirmation.requested(**fields)


class TestRequesting:
    def test_it_starts_awaiting_with_a_window_counted_from_now(self):
        """The one thing the factory exists to get right.

        ``created_at`` and ``expires_at`` must be produced together, because a
        caller passing one and deriving the other by hand is how a window ends up
        nothing like ``CONFIRMATION_LIFETIME`` while looking exactly like one
        that is.
        """
        confirmation = a_confirmation()

        assert confirmation.status is ConfirmationStatus.AWAITING
        assert confirmation.created_at == MADE_AT
        assert confirmation.expires_at == MADE_AT + CONFIRMATION_LIFETIME
        assert confirmation.transaction_id is None

    def test_the_lifetime_is_fifteen_minutes(self):
        """Pinned as a number, not as "some positive timedelta".

        The value is a judgement - long enough to walk away mid-prompt and come
        back, short enough that a forgotten request is not still live tomorrow -
        and a judgement that is only asserted to be positive is one that can be
        changed to a week without anything noticing. The failure that guards
        against is the one that would make this feature *worse* than no
        confirmation: a stale authorisation is a request that has stopped meaning
        what the person meant when they made it.
        """
        assert CONFIRMATION_LIFETIME == timedelta(minutes=15)

    def test_a_lifetime_can_be_passed(self):
        """Which is what makes the boundary testable without a clock patch."""
        confirmation = a_confirmation(lifetime=timedelta(seconds=1))

        assert confirmation.expires_at == MADE_AT + timedelta(seconds=1)

    def test_each_request_gets_its_own_id(self):
        """Two requests built from identical fields are still two requests.

        Nothing in the API relies on this - the ``UNIQUE`` key is the reference,
        not the id - but a ``uuid4`` is what makes a confirmation id safe to be
        the *only* thing needed to answer one, and a fixed or derived id would
        quietly remove that.
        """
        first, second = a_confirmation(), a_confirmation()

        assert first.confirmation_id != second.confirmation_id


class TestTheKindDecidesTheFields:
    """The four rows of the table, and then the five ways to get one wrong.

    Both directions matter and the second is the one worth arguing for. A
    *surplus* field describes a movement the kind never makes, so a reader would
    reasonably conclude money is about to leave in a way it is not. A *missing*
    one is duller and worse: the field the dispatch reads would be ``None`` at
    the moment the money moved, so the failure would surface as an
    ``AttributeError`` deep in an operation - and after the request had been
    spent.
    """

    def test_a_withdrawal_carries_an_amount_and_nothing_else(self):
        confirmation = a_confirmation(
            kind=ConfirmationKind.WITHDRAWAL, amount=ngn("500.00")
        )

        assert confirmation.amount == ngn("500.00")
        assert confirmation.destination is None
        assert confirmation.fund_name is None

    def test_a_payout_from_available_carries_an_amount_and_a_destination(self):
        confirmation = a_confirmation(
            kind=ConfirmationKind.PAYOUT_FROM_AVAILABLE,
            amount=ngn("500.00"),
            destination=PAYEE,
        )

        assert confirmation.destination is PAYEE
        assert confirmation.fund_name is None

    def test_a_payout_from_locked_may_name_a_pot_or_not(self):
        """The one column that is allowed rather than required.

        Naming no pot is the legacy pooled draw - money taken from matured pots
        oldest first - so it is a real request rather than a field somebody
        forgot. That is why ``_expects`` gives this kind a weaker third column
        than the other two, and why both spellings are asserted here.
        """
        named = a_confirmation(
            kind=ConfirmationKind.PAYOUT_FROM_LOCKED,
            amount=ngn("500.00"),
            destination=PAYEE,
            fund_name="Savings",
        )
        pooled = a_confirmation(
            kind=ConfirmationKind.PAYOUT_FROM_LOCKED,
            amount=ngn("500.00"),
            destination=PAYEE,
        )

        assert named.fund_name == "Savings"
        assert pooled.fund_name is None

    def test_a_close_carries_nothing(self):
        confirmation = a_confirmation(
            kind=ConfirmationKind.CLOSE, amount=None
        )

        assert confirmation.amount is None
        assert confirmation.destination is None
        assert confirmation.fund_name is None

    def test_a_withdrawal_with_no_amount_is_refused(self):
        with pytest.raises(InvalidConfirmationAmountError):
            a_confirmation(kind=ConfirmationKind.WITHDRAWAL, amount=None)

    def test_a_close_with_an_amount_is_refused(self):
        with pytest.raises(UnexpectedConfirmationAmountError):
            a_confirmation(kind=ConfirmationKind.CLOSE, amount=ngn("500.00"))

    def test_a_payout_with_no_destination_is_refused(self):
        with pytest.raises(MissingConfirmationDestinationError):
            a_confirmation(
                kind=ConfirmationKind.PAYOUT_FROM_AVAILABLE, amount=ngn("500.00")
            )

    def test_a_withdrawal_with_a_destination_is_refused(self):
        with pytest.raises(UnexpectedConfirmationDestinationError):
            a_confirmation(
                kind=ConfirmationKind.WITHDRAWAL,
                amount=ngn("500.00"),
                destination=PAYEE,
            )

    def test_a_withdrawal_naming_a_pot_is_refused(self):
        """A pot belongs to a payout, and only a locked one.

        Refused for a withdrawal here, and for the two payout kinds that cannot
        draw on a pot in ``test_confirmation_store.py``'s siblings - the reason
        being the duller one above: a ``fund_name`` on a withdrawal would be
        silently ignored by the operation that ran it, and a caller who named a
        pot and was charged from the available balance instead was told nothing.
        """
        with pytest.raises(UnexpectedConfirmationFundError):
            a_confirmation(
                kind=ConfirmationKind.WITHDRAWAL,
                amount=ngn("500.00"),
                fund_name="Savings",
            )

    def test_an_amount_of_zero_is_refused(self):
        """A movement of nothing is not a movement, and a prompt asking somebody
        to approve zero is a prompt that teaches them not to read prompts.
        """
        with pytest.raises(InvalidConfirmationAmountError):
            a_confirmation(amount=ngn("0.00"))

    def test_a_negative_amount_is_refused(self):
        with pytest.raises(InvalidConfirmationAmountError):
            a_confirmation(amount=ngn("-1.00"))


class TestTheConstructionGuards:
    """The type checks, one test each, because each one is a hand-edited row.

    These are not exercises in defensive programming for its own sake. The
    repository builds a ``Confirmation`` on the way *in* from the store, so a row
    somebody edited by hand reaches this constructor - and a ``kind`` stored as
    the string ``"withdrawal"`` rather than the enum name would otherwise be
    carried into the dispatch as a value nothing matches.
    """

    @pytest.mark.parametrize(
        "field, value, error",
        [
            ("confirmation_id", "not-a-uuid", InvalidConfirmationIDError),
            ("user_id", "not-a-uuid", InvalidConfirmationUserIDError),
            ("wallet_id", "not-a-uuid", InvalidConfirmationWalletIDError),
            ("kind", "withdrawal", InvalidConfirmationKindError),
            ("status", "awaiting", InvalidConfirmationStatusError),
            ("internal_reference", "", InvalidConfirmationReferenceError),
            ("created_at", "2026-09-12", InvalidConfirmationCreatedAtError),
            ("expires_at", "2026-09-12", InvalidConfirmationExpiresAtError),
        ],
    )
    def test_a_hand_edited_row_is_refused_at_load(self, field, value, error):
        """Built by hand rather than through ``requested``, because the factory
        would not let most of these be set at all - which is itself the reason
        the factory exists.
        """
        fields = {
            "confirmation_id": uuid4(),
            "user_id": uuid4(),
            "wallet_id": uuid4(),
            "kind": ConfirmationKind.WITHDRAWAL,
            "internal_reference": "ref-1",
            "status": ConfirmationStatus.AWAITING,
            "created_at": MADE_AT,
            "expires_at": MADE_AT + CONFIRMATION_LIFETIME,
            "amount": ngn("500.00"),
        }
        fields[field] = value

        with pytest.raises(error):
            Confirmation(**fields)

    def test_an_amount_that_is_not_money_is_refused(self):
        with pytest.raises(InvalidConfirmationAmountError):
            a_confirmation(amount="500.00")

    def test_a_destination_that_is_not_a_destination_is_refused(self):
        with pytest.raises(UnexpectedConfirmationDestinationError):
            a_confirmation(
                kind=ConfirmationKind.PAYOUT_FROM_AVAILABLE,
                amount=ngn("500.00"),
                destination={"kind": "bank_account"},
            )

    def test_a_fund_name_that_is_not_a_string_is_refused(self):
        with pytest.raises(UnexpectedConfirmationFundError):
            a_confirmation(
                kind=ConfirmationKind.PAYOUT_FROM_LOCKED,
                amount=ngn("500.00"),
                destination=PAYEE,
                fund_name=7,
            )

    def test_a_transaction_id_that_is_not_a_uuid_is_refused(self):
        with pytest.raises(InvalidConfirmationTransactionIDError):
            Confirmation(
                confirmation_id=uuid4(),
                user_id=uuid4(),
                wallet_id=uuid4(),
                kind=ConfirmationKind.WITHDRAWAL,
                internal_reference="ref-1",
                status=ConfirmationStatus.CONFIRMED,
                created_at=MADE_AT,
                expires_at=MADE_AT + CONFIRMATION_LIFETIME,
                amount=ngn("500.00"),
                transaction_id="not-a-uuid",
            )

    def test_a_window_that_closes_before_it_opens_is_refused(self):
        """A confirmation born dead is a bug, not a state.

        Nothing constructs one deliberately, and left alone it would present as a
        prompt the user is asked and can never answer - which is worse than no
        prompt at all, because it looks like one that works.
        """
        with pytest.raises(InvalidConfirmationWindowError):
            Confirmation(
                confirmation_id=uuid4(),
                user_id=uuid4(),
                wallet_id=uuid4(),
                kind=ConfirmationKind.WITHDRAWAL,
                internal_reference="ref-1",
                status=ConfirmationStatus.AWAITING,
                created_at=MADE_AT,
                expires_at=MADE_AT - timedelta(seconds=1),
                amount=ngn("500.00"),
            )

    def test_a_zero_length_window_is_refused_too(self):
        """``<=`` and not ``<``, because a window of no length is equally
        unusable - and the boundary is the one a reader would get wrong.
        """
        with pytest.raises(InvalidConfirmationWindowError):
            Confirmation(
                confirmation_id=uuid4(),
                user_id=uuid4(),
                wallet_id=uuid4(),
                kind=ConfirmationKind.WITHDRAWAL,
                internal_reference="ref-1",
                status=ConfirmationStatus.AWAITING,
                created_at=MADE_AT,
                expires_at=MADE_AT,
                amount=ngn("500.00"),
            )


class TestExpiry:
    def test_a_live_request_is_not_expired(self):
        confirmation = a_confirmation()

        assert not confirmation.is_expired(MADE_AT + timedelta(minutes=14, seconds=59))

    def test_it_is_expired_at_the_instant_it_closes(self):
        """``>=`` and not ``>``, matching ``Session.is_expired``.

        The alternative is a request that stays answerable for one instant its
        owner was never given, and the boundary is the whole reason this method
        takes a moment instead of reading a clock: an instant is not something a
        test can wait for.
        """
        confirmation = a_confirmation()

        assert confirmation.is_expired(confirmation.expires_at)

    def test_and_afterwards(self):
        confirmation = a_confirmation()

        assert confirmation.is_expired(confirmation.expires_at + timedelta(days=1))


class TestStatusAsOf:
    """``EXPIRED`` derived rather than stored, which is the design in one method.

    The alternative is to write ``EXPIRED`` over the row the moment something
    notices - and the only thing that notices is a reader, which would make a
    ``GET`` a write. The README refuses that shape one aggregate over: decision
    49 keeps sessions absolutely expiring rather than sliding *because*
    re-stamping on every request turns a pure read into a write.
    """

    def test_a_live_awaiting_request_reads_awaiting(self):
        confirmation = a_confirmation()

        assert (
            confirmation.status_as_of(MADE_AT) is ConfirmationStatus.AWAITING
        )

    def test_a_stale_awaiting_request_reads_expired(self):
        confirmation = a_confirmation()

        assert (
            confirmation.status_as_of(MADE_AT + CONFIRMATION_LIFETIME)
            is ConfirmationStatus.EXPIRED
        )

    def test_and_the_row_behind_it_is_untouched(self):
        """The assertion that makes "derived" mean something.

        Reporting is not writing, and this is the difference between the two: the
        stored status is still ``AWAITING`` after being reported as ``EXPIRED``.
        If a later change started persisting what this method returns, a client
        polling its own request would be mutating the thing it was reading.
        """
        confirmation = a_confirmation()

        confirmation.status_as_of(MADE_AT + timedelta(days=1))

        assert confirmation.status is ConfirmationStatus.AWAITING

    def test_a_confirmed_request_stays_confirmed_for_ever(self):
        """Expiry is about whether a request may still be *answered*, and one
        that was answered is not un-answered by the clock.

        Reporting a spent request as ``EXPIRED`` a quarter of an hour later would
        also lose the only fact that matters about it, and a client would have no
        way to tell "you did this" from "this lapsed".
        """
        confirmation = a_confirmation()
        confirmation.status = ConfirmationStatus.CONFIRMED

        assert (
            confirmation.status_as_of(MADE_AT + timedelta(days=365))
            is ConfirmationStatus.CONFIRMED
        )


class TestRecordingTheTransaction:
    """Audit, not control flow - nothing decides anything by reading this field.

    It exists so that "which payment did this request become?" is answerable from
    the request rather than only from the reference, which is a key the client
    chose and may have reused.
    """

    def test_a_confirmed_request_records_its_transaction(self):
        confirmation = a_confirmation()
        confirmation.status = ConfirmationStatus.CONFIRMED
        transaction_id = uuid4()

        confirmation.record(transaction_id)

        assert confirmation.transaction_id == transaction_id

    def test_an_awaiting_request_cannot_record_one(self):
        """The ordering this guard is here to protect.

        Recording before spending would leave a confirmation naming a transaction
        it never authorised, and the mistake would be invisible - the row would
        read exactly like a correctly spent one.
        """
        confirmation = a_confirmation()

        with pytest.raises(InvalidConfirmationStatusError):
            confirmation.record(uuid4())

    def test_it_cannot_record_twice(self):
        confirmation = a_confirmation()
        confirmation.status = ConfirmationStatus.CONFIRMED
        confirmation.record(uuid4())

        with pytest.raises(InvalidConfirmationTransactionIDError):
            confirmation.record(uuid4())

    def test_it_cannot_record_something_that_is_not_a_uuid(self):
        confirmation = a_confirmation()
        confirmation.status = ConfirmationStatus.CONFIRMED

        with pytest.raises(InvalidConfirmationTransactionIDError):
            confirmation.record("not-a-uuid")


class TestTheRefusalsThatReachACaller:
    """The three names a client sees, and they are graded by class elsewhere.

    Asserted here only to pin that they are ``MoneyError``s - the API's grader
    keys on that base, so a refusal that did not inherit it would escape the
    exception handlers entirely and surface as a 500.
    """

    @pytest.mark.parametrize(
        "error",
        [ConfirmationNotFoundError, InvalidConfirmationAmountError],
    )
    def test_they_are_money_errors(self, error):
        from app.domain.money.exception import MoneyError

        assert issubclass(error, MoneyError)
