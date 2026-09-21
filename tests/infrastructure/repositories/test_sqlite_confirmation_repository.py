"""The confirmation store: a key that claims, and a gate that is one statement.

Structured like ``test_sqlite_notification_repository.py``, and the resemblance
is not a coincidence - both tables have a *claim* rather than a write at their
centre, and the claim is the same idiom (``INSERT ... ON CONFLICT DO NOTHING``
with the row count as the answer). Reading them side by side is how a drift
between the two would show up.

What this table has that the outbox does not is a **second** atomic operation,
``claim``, and it is the one the whole feature rests on. The check it replaces -
read the row, look at its status, look at its window, then write - has a gap
between the looking and the writing, and two confirms arriving in that gap would
both see ``AWAITING`` and both move the money. So the tests below spend most of
their length on ``claim``: that it spends, that it refuses a second time, that it
refuses past the window, and that the boundaries of the window agree with
``Confirmation.is_expired`` rather than being a moment out.

The seeded wallet is real because the foreign key is: a confirmation is *about* a
wallet, and a row about nothing cannot exist.
"""

import sqlite3
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
    ConfirmationAlreadyUsedError,
    ConfirmationExpiredError,
    ConfirmationNotFoundError,
)
from app.domain.money.money import Money
from app.infrastructure.persistence.sqlite_unit_of_work import open_sqlite_connection
from app.infrastructure.repositories.sqlite_confirmation_repository import (
    SqliteConfirmationRepository,
)
from app.infrastructure.repositories.sqlite_wallet_repository import (
    SqliteWalletRepository,
)

NGN = Currency.NGN

NOON = datetime(2026, 9, 12, 12, 0, 0)

PAYEE = Destination(
    kind=DestinationKind.BANK_ACCOUNT,
    identifier="0123456789",
    name="Chinedu Okafor",
    details={"bank_code": "058"},
)


def ngn(amount: str) -> Money:
    return Money(Decimal(amount), NGN)


def build_repository(*wallets):
    """A confirmation store on a fresh in-memory database, over seeded wallets.

    Seeded rather than stubbed, for the reason ``build_repository`` in the
    transaction suite gives: the foreign key is real, so a confirmation whose
    wallet does not exist would fail at the store rather than at the rule under
    test - and the failure would look like a bug in ``add``.

    Variadic because two of the tests below need a *second* wallet - one to show
    that the key is scoped to the wallet rather than to the owner, and one to ask
    about a wallet that is nobody's. Passing them here rather than reaching for
    the connection keeps the seeding in one place.
    """
    connection = open_sqlite_connection(":memory:")
    for wallet in wallets:
        SqliteWalletRepository(connection).save(wallet)
    return SqliteConfirmationRepository(connection)


def build_confirmation(wallet, owner=None, **overrides):
    kwargs = dict(
        user_id=owner if owner is not None else wallet.user_id,
        wallet_id=wallet.wallet_id,
        kind=ConfirmationKind.WITHDRAWAL,
        internal_reference="ref-1",
        now=NOON,
        amount=ngn("500.00"),
    )
    kwargs.update(overrides)
    return Confirmation.requested(**kwargs)


def test_round_trips_a_withdrawal(build_wallet):
    """Every column, read back. The ones that are ``None`` are asserted too.

    A CLOSE's absent amount and a withdrawal's absent destination are the same
    fact - "this kind does not carry that field" - and a round trip that did not
    check them would not notice a store that wrote a zero-amount or an empty
    destination over the NULL.
    """
    wallet = build_wallet()
    confirmation = build_confirmation(wallet)
    repository = build_repository(wallet)

    assert repository.add(confirmation) is True

    stored = repository.get_owned(confirmation.confirmation_id, wallet.user_id)
    assert stored.confirmation_id == confirmation.confirmation_id
    assert stored.user_id == wallet.user_id
    assert stored.wallet_id == wallet.wallet_id
    assert stored.kind is ConfirmationKind.WITHDRAWAL
    assert stored.internal_reference == "ref-1"
    assert stored.status is ConfirmationStatus.AWAITING
    assert stored.created_at == NOON
    assert stored.expires_at == NOON + CONFIRMATION_LIFETIME
    assert stored.amount == ngn("500.00")
    assert stored.destination is None
    assert stored.fund_name is None
    assert stored.transaction_id is None


def test_round_trips_a_payout_destination(build_wallet):
    """The JSON column, which is the one that could round-trip into a dict.

    ``Destination`` is frozen and seals its ``details`` behind a read-only
    mapping, so a store that handed back a plain dict would produce a value that
    compared unequal to the one written and could be mutated in place - the exact
    thing the freeze exists to prevent.
    """
    wallet = build_wallet()
    confirmation = build_confirmation(
        wallet,
        kind=ConfirmationKind.PAYOUT_FROM_LOCKED,
        destination=PAYEE,
        fund_name="Savings",
    )
    repository = build_repository(wallet)

    repository.add(confirmation)

    stored = repository.get_owned(confirmation.confirmation_id, wallet.user_id)
    assert stored.destination == PAYEE
    assert dict(stored.destination.details) == {"bank_code": "058"}
    assert stored.fund_name == "Savings"


