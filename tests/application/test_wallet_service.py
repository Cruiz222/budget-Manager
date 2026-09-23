from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.wallet_service import WalletService
from app.domain.money.confirmation import CONFIRMATION_LIFETIME
from app.domain.money.confirmationKind import ConfirmationKind
from app.domain.money.confirmationStatus import ConfirmationStatus
from app.domain.money.currency import Currency
from app.domain.money.destination import Destination
from app.domain.money.destinationKind import DestinationKind
from app.domain.money.exception import (
    ConfirmationAlreadyUsedError,
    ConfirmationExpiredError,
    ConfirmationNotFoundError,
    DuplicateWalletCurrencyError,
    InsufficientFundsError,
    ReferenceAlreadyRefusedError,
    WalletAlreadyActiveError,
    WalletAlreadyClosedError,
    WalletAlreadyFrozenError,
    WalletClosedError,
    WalletFrozenError,
    WalletHasActivePlansError,
    WalletNotEmptyError,
    WalletNotFoundError,
    CurrencyNotOfferedError,
)
from app.domain.money.fundKind import FundKind
from app.domain.money.money import Money
from app.domain.money.transaction import Transaction
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.transactionType import TransactionType
from app.domain.money.wallet import Wallet
from app.domain.money.walletStatus import WalletStatus
from app.domain.notifications.notificationKind import NotificationKind
from app.domain.planning.planStatus import PlanStatus
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)
from tests.conftest import TEST_USER_ID

NGN = Currency.NGN

#: The user every service in this file acts as.
#:
#: The same one ``build_wallet`` gives its wallets - which is what makes the
#: fifty-odd tests below work unchanged: a wallet built by the fixture belongs to
#: whoever the service acts as, so ``service.get_wallet(wallet.wallet_id)`` still
#: finds it. A *different* id here would make every one of them raise
#: ``WalletNotFoundError``, and the failure would be about this constant rather
#: than about any behaviour under test.
ACTOR = TEST_USER_ID

#: Where a wallet receipt is addressed. Passed in rather than read from the
#: environment, because ``WalletService`` takes its inputs as arguments -
#: reading ``os.environ`` is the CLI's job, not the use case's.
RECIPIENT = "chinedu@example.com"

#: A moment after every pot these tests build. The pots brought in by
#: ``build_wallet(locked=...)`` have no maturity date and so are open at every
#: moment; the dated ones below are sealed against a later date. No test in this
#: file reads a clock.
MOMENT = datetime(2026, 1, 1)

DESTINATION = Destination(
    kind=DestinationKind.BANK_ACCOUNT,
    identifier="0123456789",
    name="Chinedu Okafor",
    details={"bank_code": "058"},
)


def build_service(tmp_path, recipient=None, name="wallet_service.db"):
    factory = SqliteUnitOfWorkFactory(str(tmp_path / name))
    return WalletService(factory, recipient=recipient, actor=ACTOR), factory


def as_somebody_else(factory, actor):
    """A second service over the same database, acting as a different person.

    A separate ``WalletService`` rather than a patched ``_actor``, because the
    actor is what every scoped read is keyed by and a test that reached in to
    change it would be testing the attribute rather than the rule. This is the
    same seam ``build_service`` uses, one argument different.
    """
    return WalletService(factory, actor=actor)


def seed(factory, wallet):
    uow = factory.start()
    uow.wallets.save(wallet)
    uow.commit()


def get_wallet(factory, wallet_id):
    uow = factory.start()
    try:
        return uow.wallets.get_owned(wallet_id, ACTOR)
    finally:
        uow.rollback()


def get_transaction(factory, internal_reference):
    uow = factory.start()
    try:
        return uow.transactions.get_by_internal_reference(internal_reference)
    finally:
        uow.rollback()


def as_stored(wallet, internal_reference):
    """The reference a client sent, as the ledger actually holds it.

    Since Phase 2b ``WalletService`` namespaces every caller-supplied
    idempotency key to the wallet it was spent from, so a test that types a raw
    key into ``get_by_internal_reference`` finds nothing - correctly, and for a
    reason worth restating here rather than leaving a reader to guess at a
    mysteriously failing lookup.

    The lookup is *global*: it is a single ``WHERE internal_reference = ?``
    against a globally unique column, with no wallet in the predicate. That is
    why the key has to carry the wallet's identity - otherwise two actors
    choosing the same word share one row and the second one's retry returns the
    first one's transaction. Spelling the format here as well as in
    ``app.domain.money.reference`` is deliberate: it is observable in every money
    response, so it is part of what this API promises rather than an internal
    detail, and the separator is now a promise with a provider on the other end
    of it - which is why a test that pins it is worth the duplication.
    """
    return f"{wallet.wallet_id}.{internal_reference}"


def notifications_of(factory):
    """Every receipt the wallet operations have left owed - the queue, not the report."""
    uow = factory.start()
    try:
        return uow.notifications.pending()
    finally:
        uow.rollback()


def a_request(
    service,
    wallet_id,
    kind,
    *,
    amount=None,
    destination=None,
    fund_name=None,
    internal_reference=None,
    now=MOMENT,
):
    """Record a confirmation and return the record. **Nothing moves.**

    The first half of every money-out call in this file, named for what it is
    rather than for what it will become. The reference is left ``None`` unless a
    test is *about* the key, in which case the service mints one - the same
    default the API has, and the reason the nine tests below that once passed a
    ``str(uuid4())`` no longer have to.
    """
    return service.request_confirmation(
        wallet_id,
        kind,
        now,
        internal_reference=internal_reference,
        amount=amount,
        destination=destination,
        fund_name=fund_name,
    ).confirmation


def answer(service, request, as_of=MOMENT):
    """Answer a recorded request. **This is the call that moves money.**"""
    return service.confirm(request.confirmation_id, as_of)


def withdraw(service, wallet_id, amount, internal_reference=None, now=MOMENT):
    """Record and answer a withdrawal, for the tests that are not about the split.

    Two calls rather than one, and the shape is worth keeping visible even here:
    the moment money moves is ``answer``, and a helper that hid both halves would
    make this file stop showing the feature it is testing.
    """
    request = a_request(
        service,
        wallet_id,
        ConfirmationKind.WITHDRAWAL,
        amount=amount,
        internal_reference=internal_reference,
        now=now,
    )
    return answer(service, request, now).transaction


def payout(
    service,
    wallet_id,
    amount,
    destination,
    fund_name=None,
    internal_reference=None,
    now=MOMENT,
):
    """The same, for a locked-source payout - the only source this file uses.

    ``fund_name`` is left out by the callers that want the pooled draw, which is
    a real request rather than a missing field; see ``Confirmation``. The
    available-source kind has no caller here and so has no helper: it is exercised
    where it can be told apart from this one, which is
    ``tests/presentation/api/test_money.py``.
    """
    request = a_request(
        service,
        wallet_id,
        ConfirmationKind.PAYOUT_FROM_LOCKED,
        amount=amount,
        destination=destination,
        fund_name=fund_name,
        internal_reference=internal_reference,
        now=now,
    )
    return answer(service, request, now).transaction


def close(service, wallet_id, now=MOMENT):
    """Record and answer a close, returning the wallet.

    A close produces no ledger row, so there is no transaction to return - which
    is why this one returns the wallet and the other two return transactions.
    """
    request = a_request(service, wallet_id, ConfirmationKind.CLOSE, now=now)
    return answer(service, request, now).wallet


def test_deposit_loads_wallet_and_persists_new_balance(tmp_path, build_wallet):
    wallet = build_wallet()
    service, factory = build_service(tmp_path)
    seed(factory, wallet)

    transaction = service.deposit(
        wallet.wallet_id,
        Money(Decimal("5000"), NGN),
        internal_reference=str(uuid4()),
    )

    assert transaction.status is TransactionStatus.SUCCESSFUL
    assert (
        get_wallet(factory, wallet.wallet_id).available_balance
        == Money(Decimal("15000"), NGN)
    )
    stored_transaction = get_transaction(factory, transaction.internal_reference)
    assert stored_transaction is not None
    assert stored_transaction.status is TransactionStatus.SUCCESSFUL


def test_withdrawal_persists_new_balance(tmp_path, build_wallet):
    wallet = build_wallet()
    service, factory = build_service(tmp_path)
    seed(factory, wallet)

    withdraw(
        service,
        wallet.wallet_id,
        Money(Decimal("3000"), NGN),
        internal_reference=str(uuid4()),
    )

    assert (
        get_wallet(factory, wallet.wallet_id).available_balance
        == Money(Decimal("7000"), NGN)
    )


def test_lock_into_fund_moves_both_balances(tmp_path, build_wallet):
    """The same fact as the old ``test_lock_moves_both_balances``, pot-scoped.

    Two balances still move in opposite directions; what is new is that the
    locked half of it now has a name, and the round trip through SQLite is what
    proves the pot was persisted rather than only the wallet's available balance.
    """
    wallet = build_wallet()
    wallet.open_fund("Vacation", FundKind.PERSONAL)
    service, factory = build_service(tmp_path)
    seed(factory, wallet)

    service.lock_into_fund(
        wallet.wallet_id,
        "Vacation",
        Money(Decimal("3000"), NGN),
        internal_reference=str(uuid4()),
        as_of=MOMENT,
    )

    stored = get_wallet(factory, wallet.wallet_id)
    assert stored.available_balance == Money(Decimal("7000"), NGN)
    assert stored.locked_balance == Money(Decimal("3000"), NGN)
    assert stored.fund_by_name("Vacation").balance == Money(Decimal("3000"), NGN)