def test_round_trips_a_close_with_no_amount(build_wallet):
    """The kind that carries nothing, and therefore the one a NULL-writing bug
    would show up in first.
    """
    wallet = build_wallet()
    confirmation = build_confirmation(
        wallet, kind=ConfirmationKind.CLOSE, amount=None
    )
    repository = build_repository(wallet)

    repository.add(confirmation)

    stored = repository.get_owned(confirmation.confirmation_id, wallet.user_id)
    assert stored.kind is ConfirmationKind.CLOSE
    assert stored.amount is None
    assert stored.destination is None


def test_a_second_add_under_the_same_key_changes_nothing(build_wallet):
    """The claim, stated as the two things it must do at once.

    ``False`` is the answer the *caller* acts on - it is what makes a retried
    request return the original rather than create a duplicate. But ``False``
    alone would also be returned by a store that had overwritten the row with the
    second request, so the row is read back and compared: a claim that let the
    loser win would be worse than no claim at all, because it would look like one.
    """
    wallet = build_wallet()
    first = build_confirmation(wallet, amount=ngn("500.00"))
    second = build_confirmation(
        wallet, amount=ngn("9000.00"), kind=ConfirmationKind.PAYOUT_FROM_AVAILABLE,
        destination=PAYEE,
    )
    repository = build_repository(wallet)
    repository.add(first)

    assert repository.add(second) is False

    stored = repository.find(wallet.wallet_id, "ref-1")
    assert stored.confirmation_id == first.confirmation_id
    assert stored.amount == ngn("500.00")
    assert stored.kind is ConfirmationKind.WITHDRAWAL


def test_the_key_is_scoped_to_the_wallet(build_wallet):
    """One user, two wallets, one key - two requests.

    Scoping the key by *owner* would have left exactly this case open: the same
    person posting the same key to two of their own wallets is not a retry, and a
    store that answered it with the first request would be handing them a
    confirmation for the wrong wallet.

    **The second wallet is in dollars, which is what decision 267 costs a test
    like this one.** One live wallet per currency per owner, so "two of their own
    wallets" cannot be two naira ones - and the second confirmation is in dollars
    with it, so the row is shaped like one this system would actually create. What
    the test is about, the key's scope, is untouched by either.
    """
    owner = uuid4()
    first_wallet = build_wallet(user_id=owner)
    second_wallet = build_wallet(user_id=owner, currency=Currency.USD)
    repository = build_repository(first_wallet, second_wallet)

    assert repository.add(build_confirmation(first_wallet)) is True
    assert (
        repository.add(
            build_confirmation(
                second_wallet, amount=Money(Decimal("500.00"), Currency.USD)
            )
        )
        is True
    )

    assert (
        repository.find(first_wallet.wallet_id, "ref-1").confirmation_id
        != repository.find(second_wallet.wallet_id, "ref-1").confirmation_id
    )


def test_find_answers_none_for_a_key_nobody_used(build_wallet):
    """Not-found is an *expected* outcome here rather than an error.

    It is what ``add`` returning ``False`` is answered with, so it returns None
    instead of raising - the same split
    ``TransactionRepository.get_by_internal_reference`` makes.
    """
    wallet = build_wallet()

    assert build_repository(wallet).find(wallet.wallet_id, "never-used") is None


def test_find_does_not_reach_across_wallets(build_wallet):
    """The other half of the scoping, from the reading side.

    The key is safe to hand a client back precisely because it cannot name a
    request on a wallet they do not own - ``wallet_id`` is half of what
    identifies it.

    The stranger's wallet is seeded rather than invented, because "not found on a
    wallet that does not exist" would pass for a reason that has nothing to do
    with scoping.
    """
    owner_wallet = build_wallet()
    stranger_wallet = build_wallet(user_id=uuid4())
    repository = build_repository(owner_wallet, stranger_wallet)
    repository.add(build_confirmation(owner_wallet))

    assert repository.find(stranger_wallet.wallet_id, "ref-1") is None