def test_deposit_into_fund_round_trips_through_sqlite(tmp_path, build_wallet):
    """A pot's balance and its ``fund_id`` both survive the database.

    The deposit path is where the ``fund_id`` column on the ledger first gets
    written, so this is the round trip that covers the serialization helpers as
    well as the funds table itself.
    """
    wallet = build_wallet(available="0")
    fund = wallet.open_fund("Vacation", FundKind.PERSONAL)
    service, factory = build_service(tmp_path)
    seed(factory, wallet)

    transaction = service.deposit_into_fund(
        wallet.wallet_id,
        "Vacation",
        Money(Decimal("5000"), NGN),
        internal_reference=str(uuid4()),
        as_of=MOMENT,
    )

    assert transaction.fund_id == fund.fund_id
    stored = get_wallet(factory, wallet.wallet_id)
    assert stored.locked_balance == Money(Decimal("5000"), NGN)
    assert stored.available_balance == Money(Decimal("0"), NGN)
    assert stored.fund_by_name("Vacation").fund_id == fund.fund_id


def test_release_from_fund_round_trips_through_sqlite(tmp_path, build_wallet):
    wallet = build_wallet(available="0", locked="5000")
    service, factory = build_service(tmp_path)
    seed(factory, wallet)

    service.release_from_fund(
        wallet.wallet_id,
        "Locked",
        Money(Decimal("2000"), NGN),
        internal_reference=str(uuid4()),
        as_of=MOMENT,
    )

    stored = get_wallet(factory, wallet.wallet_id)
    assert stored.available_balance == Money(Decimal("2000"), NGN)
    assert stored.locked_balance == Money(Decimal("3000"), NGN)


def test_payout_from_locked_spends_the_locked_balance(tmp_path, build_wallet):
    """Round-trips PAYOUT through SQLite, so the enum name persists and hydrates."""
    wallet = build_wallet(available="1000", locked="5000")
    service, factory = build_service(tmp_path)
    seed(factory, wallet)

    transaction = payout(
        service,
        wallet.wallet_id,
        Money(Decimal("3000"), NGN),
        DESTINATION,
    )

    assert transaction.type is TransactionType.PAYOUT
    stored = get_wallet(factory, wallet.wallet_id)
    assert stored.locked_balance == Money(Decimal("2000"), NGN)
    assert stored.available_balance == Money(Decimal("1000"), NGN)


def test_payout_destination_survives_the_database(tmp_path, build_wallet):
    """The whole point of snapshotting: where the money went is recoverable."""
    wallet = build_wallet(locked="5000")
    service, factory = build_service(tmp_path)
    seed(factory, wallet)

    transaction = payout(
        service,
        wallet.wallet_id,
        Money(Decimal("3000"), NGN),
        DESTINATION,
    )

    stored_transaction = get_transaction(factory, transaction.internal_reference)
    assert stored_transaction.destination == DESTINATION
    assert stored_transaction.destination.detail("bank_code") == "058"


def test_rejected_payout_persists_a_failed_audit_row_and_no_balance_change(tmp_path, build_wallet):
    """A refusal is recorded, and recording it is what spends the request.

    The second half of this test is new with the confirmation and is the reason
    the first half is still here. A FAILED row means ``WalletOperation.execute``
    has written a reference into a globally unique column, and that lookup returns
    the row **whatever its status** - so if the request stayed answerable, a retry
    would re-enter under the same spent reference, be handed this FAILED row back,
    move nothing, and report success. A receipt would then be composed for it.

    Spending the request on the attempt is what keeps a spent reference from ever
    being read again as an outcome. The client asks again, and asking again is a
    new reference.
    """
    wallet = build_wallet(locked="5000")
    service, factory = build_service(tmp_path)
    seed(factory, wallet)
    internal_reference = str(uuid4())
    request = a_request(
        service,
        wallet.wallet_id,
        ConfirmationKind.PAYOUT_FROM_LOCKED,
        amount=Money(Decimal("15000"), NGN),
        destination=DESTINATION,
        internal_reference=internal_reference,
    )

    with pytest.raises(InsufficientFundsError):
        answer(service, request)

    assert (
        get_wallet(factory, wallet.wallet_id).locked_balance
        == Money(Decimal("5000"), NGN)
    )
    stored_transaction = get_transaction(
        factory, as_stored(wallet, internal_reference)
    )
    assert stored_transaction is not None
    assert stored_transaction.status is TransactionStatus.FAILED

    with pytest.raises(ConfirmationAlreadyUsedError):
        answer(service, request)


def test_operation_on_unknown_wallet_raises(tmp_path):
    service, factory = build_service(tmp_path)

    with pytest.raises(WalletNotFoundError):
        service.deposit(
            uuid4(),
            Money(Decimal("5000"), NGN),
            internal_reference=str(uuid4()),
        )


def test_rejected_operation_persists_a_failed_audit_row_and_no_balance_change(tmp_path, build_wallet):
    wallet = build_wallet(status=WalletStatus.CLOSED)
    service, factory = build_service(tmp_path)
    seed(factory, wallet)
    internal_reference = str(uuid4())

    with pytest.raises(WalletClosedError):
        service.deposit(
            wallet.wallet_id,
            Money(Decimal("5000"), NGN),
            internal_reference=internal_reference,
        )

    assert (
        get_wallet(factory, wallet.wallet_id).available_balance
        == Money(Decimal("10000"), NGN)
    )
    stored_transaction = get_transaction(
        factory, as_stored(wallet, internal_reference)
    )
    assert stored_transaction is not None
    assert stored_transaction.status is TransactionStatus.FAILED


def test_replaying_an_internal_reference_does_not_double_credit(tmp_path, build_wallet):
    wallet = build_wallet()
    service, factory = build_service(tmp_path)
    seed(factory, wallet)
    internal_reference = str(uuid4())

    first = service.deposit(
        wallet.wallet_id,
        Money(Decimal("5000"), NGN),
        internal_reference=internal_reference,
    )
    second = service.deposit(
        wallet.wallet_id,
        Money(Decimal("5000"), NGN),
        internal_reference=internal_reference,
    )

    assert second.transaction_id == first.transaction_id
    assert second.status is TransactionStatus.SUCCESSFUL
    assert (
        get_wallet(factory, wallet.wallet_id).available_balance
        == Money(Decimal("15000"), NGN)
    )


def test_open_wallet_persists_an_empty_active_wallet(tmp_path):
    service, factory = build_service(tmp_path)

    wallet = service.open_wallet(NGN)

    stored = get_wallet(factory, wallet.wallet_id)
    assert stored.user_id == ACTOR
    assert stored.status is WalletStatus.ACTIVE
    assert stored.currency is NGN
    assert stored.available_balance == Money(Decimal("0"), NGN)
    assert stored.locked_balance == Money(Decimal("0"), NGN)


def test_get_wallet_of_unknown_id_raises(tmp_path):
    service, _ = build_service(tmp_path)

    with pytest.raises(WalletNotFoundError):
        service.get_wallet(uuid4())


class TestOneWalletPerCurrency:
    """Decision 267, at the layer that decides it.

    **The rule is asserted here and not only over HTTP**, because this is where it
    lives: the route calls ``open_wallet`` and translates whatever comes back, so a
    test that only ever posts to ``/wallets`` would pass against a route that did
    the checking itself - and the CLI and the browser would each then be a door
    with its own copy of the rule. There is one copy, and it is this one.

    The four claims: a duplicate is refused; a currency outside the MVP offer is
    refused; either refusal persists nothing; and a closed wallet does not count
    as holding its currency.
    """

    def test_a_second_wallet_in_a_held_currency_is_refused(self, tmp_path):
        service, _ = build_service(tmp_path)
        first = service.open_wallet(NGN)

        with pytest.raises(DuplicateWalletCurrencyError) as refused:
            service.open_wallet(NGN)

        assert str(first.wallet_id) in str(refused.value)

    def test_a_currency_not_offered_by_the_mvp_is_refused(self, tmp_path):
        """USD remains a known currency, but this MVP opens only NGN wallets.

        Knowing how to represent a currency is different from offering a new wallet
        in that currency. Refusing USD here keeps every presentation layer on the
        same product rule.
        """
        service, _ = build_service(tmp_path)

        with pytest.raises(CurrencyNotOfferedError) as refused:
            service.open_wallet(Currency.USD)

        assert "NGN" in str(refused.value)
        assert "USD" in str(refused.value)
        assert service.wallets_for_actor() == []
    
    
    def test_the_refusal_writes_nothing(self, tmp_path):
        """**The check runs before the save, and this is the assertion that it
        still does.** A refusal that had already written a row would leave a wallet
        the caller was told they could not have - and, with the store's index
        underneath, would leave the *next* open in that currency failing too.
        """
        service, factory = build_service(tmp_path)
        kept = service.open_wallet(NGN)

        with pytest.raises(DuplicateWalletCurrencyError):
            service.open_wallet(NGN)

        assert [one.wallet_id for one in service.wallets_for_actor()] == [kept.wallet_id]

    def test_a_closed_wallet_frees_its_currency(self, tmp_path):
        """**The one that keeps the rule from being a one-way door.** Closing a
        wallet and opening another in the same currency is an ordinary thing to
        want - closing one by mistake should not cost a currency for good - so the
        rule reads "a wallet that is not closed", in the use case and in the store's
        index alike.
        """
        service, factory = build_service(tmp_path)
        first = service.open_wallet(NGN)
        close(service, first.wallet_id)

        second = service.open_wallet(NGN)

        assert second.wallet_id != first.wallet_id
        # Both are still listed: the currency came back, and the closed wallet's
        # history did not go anywhere.
        assert [one.status for one in service.wallets_for_actor()] == [
            WalletStatus.CLOSED,
            WalletStatus.ACTIVE,
        ]


    def test_another_person_wallet_does_not_count(self, tmp_path):
        """A wallet is held by one person, and the rule is per account. Two people
        may each hold a naira wallet - which is only true if the check is scoped to
        the actor, and a check written against the wrong key would pass every test
        above while refusing the second person their first wallet.
        """
        service, factory = build_service(tmp_path)
        service.open_wallet(NGN)
        somebody_else = as_somebody_else(factory, uuid4())

        theirs = somebody_else.open_wallet(NGN)

        assert theirs.user_id != ACTOR


def test_freeze_persists_frozen_then_unfreeze_restores_active(tmp_path, build_wallet):
    wallet = build_wallet()
    service, factory = build_service(tmp_path)
    seed(factory, wallet)

    frozen = service.freeze_wallet(wallet.wallet_id)
    assert frozen.status is WalletStatus.FROZEN
    assert (
        get_wallet(factory, wallet.wallet_id).status
        is WalletStatus.FROZEN
    )

    active = service.unfreeze_wallet(wallet.wallet_id)
    assert active.status is WalletStatus.ACTIVE
    assert (
        get_wallet(factory, wallet.wallet_id).status
        is WalletStatus.ACTIVE
    )


def test_freezing_an_already_frozen_wallet_rejects(tmp_path, build_wallet):
    wallet = build_wallet()
    service, factory = build_service(tmp_path)
    seed(factory, wallet)

    service.freeze_wallet(wallet.wallet_id)

    with pytest.raises(WalletAlreadyFrozenError):
        service.freeze_wallet(wallet.wallet_id)

    # Unfreezing a frozen wallet is the intended path - it succeeds.
    assert (
        service.unfreeze_wallet(wallet.wallet_id).status
        is WalletStatus.ACTIVE
    )


def test_unfreezing_an_already_active_wallet_rejects(tmp_path, build_wallet):
    wallet = build_wallet()
    service, factory = build_service(tmp_path)
    seed(factory, wallet)

    with pytest.raises(WalletAlreadyActiveError):
        service.unfreeze_wallet(wallet.wallet_id)


def test_status_change_on_unknown_wallet_raises(tmp_path):
    service, _ = build_service(tmp_path)

    with pytest.raises(WalletNotFoundError):
        service.freeze_wallet(uuid4())


def test_transactions_for_wallet_returns_ledger_oldest_first(tmp_path, build_wallet):
    """Both rows come back in order, and the two do not share a status any more.

    This asserted ``all(... SUCCESSFUL)`` until Phase 2b, which was true while
    every movement claimed to have finished the instant it was decided. The
    withdrawal in the middle of this test is the counter-example that made the
    assertion wrong: its money is held, bound for an account nobody here has
    contacted. Listing the statuses by hand rather than filtering them keeps the
    contrast on screen - a deposit settles here, a withdrawal does not, and both
    are rows in the same ledger.

    **The withdrawal is stamped a minute after the deposit, and that offset is
    the point rather than a convenience.** The two rows are written from two
    frames deliberately: a deposit records the clock, because no decision reads
    its moment, while a movement *out* records the moment it was judged in - the
    day the daily cap sums its allowance from (see ``WalletOperation.now``). An
    order between a row from each frame is therefore not a fact about this
    service until the test puts them in one frame, which is what
    ``deposited.created_at`` below does. Note it reads the *record's* stamp, not
    the clock, so this file's rule that no test here reads a clock still holds.
    """
    wallet = build_wallet()
    service, factory = build_service(tmp_path)
    seed(factory, wallet)

    deposited = service.deposit(
        wallet.wallet_id,
        Money(Decimal("5000"), NGN),
        internal_reference=str(uuid4()),
    )
    withdraw(
        service,
        wallet.wallet_id,
        Money(Decimal("2000"), NGN),
        internal_reference=str(uuid4()),
        now=deposited.created_at + timedelta(minutes=1),
    )

    ledger = service.transactions_for_wallet(wallet.wallet_id)

    assert [transaction.type for transaction in ledger] == [
        TransactionType.DEPOSIT,
        TransactionType.WITHDRAWAL,
    ]
    assert [transaction.status for transaction in ledger] == [
        TransactionStatus.SUCCESSFUL,
        TransactionStatus.PENDING,
    ]


def test_transactions_for_wallet_ignores_other_wallets(tmp_path, build_wallet):
    """**The second wallet is in dollars, and decision 267 is why.** One owner may
    hold one wallet per currency, so two naira wallets cannot both be saved - and a
    wallet this test needs to *deposit into* cannot be a closed one either. The
    currency has nothing to do with the claim (one wallet's ledger is not another's
    wallet's), and the smaller amount is the USD tier's ceiling rather than a
    preference: an unverified account may hold 3,000 dollars and move 500 at a
    time, where the same account's naira numbers are a hundred times that.
    """
    wallet = build_wallet()
    other_wallet = build_wallet(available="0", currency=Currency.USD)
    service, factory = build_service(tmp_path)
    seed(factory, wallet)
    seed(factory, other_wallet)

    service.deposit(
        wallet.wallet_id,
        Money(Decimal("5000"), NGN),
        internal_reference=str(uuid4()),
    )
    service.deposit(
        other_wallet.wallet_id,
        Money(Decimal("300"), Currency.USD),
        internal_reference=str(uuid4()),
    )

    ledger = service.transactions_for_wallet(wallet.wallet_id)

    assert len(ledger) == 1
    assert ledger[0].amount == Money(Decimal("5000"), NGN)


def test_transactions_for_wallet_of_unknown_wallet_raises(tmp_path):
    service, _ = build_service(tmp_path)

    with pytest.raises(WalletNotFoundError):
        service.transactions_for_wallet(uuid4())