def test_claim_spends_the_request_and_returns_it_confirmed(build_wallet):
    """``CONFIRMED`` comes back, and the row agrees.

    Both halves matter. The returned object is what the service puts in the
    answer it hands the client; the stored row is what stops a second confirm.
    A claim that returned a spent object without writing would move money once
    and allow it a second time.
    """
    wallet = build_wallet()
    confirmation = build_confirmation(wallet)
    repository = build_repository(wallet)
    repository.add(confirmation)

    claimed = repository.claim(
        confirmation.confirmation_id,
        wallet.user_id,
        ConfirmationKind.WITHDRAWAL,
        NOON,
    )

    assert claimed.status is ConfirmationStatus.CONFIRMED
    assert claimed.amount == ngn("500.00")
    assert (
        repository.get_owned(confirmation.confirmation_id, wallet.user_id).status
        is ConfirmationStatus.CONFIRMED
    )


def test_claiming_twice_is_refused(build_wallet):
    """The gate. This is the assertion the feature rests on.

    The second claim matches no row - not because anything looked at the status
    and decided, but because the UPDATE's ``status = AWAITING`` condition is
    false. That is the difference between this and a check-then-write, and it is
    the difference two concurrent confirms would expose.
    """
    wallet = build_wallet()
    confirmation = build_confirmation(wallet)
    repository = build_repository(wallet)
    repository.add(confirmation)
    repository.claim(
        confirmation.confirmation_id, wallet.user_id, ConfirmationKind.WITHDRAWAL, NOON
    )

    with pytest.raises(ConfirmationAlreadyUsedError):
        repository.claim(
            confirmation.confirmation_id,
            wallet.user_id,
            ConfirmationKind.WITHDRAWAL,
            NOON,
        )


def test_claiming_past_the_window_is_refused(build_wallet):
    wallet = build_wallet()
    confirmation = build_confirmation(wallet)
    repository = build_repository(wallet)
    repository.add(confirmation)

    with pytest.raises(ConfirmationExpiredError):
        repository.claim(
            confirmation.confirmation_id,
            wallet.user_id,
            ConfirmationKind.WITHDRAWAL,
            NOON + CONFIRMATION_LIFETIME,
        )


def test_the_window_closes_at_the_instant_it_closes(build_wallet):
    """The boundary, pinned against ``Confirmation.is_expired``.

    ``expires_at > ?`` in the store and ``as_of >= expires_at`` in the aggregate
    are the same rule written twice, and they must agree or the two would
    disagree about the last instant of a request's life - in the direction of
    moving money for one moment longer than the person was given.

    So the two moments either side of the boundary are both asserted, and the
    test below asserts the aggregate's answer at the same two moments. A change
    to either comparison that was not mirrored fails one of them.
    """
    wallet = build_wallet()
    expired = build_confirmation(wallet, internal_reference="at-the-edge")
    live = build_confirmation(wallet, internal_reference="just-inside")
    repository = build_repository(wallet)
    repository.add(expired)
    repository.add(live)

    with pytest.raises(ConfirmationExpiredError):
        repository.claim(
            expired.confirmation_id,
            wallet.user_id,
            ConfirmationKind.WITHDRAWAL,
            NOON + CONFIRMATION_LIFETIME,
        )

    claimed = repository.claim(
        live.confirmation_id,
        wallet.user_id,
        ConfirmationKind.WITHDRAWAL,
        NOON + CONFIRMATION_LIFETIME - timedelta(microseconds=1),
    )

    assert claimed.status is ConfirmationStatus.CONFIRMED
    assert not live.is_expired(NOON + CONFIRMATION_LIFETIME - timedelta(microseconds=1))
    assert expired.is_expired(NOON + CONFIRMATION_LIFETIME)


def test_claiming_another_actors_request_is_not_found(build_wallet):
    """Absent and foreign are one answer, for the reason ``get_owned`` gives.

    Telling a stranger that a request exists and is not theirs answers a question
    they have no standing to ask - and here that question is worth more than it
    is anywhere else in the API, because a request id is the only thing needed to
    *answer* a request.
    """
    wallet = build_wallet()
    confirmation = build_confirmation(wallet)
    repository = build_repository(wallet)
    repository.add(confirmation)

    with pytest.raises(ConfirmationNotFoundError):
        repository.claim(
            confirmation.confirmation_id,
            uuid4(),
            ConfirmationKind.WITHDRAWAL,
            NOON,
        )

    # And it is still spendable by its owner, which is what makes the refusal
    # above a refusal rather than a side effect.
    assert (
        repository.get_owned(confirmation.confirmation_id, wallet.user_id).status
        is ConfirmationStatus.AWAITING
    )


def test_claiming_a_request_that_is_not_there_is_not_found(build_wallet):
    repository = build_repository(build_wallet())

    with pytest.raises(ConfirmationNotFoundError):
        repository.claim(uuid4(), uuid4(), ConfirmationKind.WITHDRAWAL, NOON)