class TestTheReceiptForAWalletCommand:
    """Every wallet operation that crosses the wallet's boundary says so.

    The receipt is queued inside the operation's own transaction, so a command
    that moved money cannot commit without it - see ``WalletService``. "Says so"
    is deliberately not "sends": composing and queueing are writes, and the
    network call happens afterwards, outside the transaction, in the CLI.

    Which operations speak is one dict - ``WalletService.ANNOUNCED`` - and the
    two classes of test below are its two halves. Everything in it queues exactly
    one receipt; everything out of it queues none, and that silence is a decision
    rather than an oversight.

    **Phase 2b added a second mechanism, so that sentence is now half the
    story.** A deposit is the operation in the dict that fires. A withdrawal and
    a payout are also in the dict and are still silent, because ``_announce``
    skips any transaction that is not SUCCESSFUL and both of theirs come back
    PENDING. Two different kinds of quiet, and the tests below keep them apart:
    the lock is quiet *forever* and by design, the withdrawal and the payout are
    quiet *for now*.

    That "for now" is why they stay in the dict rather than being deleted from
    it. Their sentences are already written - ``compose.wallet_movement`` has
    prose for each, in the past tense, because a receipt reports something that
    happened - and Phase 3's settlement is what will make those sentences true.
    Deleting them would throw away a message that is only waiting its turn; the
    guard defers it, and the guard lifts the moment a row can say SUCCESSFUL.
    """

    def test_a_deposit_queues_exactly_one_receipt(self, tmp_path, build_wallet):
        wallet = build_wallet()
        service, factory = build_service(tmp_path, recipient=RECIPIENT)
        seed(factory, wallet)

        transaction = service.deposit(
            wallet.wallet_id, Money(Decimal("5000"), NGN), str(uuid4())
        )

        queued = notifications_of(factory)
        assert len(queued) == 1
        assert queued[0].kind is NotificationKind.WALLET_DEPOSIT
        assert queued[0].subject_id == wallet.wallet_id
        assert queued[0].recipient == RECIPIENT
        assert queued[0].event_key == (
            f"wallet_deposit:wallet:{wallet.wallet_id}:"
            f"{transaction.internal_reference}"
        )

    def test_a_withdrawal_is_silent_while_the_money_is_only_held(
        self, tmp_path, build_wallet
    ):
        """It was ``test_a_withdrawal_queues_exactly_one_receipt`` until 2b.

        Both halves of the change are asserted here, and the second one is the
        one that would be easy to lose. The receipt is suppressed because
        ``compose.wallet_movement`` writes in the past tense - *"Withdrew 3,000.00
        NGN"*, *"3,000.00 NGN left the wallet"* - and at the moment this operation
        returns, nothing has left. Sending it would be an email stating a payment
        that has not happened, which is the same lie the ledger used to tell by
        recording SUCCESSFUL.

        The balance is the other half: refusing to speak must not become refusing
        to act. ``_announce`` runs inside the operation's own transaction, so a
        raise while composing a receipt would roll the withdrawal back and the
        hold would evaporate - silently, since the exception would be caught one
        frame up and re-raised as something the caller could not connect to
        notifications. The money really moved and the row really exists, and the
        row is PENDING, which is the reason for the silence stated as a fact
        rather than as a comment.
        """
        wallet = build_wallet()
        service, factory = build_service(tmp_path, recipient=RECIPIENT)
        seed(factory, wallet)

        transaction = withdraw(
            service, wallet.wallet_id, Money(Decimal("3000"), NGN), str(uuid4())
        )

        assert notifications_of(factory) == []
        assert transaction.status is TransactionStatus.PENDING
        assert (
            get_wallet(factory, wallet.wallet_id).available_balance
            == Money(Decimal("7000"), NGN)
        )

    def test_a_payout_is_silent_while_the_money_is_only_held(
        self, tmp_path, build_wallet
    ):
        """The same silence, and the payout is where it is most visible.

        A payout's receipt names the person who was paid - *"3,000.00 NGN was paid
        to Chinedu Okafor"* - which is a claim about somebody else's bank account.
        That is the sentence this system is least entitled to write early, and the
        one a reader would be most likely to act on.
        """
        wallet = build_wallet(locked="5000")
        service, factory = build_service(tmp_path, recipient=RECIPIENT)
        seed(factory, wallet)

        transaction = payout(
            service,
            wallet.wallet_id,
            Money(Decimal("3000"), NGN),
            DESTINATION,
        )

        assert notifications_of(factory) == []
        assert transaction.status is TransactionStatus.PENDING
        assert (
            get_wallet(factory, wallet.wallet_id).locked_balance
            == Money(Decimal("2000"), NGN)
        )

    def test_the_receipt_reports_the_balance_the_command_left(
        self, tmp_path, build_wallet
    ):
        """Composed from the wallet *after* the operation ran, not before it.

        This is most of the value of the message: "5,000 arrived" is a fact, "you
        now hold 15,000" is the answer to the question the reader opened it with.
        Reading the balance before the money moved would get it exactly backwards
        and still look plausible.
        """
        wallet = build_wallet()
        service, factory = build_service(tmp_path, recipient=RECIPIENT)
        seed(factory, wallet)

        service.deposit(wallet.wallet_id, Money(Decimal("5000"), NGN), str(uuid4()))

        assert "available  15000.00 NGN" in notifications_of(factory)[0].body

    def test_a_lock_says_nothing(self, tmp_path, build_wallet):
        """The operation that is absent on purpose, and the reason it is absent.

        Locking moves money between the wallet's own two balances: nothing the
        owner holds changes, and they typed the command at a terminal that has
        already printed the result. A receipt for that is not information - it is
        a second acknowledgement of something the user did themselves.

        Which pot it went into is the one new fact - and it is not enough to
        change the answer, because the user typed the pot's name in the same
        breath as the amount.
        """
        wallet = build_wallet()
        wallet.open_fund("Vacation", FundKind.PERSONAL)
        service, factory = build_service(tmp_path, recipient=RECIPIENT)
        seed(factory, wallet)

        service.lock_into_fund(
            wallet.wallet_id,
            "Vacation",
            Money(Decimal("3000"), NGN),
            str(uuid4()),
            MOMENT,
        )

        assert notifications_of(factory) == []

    def test_a_release_says_nothing(self, tmp_path, build_wallet):
        wallet = build_wallet(locked="5000")
        service, factory = build_service(tmp_path, recipient=RECIPIENT)
        seed(factory, wallet)

        service.release_from_fund(
            wallet.wallet_id,
            "Locked",
            Money(Decimal("2000"), NGN),
            str(uuid4()),
            MOMENT,
        )

        assert notifications_of(factory) == []

    def test_depositing_into_a_pot_says_something(self, tmp_path, build_wallet):
        """The one new fund operation that is loud, and the rule that says which.

        The notice set is unchanged in *shape*: an operation notifies when it
        moves value across the wallet's boundary, and stays silent when it only
        moves it around inside. Opening, locking, releasing and extending are all
        internal (or move nothing at all), so they are silent; a deposit into a
        pot brings money in from outside, so it speaks - exactly as a plain
        deposit does.
        """
        wallet = build_wallet(available="0")
        wallet.open_fund("Vacation", FundKind.PERSONAL)
        service, factory = build_service(tmp_path, recipient=RECIPIENT)
        seed(factory, wallet)

        service.deposit_into_fund(
            wallet.wallet_id,
            "Vacation",
            Money(Decimal("5000"), NGN),
            str(uuid4()),
            MOMENT,
        )

        queued = notifications_of(factory)
        assert len(queued) == 1
        assert queued[0].kind is NotificationKind.WALLET_DEPOSIT

    def test_opening_a_pot_says_nothing(self, tmp_path, build_wallet):
        """No ledger row either - opening a pot moves no money.

        Pinned because it is easy to assume every new fund command speaks; this
        one has nothing to report and nothing to record.
        """
        wallet = build_wallet()
        service, factory = build_service(tmp_path, recipient=RECIPIENT)
        seed(factory, wallet)

        service.open_fund(wallet.wallet_id, "Vacation", FundKind.PERSONAL)

        assert notifications_of(factory) == []

    def test_a_rejected_operation_says_nothing(self, tmp_path, build_wallet):
        """An error is already the message - it is printed where it happened.

        The rejection path commits a FAILED audit row and re-raises, and the
        receipt is queued after the operation *returns*, so a refusal never
        reaches it. That is the right outcome: the user is looking at the
        terminal, and the failure is the thing they need to read.
        """
        wallet = build_wallet(status=WalletStatus.CLOSED)
        service, factory = build_service(tmp_path, recipient=RECIPIENT)
        seed(factory, wallet)

        with pytest.raises(WalletClosedError):
            service.deposit(
                wallet.wallet_id, Money(Decimal("5000"), NGN), str(uuid4())
            )

        assert notifications_of(factory) == []

    def test_replaying_a_refused_reference_says_nothing_either(
        self, tmp_path, build_wallet
    ):
        """The test above taken one step further, and the step is the whole defect.

        ``test_a_rejected_operation_says_nothing`` pins the *first* call: the
        refusal raises, ``_run`` never reaches ``_announce``, and the user is left
        reading the error. What it did not pin is the retry. The dedupe lookup
        answers with a row whatever its status, so a replay used to be handed the
        FAILED row, ``execute`` returned it normally, and ``_run`` carried on as
        though the deposit had happened - wallets saved, commit reached, and a
        receipt composed. The user is told the deposit succeeded and is emailed
        about it, and the money never arrived.

        Both halves of the fix are asserted here because either one alone leaves
        the defect reachable on this path. The refusal is what stops the replay
        from being reported as an outcome; the silence is what stops it being
        *announced* as one. The balance is the third: nothing moved, on either
        call.
        """
        wallet = build_wallet(status=WalletStatus.CLOSED)
        service, factory = build_service(tmp_path, recipient=RECIPIENT)
        seed(factory, wallet)
        reference = str(uuid4())

        with pytest.raises(WalletClosedError):
            service.deposit(wallet.wallet_id, Money(Decimal("5000"), NGN), reference)

        assert notifications_of(factory) == []

        with pytest.raises(ReferenceAlreadyRefusedError):
            service.deposit(wallet.wallet_id, Money(Decimal("5000"), NGN), reference)

        assert notifications_of(factory) == []
        # Unchanged from what ``build_wallet`` built it with, so the assertion
        # reads as "the deposit did not land" rather than as a magic number.
        assert get_wallet(factory, wallet.wallet_id).available_balance == Money(
            Decimal("10000"), NGN
        )

    def test_with_no_recipient_the_command_happens_and_says_nothing(
        self, tmp_path, build_wallet
    ):
        """The ordinary state of a fresh install, and never a reason to refuse.

        A missing mail address is a reason to be quiet about a payment, not a
        reason to decline one - so the money moves exactly as it otherwise would.
        """
        wallet = build_wallet()
        service, factory = build_service(tmp_path, recipient=None)
        seed(factory, wallet)

        service.deposit(wallet.wallet_id, Money(Decimal("5000"), NGN), str(uuid4()))

        assert notifications_of(factory) == []
        assert (
            get_wallet(factory, wallet.wallet_id).available_balance
            == Money(Decimal("15000"), NGN)
        )

    def test_a_repeated_deposit_is_announced_once(self, tmp_path, build_wallet):
        """The idempotent path, and the reason the message is built from the row.

        Replaying a reference returns the *existing* ledger row and moves no
        money - so the second call composes a receipt for an event that already
        has one. It derives the same key, the insert claims nothing, and one
        deposit stays one email with no check anywhere. See decision 31.
        """
        wallet = build_wallet()
        service, factory = build_service(tmp_path, recipient=RECIPIENT)
        seed(factory, wallet)
        reference = str(uuid4())

        first = service.deposit(
            wallet.wallet_id, Money(Decimal("5000"), NGN), reference
        )
        second = service.deposit(
            wallet.wallet_id, Money(Decimal("5000"), NGN), reference
        )

        assert second.transaction_id == first.transaction_id
        queued = notifications_of(factory)
        assert len(queued) == 1
        assert queued[0].event_key.endswith(reference)

    def test_two_deposits_are_two_receipts(self, tmp_path, build_wallet):
        """The other half of the dedupe: a genuinely different deposit still speaks."""
        wallet = build_wallet()
        service, factory = build_service(tmp_path, recipient=RECIPIENT)
        seed(factory, wallet)

        service.deposit(wallet.wallet_id, Money(Decimal("5000"), NGN), str(uuid4()))
        service.deposit(wallet.wallet_id, Money(Decimal("1000"), NGN), str(uuid4()))

        assert len(notifications_of(factory)) == 2


# --- ownership --------------------------------------------------------------
#
# Every method that takes a wallet id, with a call that would do something
# sensible if the wallet were the service's own. The table exists so the test
# below can sweep the whole surface rather than sample it: a method added later
# and wired to an unscoped read is the one mistake this phase most needs to
# catch, and a hand-written list of calls cannot notice an omission. Adding the
# method here is the tax - and it is a small one, because the entry has to exist
# for the sweep to cover it.
#
# ``open_wallet`` is absent and cannot be here: it takes no wallet id, because a
# service opens a wallet for its own actor and has no way to name anyone else.
#
# The four money-out entries ask for a *confirmation* rather than moving money,
# and that is now the only way to reach those four operations - the methods that
# used to be called bare are private. So the entry named ``request_withdrawal``
# is the whole of the withdrawal surface as far as a caller is concerned, and
# sweeping it is equivalent to the sweep the bare call used to get.
#
# ``confirm`` and ``get_confirmation`` are absent for a different reason, and it
# is not an omission: they take a *confirmation* id rather than a wallet id, so
# they have no entry in a table keyed by wallet. Their scoping is covered in
# ``TestConfirming`` and ``TestRequestingAConfirmation``, one test each.
_REFERENCE = "ref-ownership"


def _money(amount: str) -> Money:
    return Money(Decimal(amount), NGN)


_FOREIGN_CALLS = {
    "deposit": lambda service, wallet_id: service.deposit(
        wallet_id, _money("100"), _REFERENCE
    ),
    "request_withdrawal": lambda service, wallet_id: service.request_confirmation(
        wallet_id,
        ConfirmationKind.WITHDRAWAL,
        MOMENT,
        internal_reference=_REFERENCE,
        amount=_money("100"),
    ),
    "request_payout_from_available": lambda service, wallet_id: (
        service.request_confirmation(
            wallet_id,
            ConfirmationKind.PAYOUT_FROM_AVAILABLE,
            MOMENT,
            internal_reference=_REFERENCE,
            amount=_money("100"),
            destination=DESTINATION,
        )
    ),
    "request_payout_from_locked": lambda service, wallet_id: (
        service.request_confirmation(
            wallet_id,
            ConfirmationKind.PAYOUT_FROM_LOCKED,
            MOMENT,
            internal_reference=_REFERENCE,
            amount=_money("100"),
            destination=DESTINATION,
        )
    ),
    "request_close": lambda service, wallet_id: service.request_confirmation(
        wallet_id, ConfirmationKind.CLOSE, MOMENT, internal_reference=_REFERENCE
    ),
    "open_fund": lambda service, wallet_id: service.open_fund(
        wallet_id, "Holiday", FundKind.PERSONAL
    ),
    "extend_fund": lambda service, wallet_id: service.extend_fund(
        wallet_id, "Holiday", date(2027, 1, 1), MOMENT
    ),
    "deposit_into_fund": lambda service, wallet_id: service.deposit_into_fund(
        wallet_id, "Holiday", _money("100"), _REFERENCE, MOMENT
    ),
    "lock_into_fund": lambda service, wallet_id: service.lock_into_fund(
        wallet_id, "Holiday", _money("100"), _REFERENCE, MOMENT
    ),
    "release_from_fund": lambda service, wallet_id: service.release_from_fund(
        wallet_id, "Holiday", _money("100"), _REFERENCE, MOMENT
    ),
    "funds_for_wallet": lambda service, wallet_id: service.funds_for_wallet(wallet_id),
    "get_wallet": lambda service, wallet_id: service.get_wallet(wallet_id),
    "transactions_for_wallet": lambda service, wallet_id: (
        service.transactions_for_wallet(wallet_id)
    ),
    "freeze_wallet": lambda service, wallet_id: service.freeze_wallet(wallet_id),
    "unfreeze_wallet": lambda service, wallet_id: service.unfreeze_wallet(wallet_id),
}


@pytest.mark.parametrize("method", sorted(_FOREIGN_CALLS))
def test_no_method_can_see_another_users_wallet(
    method, tmp_path, build_wallet, stranger
):
    """Every method on the service, asked for a stranger's wallet, finds nothing.

    Parametrised over the whole surface rather than written as one test per
    method, and the reason is what the sweep is *for*: the failure this phase is
    guarding against is a method that forgets to scope its read, and that failure
    can only enter through a method that did not exist when this was written. A
    new entry in ``_FOREIGN_CALLS`` is a visible omission; a new method with no
    entry is caught by the reviewer reading the table against the class, which is
    a far better place to notice it than in a green suite.

    The wallet is real, seeded, and named by its correct id. What is missing is
    that it belongs to somebody else - so every refusal here is
    ``WalletNotFoundError``: not a permission denied, a wallet that cannot be
    found by this service at all. See ``WalletRepository.get_owned`` for why
    those two must be the same answer.
    """
    wallet = build_wallet(user_id=stranger)
    service, factory = build_service(tmp_path)
    seed(factory, wallet)

    with pytest.raises(WalletNotFoundError):
        _FOREIGN_CALLS[method](service, wallet.wallet_id)


# --- closing a wallet -------------------------------------------------------
#
# Two rules, two owners, and this is the only layer that can ask both. "Is it
# empty?" is the domain's - ``Wallet.close`` answers it from the wallet alone,
# and ``tests/domain/wallet/test_close.py`` covers it. "Is anything still
# promised from it?" needs the plans, so it lives here, and a wallet that is
# empty but still committed is a state only these tests can describe.


def seed_plan(factory, plan):
    """Persist a plan the way the plan service would have created it.

    The file's ``seed`` saves a wallet and nothing else, because until Phase 2b
    no test in it needed a plan in the database. ``close_wallet`` is what changes
    that: it is the first use case here that reads another aggregate, so the
    tests for it need a plan on disk to be refused by - and *only* on disk. A
    plan held in memory would prove nothing, since the check runs inside the
    unit of work against ``uow.plans``.
    """
    uow = factory.start()
    uow.plans.save(plan)
    uow.commit()


class TestClosingAWallet:
    def test_an_empty_wallet_with_nothing_promised_closes(
        self, tmp_path, build_wallet
    ):
        wallet = build_wallet(available="0")
        service, factory = build_service(tmp_path)
        seed(factory, wallet)

        closed = close(service, wallet.wallet_id)

        assert closed.status is WalletStatus.CLOSED
        # Read back rather than trusting the return value: the returned object is
        # the aggregate that was just mutated in memory, and a close that never
        # reached the database would look identical here.
        assert get_wallet(factory, wallet.wallet_id).status is WalletStatus.CLOSED

    def test_money_in_the_available_balance_refuses_the_close(
        self, tmp_path, build_wallet
    ):
        wallet = build_wallet(available="10000")
        service, factory = build_service(tmp_path)
        seed(factory, wallet)

        with pytest.raises(WalletNotEmptyError):
            close(service, wallet.wallet_id)

        assert get_wallet(factory, wallet.wallet_id).status is WalletStatus.ACTIVE

    def test_money_locked_in_a_pot_refuses_the_close(self, tmp_path, build_wallet):
        """The half that a balance-only reading of "empty" would let through.

        The available balance is zero here, so a check written against it alone
        would close this wallet and seal 5000 NGN behind a status nothing can
        leave. That is the failure the rule exists to prevent, and this is the
        test that would catch it.
        """
        wallet = build_wallet(available="0", locked="5000")
        service, factory = build_service(tmp_path)
        seed(factory, wallet)

        with pytest.raises(WalletNotEmptyError):
            close(service, wallet.wallet_id)

    def test_a_live_plan_refuses_the_close_and_says_which(
        self, tmp_path, build_wallet, build_plan
    ):
        """An empty wallet, still committed - the state the domain cannot see.

        The wallet passes every rule ``Wallet.close`` knows about and the close
        still has to be refused, because a plan is due to spend from it. Closed,
        that plan would fail on every tick for the rest of its life, each failure
        recorded in a ``plan_runs`` row nobody reads.

        The name is in the message because "you have an active plan" is not
        actionable for somebody with four of them.
        """
        wallet = build_wallet(available="0")
        plan = build_plan(wallet_id=wallet.wallet_id, name="Rent 2026")
        service, factory = build_service(tmp_path)
        seed(factory, wallet)
        seed_plan(factory, plan)

        with pytest.raises(WalletHasActivePlansError, match="Rent 2026"):
            close(service, wallet.wallet_id)

    def test_a_paused_plan_refuses_the_close_too(self, tmp_path, build_wallet, build_plan):
        """PAUSED is not finished, and the difference is the whole rule.

        A paused plan is waiting for a human and can resume - and a resume that
        found its wallet closed would be that same broken tick, arriving later and
        more confusingly. Non-terminal means ACTIVE *or* PAUSED.
        """
        wallet = build_wallet(available="0")
        plan = build_plan(wallet_id=wallet.wallet_id, status=PlanStatus.PAUSED)
        service, factory = build_service(tmp_path)
        seed(factory, wallet)
        seed_plan(factory, plan)

        with pytest.raises(WalletHasActivePlansError):
            close(service, wallet.wallet_id)

    def test_a_cancelled_plan_does_not_refuse_the_close(
        self, tmp_path, build_wallet, build_plan
    ):
        """The other side of the rule, so it is not read as "any plan at all".

        A cancelled plan will never run again - the money it was going to move is
        the owner's to deal with - so keeping a wallet open for it would mean
        never being able to close one that ever held a plan. And a *completed*
        plan is in the same position, which is why the check names the two live
        states rather than excluding the two dead ones.
        """
        wallet = build_wallet(available="0")
        plan = build_plan(wallet_id=wallet.wallet_id, status=PlanStatus.CANCELLED)
        service, factory = build_service(tmp_path)
        seed(factory, wallet)
        seed_plan(factory, plan)

        assert close(service, wallet.wallet_id).status is WalletStatus.CLOSED

    def test_a_rejected_close_writes_no_ledger_row(self, tmp_path, build_wallet):
        """A status change is not an operation, so there is nothing to audit.

        Note the contrast with the money operations, which *do* write a FAILED row
        when a wallet refuses them: that row is evidence about an instruction
        somebody issued. A close is refused for the state the wallet is in, and
        there is no instruction to keep a record of - so the ledger stays empty
        rather than filling with rows that moved nothing.
        """
        wallet = build_wallet(available="10000")
        service, factory = build_service(tmp_path)
        seed(factory, wallet)

        with pytest.raises(WalletNotEmptyError):
            close(service, wallet.wallet_id)

        assert get_wallet(factory, wallet.wallet_id).status is WalletStatus.ACTIVE

    def test_a_second_close_is_refused_by_the_domain(
        self, tmp_path, build_wallet
    ):
        """``WalletAlreadyClosedError``, which is not the 409 a non-empty wallet gets.

        Both are 409 on the wire, so this is asserted here rather than at the API:
        what differs is the sentence a caller reads and what they would do about
        it - one says "move the money out", the other says "you already did this".
        """
        wallet = build_wallet(available="0")
        service, factory = build_service(tmp_path)
        seed(factory, wallet)
        close(service, wallet.wallet_id)

        with pytest.raises(WalletAlreadyClosedError):
            close(service, wallet.wallet_id)