def test_claiming_under_the_wrong_kind_is_refused(build_wallet):
    """A payout's request cannot authorise a withdrawal.

    The UPDATE carries the kind, so it matches no row and nothing moves. What it
    is *reported* as is ``ConfirmationNotFoundError`` - the request named is not
    a withdrawal, so as far as this call is concerned there is no such request -
    and that is a deliberate choice over the misleading alternative: the
    fall-through read would otherwise call a wrong-kind request *expired*, which
    is a name that sends a caller looking at a clock instead of at their own bug.
    """
    wallet = build_wallet()
    confirmation = build_confirmation(
        wallet, kind=ConfirmationKind.CLOSE, amount=None
    )
    repository = build_repository(wallet)
    repository.add(confirmation)

    with pytest.raises(ConfirmationNotFoundError):
        repository.claim(
            confirmation.confirmation_id,
            wallet.user_id,
            ConfirmationKind.WITHDRAWAL,
            NOON,
        )

    assert (
        repository.get_owned(confirmation.confirmation_id, wallet.user_id).status
        is ConfirmationStatus.AWAITING
    )


def test_save_progresses_the_status_and_records_the_transaction(build_wallet):
    """The two columns that move, and they move together."""
    wallet = build_wallet()
    confirmation = build_confirmation(wallet)
    repository = build_repository(wallet)
    repository.add(confirmation)
    claimed = repository.claim(
        confirmation.confirmation_id, wallet.user_id, ConfirmationKind.WITHDRAWAL, NOON
    )
    transaction_id = uuid4()

    claimed.record(transaction_id)
    repository.save(claimed)

    stored = repository.get_owned(confirmation.confirmation_id, wallet.user_id)
    assert stored.status is ConfirmationStatus.CONFIRMED
    assert stored.transaction_id == transaction_id


def test_save_does_not_rewrite_what_the_request_said(build_wallet):
    """**The row's promise**: a confirmed request did what it said it would.

    Every fact about the movement is written once at ``add`` and never rewritten.
    This is not hypothetical tidiness - it is what lets ``request_confirmation``
    return the *existing* request when a key is reused, and it is what makes the
    preview a client was shown the movement that will actually happen.

    The test mutates every one of those facts on the aggregate and saves, then
    reads the row back. All of them are unchanged, which is the store's
    guarantee rather than the aggregate's - the aggregate would happily hold the
    edited values, and the ``UPDATE`` simply does not mention those columns.

    ``wallet_id`` is re-pointed at a *real* second wallet rather than at a random
    uuid, and that is deliberate rather than tidier. ``save`` is an ``INSERT ...
    ON CONFLICT DO UPDATE``, and whether SQLite checks the foreign key on the
    row it is *not* going to insert is not something worth depending on - a test
    that pinned behaviour there would be pinning the query planner rather than
    this repository. Pointing it at a wallet that exists makes the assertion
    about the columns and nothing else.

    That second wallet is in dollars because decision 267 allows one live wallet
    per currency per owner, and two rows for one owner is the whole requirement
    here - the wallet is a foreign key for this test to point at, and its currency
    plays no part in what is asserted. The re-pointed ``wallet_id`` it *is* asked
    to hold is a value the ``UPDATE`` never writes, which is exactly the claim.
    """
    wallet = build_wallet()
    other_wallet = build_wallet(user_id=wallet.user_id, currency=Currency.USD)
    confirmation = build_confirmation(wallet, amount=ngn("500.00"))
    repository = build_repository(wallet, other_wallet)
    repository.add(confirmation)
    claimed = repository.claim(
        confirmation.confirmation_id, wallet.user_id, ConfirmationKind.WITHDRAWAL, NOON
    )

    claimed.amount = ngn("9999.00")
    claimed.internal_reference = "somebody-elses-key"
    claimed.wallet_id = other_wallet.wallet_id
    claimed.expires_at = NOON + timedelta(days=3650)
    repository.save(claimed)

    stored = repository.get_owned(confirmation.confirmation_id, wallet.user_id)
    assert stored.amount == ngn("500.00")
    assert stored.internal_reference == "ref-1"
    assert stored.wallet_id == wallet.wallet_id
    assert stored.expires_at == NOON + CONFIRMATION_LIFETIME


def test_the_foreign_key_is_real(build_wallet):
    """A confirmation about a wallet that does not exist is refused by the store.

    Asserted because the column is declared ``REFERENCES wallets(wallet_id)``,
    and a database opened without ``PRAGMA foreign_keys = ON`` would ignore that
    entirely - silently, and only for the rows that were wrong. The exception is
    named rather than caught as ``Exception``, because that is the whole point:
    if this test passed for any other reason it would be asserting nothing.
    """
    wallet = build_wallet()
    repository = build_repository(wallet)
    orphan = build_confirmation(wallet, wallet_id=uuid4())

    with pytest.raises(sqlite3.IntegrityError):
        repository.add(orphan)