class TestAnOperationOnAClosedWallet:
    """Closed is not a state money can move in, whichever direction it travels.

    The domain refuses each of these on its own - ``tests/domain/wallet/test_close.py``
    covers that - so what is being checked here is the thing only this layer can
    show: that a wallet closed *through the service*, written to SQLite and read
    back, is still closed, and that the refusal survives the round trip rather
    than being a property of an object that was mutated in memory and never saved.

    Note what is *not* interesting about these two, and why they are nonetheless
    worth writing: a closed wallet is necessarily empty, so the withdrawal below
    would fail on a balance check too if the guard were absent. It does not - the
    status guard is first in ``Wallet.withdraw`` - and that ordering is the
    assertion. "This wallet is closed" is the sentence that tells the owner to
    stop looking at their balance and go ask why their wallet was closed; "not
    enough money" sends them to check a wallet that has no money to check.
    """

    def test_money_going_out_of_a_closed_wallet_is_refused(
        self, tmp_path, build_wallet
    ):
        wallet = build_wallet(available="0")
        service, factory = build_service(tmp_path)
        seed(factory, wallet)
        close(service, wallet.wallet_id)

        with pytest.raises(WalletClosedError):
            withdraw(
                service, wallet.wallet_id, Money(Decimal("5000"), NGN), str(uuid4())
            )

    def test_money_coming_in_to_a_closed_wallet_is_refused(
        self, tmp_path, build_wallet
    ):
        """The direction with no balance check behind it.

        A deposit into a closed wallet has nothing else that could refuse it -
        there is always room for money - so this is the guard carrying the whole
        weight rather than the guard winning a race against another rule.
        """
        wallet = build_wallet(available="0")
        service, factory = build_service(tmp_path)
        seed(factory, wallet)
        close(service, wallet.wallet_id)

        with pytest.raises(WalletClosedError):
            service.deposit(
                wallet.wallet_id, Money(Decimal("5000"), NGN), str(uuid4())
            )

    def test_the_closed_status_is_what_the_database_holds(
        self, tmp_path, build_wallet
    ):
        """The round trip on its own, with no operation in the way.

        Split out from the two above because they would pass if the close had
        been rolled back and the wallet rebuilt CLOSED from the fixture - a
        genuinely closed wallet and one that was merely *built* closed refuse
        identically. This is the one that would notice the difference.
        """
        wallet = build_wallet(available="0")
        service, factory = build_service(tmp_path)
        seed(factory, wallet)

        close(service, wallet.wallet_id)

        assert get_wallet(factory, wallet.wallet_id).status is WalletStatus.CLOSED


# --- the idempotency key, scoped --------------------------------------------
#
# ``get_by_internal_reference`` is a *global* lookup - ``WHERE
# internal_reference = ?`` with no wallet in the predicate - against a globally
# UNIQUE column, and it returns whatever row it finds regardless of whose wallet
# it belongs to. Unreachable while the key was a server-minted uuid4; a
# cross-actor leak the moment a client supplies it. The fix is that the service
# namespaces every key to the wallet it is spent from, and these are the tests
# that say what that buys.


class TestTheIdempotencyKeyIsScoped:
    def test_two_wallets_may_use_the_same_key(self, tmp_path, build_wallet):
        """The collision that made this necessary, at its smallest.

        Two wallets, one key, and both deposits really happen. Before the
        namespacing the second call would have found the first wallet's row and
        returned it - so this wallet would be credited nothing and its owner told
        their deposit succeeded.

        **One of the two is in dollars**, because decision 267 allows one wallet per
        currency and both of these have to accept a deposit - which a closed wallet
        will not. It is a better pair for the claim than two naira ones were: the
        key is scoped to the *wallet*, so two wallets of different currencies
        sharing it is the same question asked in a shape the store cannot confuse
        with "the old wallet, seen again". The amounts differ with the currency,
        because the USD tier's ceilings are the tight ones.
        """
        first_wallet = build_wallet()
        second_wallet = build_wallet(available="0", currency=Currency.USD)
        service, factory = build_service(tmp_path)
        seed(factory, first_wallet)
        seed(factory, second_wallet)

        first = service.deposit(
            first_wallet.wallet_id, Money(Decimal("5000"), NGN), "shared-key"
        )
        second = service.deposit(
            second_wallet.wallet_id, Money(Decimal("300"), Currency.USD), "shared-key"
        )

        assert first.transaction_id != second.transaction_id
        assert get_wallet(factory, first_wallet.wallet_id).available_balance == Money(
            Decimal("15000"), NGN
        )
        assert get_wallet(factory, second_wallet.wallet_id).available_balance == Money(
            Decimal("300"), Currency.USD
        )

    def test_the_same_key_on_one_wallet_still_deduplicates(
        self, tmp_path, build_wallet
    ):
        """The property the fix must not have broken, checked beside it.

        Scoping the key made it *narrower*, not weaker: within one wallet the
        replay is still a replay, which is what the key is for. A fix that had
        scoped by appending something unique per call would pass the test above
        and destroy idempotency entirely.
        """
        wallet = build_wallet()
        service, factory = build_service(tmp_path)
        seed(factory, wallet)

        first = service.deposit(
            wallet.wallet_id, Money(Decimal("5000"), NGN), "shared-key"
        )
        second = service.deposit(
            wallet.wallet_id, Money(Decimal("5000"), NGN), "shared-key"
        )

        assert second.transaction_id == first.transaction_id
        assert get_wallet(factory, wallet.wallet_id).available_balance == Money(
            Decimal("15000"), NGN
        )

    def test_the_stored_key_carries_the_wallet_it_was_spent_from(
        self, tmp_path, build_wallet
    ):
        """The shape on disk, pinned because it is visible to every client.

        The key a response echoes is this one - ``"<wallet uuid>.<their key>"`` -
        which is why ``MovementIn``'s docstring has to tell a caller not to send
        it back: doing so would not match the row it came from, and for a
        withdrawal it would be a second one.

        Pinning the separator here as well as in ``app.domain.money.reference``
        is deliberate. It is not an internal detail; it is in every money
        response, so a change to it is a change to what this API promises, and a
        test that would notice is worth the duplication. This one is the test
        that failed when the separator moved off ``:`` - the character a payment
        provider refuses, which is why it moved.
        """
        wallet = build_wallet()
        service, factory = build_service(tmp_path)
        seed(factory, wallet)

        transaction = service.deposit(
            wallet.wallet_id, Money(Decimal("5000"), NGN), "client-key-7"
        )

        assert transaction.internal_reference == f"{wallet.wallet_id}.client-key-7"
        assert get_transaction(
            factory, f"{wallet.wallet_id}.client-key-7"
        ) is not None

    def test_the_raw_key_alone_finds_nothing(self, tmp_path, build_wallet):
        """Which is the leak closed, stated from the other direction.

        A global lookup for the bare key returns no row, so there is nothing for
        a second actor to be handed. This is the assertion that would have failed
        before the change, and it is the one an attacker's request would make.
        """
        wallet = build_wallet()
        service, factory = build_service(tmp_path)
        seed(factory, wallet)
        service.deposit(wallet.wallet_id, Money(Decimal("5000"), NGN), "client-key-7")

        assert get_transaction(factory, "client-key-7") is None


# --- the two halves of a confirmed movement ---------------------------------
#
# Everything above reaches the money through ``withdraw`` / ``payout`` / ``close``,
# which now record a request and answer it in one go. What those helpers conceal
# is the whole feature, so the two classes below take the halves apart and assert
# what each one does on its own - and, more to the point, what the *first* one
# does not do.
#
# The two facts that matter and cannot be shown from the helpers:
#
#   nothing moves until ``answer``, and the wallet is the proof of it
#   an attempt spends the request, so the same request cannot be answered twice
#
# The second is where the trap found while designing this phase lives. If a
# refused attempt left the request ``AWAITING``, the retry would re-enter the
# ledger under the reference the refusal had already written into a globally
# unique column, be handed the old FAILED row back, move nothing and report
# success. ``test_a_refused_confirm_spends_the_request`` is what keeps that
# unreachable, and it is the single most important test in this file.


class TestRequestingAConfirmation:
    def test_it_records_a_withdrawal_and_moves_nothing(self, tmp_path, build_wallet):
        """The request and the untouched wallet, asserted in the same test.

        Both halves are needed. A request that reported ``AWAITING`` while the
        balance had already come down would pass a test that only read the
        record, and it is exactly the mistake a reader would assume the code
        makes - the two-step shape is only real if the first step is inert.
        """
        wallet = build_wallet()
        service, factory = build_service(tmp_path)
        seed(factory, wallet)

        requested = service.request_confirmation(
            wallet.wallet_id,
            ConfirmationKind.WITHDRAWAL,
            MOMENT,
            internal_reference="ref-1",
            amount=Money(Decimal("3000"), NGN),
        )

        assert requested.created is True
        assert requested.confirmation.status is ConfirmationStatus.AWAITING
        assert requested.confirmation.amount == Money(Decimal("3000"), NGN)
        assert requested.confirmation.expires_at == MOMENT + CONFIRMATION_LIFETIME
        assert (
            get_wallet(factory, wallet.wallet_id).available_balance
            == Money(Decimal("10000"), NGN)
        )
        assert service.transactions_for_wallet(wallet.wallet_id) == []

    def test_a_taken_reference_returns_the_request_it_already_names(
        self, tmp_path, build_wallet
    ):
        """``created`` is a fact about the *call*, not about the row.

        It is what the API turns into 201-versus-200, and it is deliberately not
        a field on the wire - a client reading the body cannot tell the two
        answers apart, because the body is the same request either way. The
        second call's amount is discarded rather than applied, which is the
        assertion worth having: a claim that let the loser win would look exactly
        like one that did not, from the outside.
        """
        wallet = build_wallet()
        service, factory = build_service(tmp_path)
        seed(factory, wallet)

        first = service.request_confirmation(
            wallet.wallet_id,
            ConfirmationKind.WITHDRAWAL,
            MOMENT,
            internal_reference="ref-1",
            amount=Money(Decimal("3000"), NGN),
        )
        second = service.request_confirmation(
            wallet.wallet_id,
            ConfirmationKind.WITHDRAWAL,
            MOMENT,
            internal_reference="ref-1",
            amount=Money(Decimal("9000"), NGN),
        )

        assert second.created is False
        assert second.confirmation.confirmation_id == (
            first.confirmation.confirmation_id
        )
        assert second.confirmation.amount == Money(Decimal("3000"), NGN)

    def test_one_owner_may_use_the_same_reference_on_two_wallets(
        self, tmp_path, build_wallet
    ):
        """The key is scoped to the wallet, and a wallet has one owner.

        Scoping by owner instead would have left one case open: a single person
        posting the same key to two of their own wallets - which is not a retry,
        and a store that answered it with the first request would be handing them
        a confirmation for the wrong wallet.

        **It takes one actor and two wallets to ask the question at all.** With
        two actors holding one wallet each - which is what this test used to do -
        owner-scoping and wallet-scoping give the same answer, so the test passed
        without ever reaching the difference it was named for. The same reference
        under one owner is the only arrangement that tells the two scopings
        apart, and the wallet ids in the two answers are what a store keyed by
        owner would have got wrong.

        **The second wallet is in dollars, because decision 267 leaves no other
        pair.** Two live naira wallets under one owner is exactly what the rule
        refuses, and this test needs two of the same owner's - so the currencies
        differ, which costs the test nothing: no money moves here at all, and the
        request is recorded against whichever wallet it names.
        """
        owner = uuid4()
        own_wallet = build_wallet(user_id=owner)
        other_wallet = build_wallet(user_id=owner, currency=Currency.USD)
        factory = SqliteUnitOfWorkFactory(str(tmp_path / "same_reference.db"))
        seed(factory, own_wallet)
        seed(factory, other_wallet)
        service = as_somebody_else(factory, owner)

        first = service.request_confirmation(
            own_wallet.wallet_id,
            ConfirmationKind.WITHDRAWAL,
            MOMENT,
            internal_reference="rent",
            amount=Money(Decimal("100"), NGN),
        )
        second = service.request_confirmation(
            other_wallet.wallet_id,
            ConfirmationKind.WITHDRAWAL,
            MOMENT,
            internal_reference="rent",
            amount=Money(Decimal("100"), Currency.USD),
        )

        assert first.created is True and second.created is True
        assert first.confirmation.confirmation_id != (
            second.confirmation.confirmation_id
        )
        assert first.confirmation.wallet_id == own_wallet.wallet_id
        assert second.confirmation.wallet_id == other_wallet.wallet_id

    def test_a_request_against_a_stranger_s_wallet_finds_nothing(
        self, tmp_path, build_wallet
    ):
        """Through the one actor-scoped door, so the wallet is a not-found rather
        than a request recorded against somebody else's money.
        """
        wallet = build_wallet(user_id=uuid4())
        service, factory = build_service(tmp_path)
        seed(factory, wallet)

        with pytest.raises(WalletNotFoundError):
            service.request_confirmation(
                wallet.wallet_id,
                ConfirmationKind.WITHDRAWAL,
                MOMENT,
                internal_reference="ref-1",
                amount=Money(Decimal("100"), NGN),
            )

    def test_a_close_request_carries_nothing(self, tmp_path, build_wallet):
        """The kind that names no movement, and the record says so.

        ``None`` here is load-bearing rather than an absent field: a preview that
        rendered a close with an amount would be describing a movement the
        operation does not make.
        """
        wallet = build_wallet()
        service, factory = build_service(tmp_path)
        seed(factory, wallet)

        requested = service.request_confirmation(
            wallet.wallet_id, ConfirmationKind.CLOSE, MOMENT,
            internal_reference="ref-1",
        )

        assert requested.confirmation.amount is None
        assert requested.confirmation.destination is None
        assert get_wallet(factory, wallet.wallet_id).status is WalletStatus.ACTIVE

    def test_a_reference_is_minted_when_the_caller_sends_none(
        self, tmp_path, build_wallet
    ):
        """So a request made without a key is still findable and still unique.

        The alternative - a NULL key - would make the ``UNIQUE`` constraint stop
        applying, and with it the chain that keeps a spent reference from being
        read back as an outcome.
        """
        wallet = build_wallet()
        service, factory = build_service(tmp_path)
        seed(factory, wallet)

        first = service.request_confirmation(
            wallet.wallet_id, ConfirmationKind.CLOSE, MOMENT
        )
        second = service.request_confirmation(
            wallet.wallet_id, ConfirmationKind.CLOSE, MOMENT
        )

        assert first.confirmation.internal_reference
        assert first.confirmation.internal_reference != (
            second.confirmation.internal_reference
        )


class TestConfirming:
    def test_it_moves_the_money_and_comes_back_confirmed(
        self, tmp_path, build_wallet
    ):
        """The debit, the PENDING row, the spent request and the wallet, together.

        ``confirmed.transaction`` is PENDING rather than SUCCESSFUL, which is
        Phase 2b's hold untouched: the confirmation added a level *before* the
        movement rather than changing it, and a test that expected SUCCESSFUL
        here would be asking this phase to settle a bank transfer it has not
        made.
        """
        wallet = build_wallet()
        service, factory = build_service(tmp_path)
        seed(factory, wallet)
        request = a_request(
            service,
            wallet.wallet_id,
            ConfirmationKind.WITHDRAWAL,
            amount=Money(Decimal("3000"), NGN),
        )

        confirmed = answer(service, request)

        assert confirmed.confirmation.status is ConfirmationStatus.CONFIRMED
        assert confirmed.transaction.status is TransactionStatus.PENDING
        assert confirmed.transaction.type is TransactionType.WITHDRAWAL
        assert confirmed.wallet.available_balance == Money(Decimal("7000"), NGN)
        assert (
            get_wallet(factory, wallet.wallet_id).available_balance
            == Money(Decimal("7000"), NGN)
        )

    def test_the_request_records_the_row_it_produced(self, tmp_path, build_wallet):
        """The audit field, and it is set by the same transaction as the movement.

        Read back from the store rather than off the returned object, because the
        returned aggregate was mutated in memory and a ``save`` that never ran
        would look identical.
        """
        wallet = build_wallet()
        service, factory = build_service(tmp_path)
        seed(factory, wallet)
        request = a_request(
            service,
            wallet.wallet_id,
            ConfirmationKind.WITHDRAWAL,
            amount=Money(Decimal("3000"), NGN),
        )

        confirmed = answer(service, request)

        stored = service.get_confirmation(request.confirmation_id, MOMENT)
        assert stored.transaction_id == confirmed.transaction.transaction_id

    def test_a_second_confirm_is_refused_and_moves_nothing_again(
        self, tmp_path, build_wallet
    ):
        """The gate. Without this, "confirm twice" is "withdraw twice".

        The balance is asserted after the refusal as well as before it, because
        the failure this guards against is not a wrong status code - it is money
        leaving twice, and a test that stopped at the exception would not see it.
        """
        wallet = build_wallet()
        service, factory = build_service(tmp_path)
        seed(factory, wallet)
        request = a_request(
            service,
            wallet.wallet_id,
            ConfirmationKind.WITHDRAWAL,
            amount=Money(Decimal("3000"), NGN),
        )
        answer(service, request)

        with pytest.raises(ConfirmationAlreadyUsedError):
            answer(service, request)

        assert (
            get_wallet(factory, wallet.wallet_id).available_balance
            == Money(Decimal("7000"), NGN)
        )
        assert len(service.transactions_for_wallet(wallet.wallet_id)) == 1

    def test_an_expired_request_is_refused_and_moves_nothing(
        self, tmp_path, build_wallet
    ):
        """Fifteen minutes is a window, not a formality.

        The moment is passed in rather than waited for, which is the reason
        ``confirm`` takes ``as_of`` at all - a test that slept would not be run,
        and a test that patched the clock would be testing the patch.
        """
        wallet = build_wallet()
        service, factory = build_service(tmp_path)
        seed(factory, wallet)
        request = a_request(
            service,
            wallet.wallet_id,
            ConfirmationKind.WITHDRAWAL,
            amount=Money(Decimal("3000"), NGN),
        )
        too_late = MOMENT + CONFIRMATION_LIFETIME

        with pytest.raises(ConfirmationExpiredError):
            answer(service, request, too_late)

        assert (
            get_wallet(factory, wallet.wallet_id).available_balance
            == Money(Decimal("10000"), NGN)
        )

    def test_an_expired_request_reads_back_expired(self, tmp_path, build_wallet):
        """And reading it is not what makes it so.

        ``EXPIRED`` is derived by ``status_as_of``, so this ``GET`` writes
        nothing - which is why the *stored* status is still ``AWAITING``
        underneath. A design that stamped the row on read would make a client
        polling its own request a writer.
        """
        wallet = build_wallet()
        service, factory = build_service(tmp_path)
        seed(factory, wallet)
        request = a_request(
            service, wallet.wallet_id, ConfirmationKind.CLOSE, internal_reference="r"
        )

        stored = service.get_confirmation(
            request.confirmation_id, MOMENT + CONFIRMATION_LIFETIME
        )

        assert stored.status is ConfirmationStatus.AWAITING
        assert (
            stored.status_as_of(MOMENT + CONFIRMATION_LIFETIME)
            is ConfirmationStatus.EXPIRED
        )

    def test_another_actor_s_request_is_not_found(self, tmp_path, build_wallet):
        """The whole authorisation for answering a request is knowing its id, so
        this is the most valuable not-found in the API.

        Absent and foreign are one answer, for the reason
        ``WalletRepository.get_owned`` gives - and the request is asserted still
        answerable by its owner afterwards, which is what makes this a refusal
        rather than a side effect.

        The wallet is empty so the owner's confirm genuinely closes it: a witness
        that had to be refused for a *different* reason would prove nothing about
        whose request this is.
        """
        wallet = build_wallet(available="0")
        service, factory = build_service(tmp_path)
        seed(factory, wallet)
        request = a_request(
            service, wallet.wallet_id, ConfirmationKind.CLOSE, internal_reference="r"
        )
        stranger = as_somebody_else(factory, uuid4())

        with pytest.raises(ConfirmationNotFoundError):
            stranger.confirm(request.confirmation_id, MOMENT)

        assert (
            service.confirm(request.confirmation_id, MOMENT).wallet.status
            is WalletStatus.CLOSED
        )

    def test_a_refused_confirm_spends_the_request(self, tmp_path, build_wallet):
        """**The trap this phase was designed around, closed.**

        ``WalletOperation.execute`` deduplicates on ``get_by_internal_reference``,
        which is a global lookup returning the row *whatever its status*. So a
        refused attempt writes a FAILED row into a globally unique column, and if
        the request stayed answerable the retry would be handed that row back,
        move nothing, and report success - and ``_announce`` would compose a
        receipt for a payment that did not happen.

        The wallet is topped up between the two attempts so the retry would
        genuinely succeed if it were allowed to run. That is what makes this a
        test of the spend rather than of the balance: without the top-up the
        second attempt would be refused for insufficient funds and the assertion
        would pass for the wrong reason.
        """
        wallet = build_wallet(available="1000")
        service, factory = build_service(tmp_path)
        seed(factory, wallet)
        request = a_request(
            service,
            wallet.wallet_id,
            ConfirmationKind.WITHDRAWAL,
            amount=Money(Decimal("1001"), NGN),
        )

        with pytest.raises(InsufficientFundsError):
            answer(service, request)
        service.deposit(
            wallet.wallet_id, Money(Decimal("5000"), NGN), internal_reference="top-up"
        )

        with pytest.raises(ConfirmationAlreadyUsedError):
            answer(service, request)

    def test_a_refused_close_does_not_spend_the_request(self, tmp_path, build_wallet):
        """The exception, and it is the rule read correctly rather than bent.

        The rule is not "an attempt spends the request" - it is *the spend is
        committed whenever the refusal is recorded*. A withdrawal writes a FAILED
        row when it refuses and that row is what the request is spent against. A
        close writes nothing at all, so its whole unit rolls back - the claim
        included - and the request stays answerable.

        That is also the right answer for a person: the refusal says "move your
        money out first", and emptying the wallet and answering again is exactly
        what they are going to do next. Spending the request would make them ask
        again for no reason.
        """
        wallet = build_wallet(available="10000")
        service, factory = build_service(tmp_path)
        seed(factory, wallet)
        request = a_request(
            service, wallet.wallet_id, ConfirmationKind.CLOSE, internal_reference="r"
        )

        with pytest.raises(WalletNotEmptyError):
            answer(service, request)
        # Emptied the way a person would have to empty it, and then the *same*
        # request answers - which is the whole claim.
        withdraw(service, wallet.wallet_id, Money(Decimal("10000"), NGN))

        assert answer(service, request).wallet.status is WalletStatus.CLOSED
        assert get_wallet(factory, wallet.wallet_id).status is WalletStatus.CLOSED

    def test_a_close_comes_back_with_no_transaction(self, tmp_path, build_wallet):
        """``None`` means "this operation writes no ledger row".

        Not "the row is unknown" and not "the row is still being made". A client
        that rendered the absent transaction as pending would be describing a
        payment that does not exist.

        The wallet is built empty, because a close over money is refused - and a
        test that had to top it up would be asserting this on a wallet that had
        been emptied by a withdrawal, whose ledger row would then be the thing the
        ``[]`` below found.
        """
        wallet = build_wallet(available="0")
        service, factory = build_service(tmp_path)
        seed(factory, wallet)
        request = a_request(
            service, wallet.wallet_id, ConfirmationKind.CLOSE, internal_reference="r"
        )

        confirmed = answer(service, request)

        assert confirmed.transaction is None
        assert confirmed.wallet.status is WalletStatus.CLOSED
        assert service.transactions_for_wallet(wallet.wallet_id) == []

    def test_a_live_plan_refuses_the_confirm_and_does_not_spend_it(
        self, tmp_path, build_wallet, build_plan
    ):
        """The one close refusal the domain cannot see, reaching ``confirm``.

        ``WalletHasActivePlansError`` is raised by the application layer because
        the domain does not import planning. The request stays answerable for the
        same reason a non-empty wallet's does: nothing was recorded, so there is
        nothing to spend it against.
        """
        wallet = build_wallet(available="0")
        plan = build_plan(wallet_id=wallet.wallet_id, name="Rent 2026")
        service, factory = build_service(tmp_path)
        seed(factory, wallet)
        seed_plan(factory, plan)
        request = a_request(
            service, wallet.wallet_id, ConfirmationKind.CLOSE, internal_reference="r"
        )

        with pytest.raises(WalletHasActivePlansError):
            answer(service, request)

        assert (
            service.get_confirmation(request.confirmation_id, MOMENT).status
            is ConfirmationStatus.AWAITING
        )

    def test_a_frozen_wallet_refuses_the_confirm_and_spends_the_request(
        self, tmp_path, build_wallet
    ):
        """The other asymmetry, side by side with the close above.

        A frozen wallet refuses a withdrawal *inside* the operation, which writes
        a FAILED row before re-raising - so the request is spent. Two refusals,
        two different answers about whether the client must ask again, and the
        difference is exactly whether a ledger row was written.

        Worth its own test rather than being folded into the trap above: the trap
        is about a reference being read back as an outcome, and this is about a
        wallet state that can change between the two calls. A person who freezes
        their wallet and then answers a pending prompt has spent it, and telling
        them otherwise would be the system disagreeing with its own ledger.
        """
        wallet = build_wallet()
        service, factory = build_service(tmp_path)
        seed(factory, wallet)
        request = a_request(
            service,
            wallet.wallet_id,
            ConfirmationKind.WITHDRAWAL,
            amount=Money(Decimal("3000"), NGN),
        )
        service.freeze_wallet(wallet.wallet_id)
        # Read back, so the refusal below cannot be a property of an object that
        # was mutated in memory and never saved.
        assert get_wallet(factory, wallet.wallet_id).status is WalletStatus.FROZEN

        with pytest.raises(WalletFrozenError):
            answer(service, request)

        assert (
            service.get_confirmation(request.confirmation_id, MOMENT).status
            is ConfirmationStatus.CONFIRMED
        )
        assert (
            get_wallet(factory, wallet.wallet_id).available_balance
            == Money(Decimal("10000"), NGN)
        )

    def test_a_payout_from_a_named_pot_spends_the_pot(self, tmp_path, build_wallet):
        """The locked kind, which is the one whose arguments come off the record.

        ``_payout_from_locked`` reads its pot name from the confirmation rather
        than from an argument, so this is also the test that would fail if the
        record stopped carrying it - the failure would be a ``None`` pot name and
        the pooled draw spending a pot nobody named.
        """
        wallet = build_wallet(locked="5000")
        service, factory = build_service(tmp_path)
        seed(factory, wallet)
        request = a_request(
            service,
            wallet.wallet_id,
            ConfirmationKind.PAYOUT_FROM_LOCKED,
            amount=Money(Decimal("3000"), NGN),
            destination=DESTINATION,
            fund_name="Locked",
        )

        confirmed = answer(service, request)

        assert confirmed.transaction.type is TransactionType.PAYOUT
        assert confirmed.transaction.destination == DESTINATION
        assert confirmed.wallet.locked_balance == Money(Decimal("2000"), NGN)

    def test_the_response_is_read_back_rather_than_assembled(
        self, tmp_path, build_wallet
    ):
        """What a caller is told is what was persisted, not a second derivation.

        The position ``ExecutePlanRun._move_the_money`` takes about reading an
        outcome off the rows, applied to a response. Asserted by comparing the
        returned wallet against a fresh read from the store - two objects built
        by different code paths, which is the only way to notice if one of them
        stops agreeing with the other.
        """
        wallet = build_wallet()
        service, factory = build_service(tmp_path)
        seed(factory, wallet)
        request = a_request(
            service,
            wallet.wallet_id,
            ConfirmationKind.WITHDRAWAL,
            amount=Money(Decimal("3000"), NGN),
        )

        confirmed = answer(service, request)

        assert confirmed.wallet == get_wallet(factory, wallet.wallet_id)

