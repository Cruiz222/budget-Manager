"""What an account is allowed to move, refused where the money actually moves.

The domain tests (``tests/domain/identity/test_tier.py``) pin the arithmetic of a
ceiling: which comparison, which boundary, which breach wins. **Nothing in this
file is about arithmetic.** What is tested here is the wiring that turns three
numbers into a control - that the tier comes off the profile the *owner* has
stored, that the day's total comes off the ledger, and that a refusal arrives as
a FAILED row rather than as a stack trace.

That last one is the claim with no counterpart anywhere else, and it is the
reason these tests go through the real ``SqliteUnitOfWorkFactory`` rather than a
double: the guard runs inside ``WalletOperation.execute``'s ``try`` precisely so
that a limit refusal is recorded by the same ``except MoneyError`` every other
refusal is, and a fake unit of work would prove nothing about that - it would be
testing the fake.

**The ceilings below are read from ``LIMITS`` and never typed out.** A test that
hard-coded ``"50000.00"`` would keep passing after somebody raised the ceiling and
would be checking a second copy of the numbers rather than the ones in force -
the one thing a test about a limit must not do.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.wallet_service import WalletService
from app.domain.identity.exception import TierLimitExceededError
from app.domain.identity.limitKind import LimitKind
from app.domain.identity.profile import Profile
from app.domain.identity.tier import Tier, limits_for
from app.domain.money.confirmationKind import ConfirmationKind
from app.domain.money.currency import Currency
from app.domain.money.destination import Destination
from app.domain.money.destinationKind import DestinationKind
from app.domain.money.fundKind import FundKind
from app.domain.money.money import Money
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.transactionType import TransactionType
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)
from tests.conftest import TEST_USER_ID

NGN = Currency.NGN

#: The user every service in this file acts as, and the owner of every wallet
#: ``build_wallet`` builds - see ``test_wallet_service.py``, whose arrangement
#: this copies for the same reason.
ACTOR = TEST_USER_ID

#: Midday, so that a movement stamped here sits inside the limit-day rather than
#: on its lower bound. The bounds below are the day it falls in, written out
#: rather than derived from ``limit_day_bounds``: an assertion built from the
#: function under test could not catch it moving.
MOMENT = datetime(2026, 3, 10, 12, 0, 0)
DAY_START = datetime(2026, 3, 10)
DAY_END = datetime(2026, 3, 11)

LIMITS = limits_for(Tier.UNVERIFIED, NGN)

DESTINATION = Destination(
    kind=DestinationKind.BANK_ACCOUNT,
    identifier="0123456789",
    name="Chinedu Okafor",
    details={"bank_code": "058"},
)


def money(amount: str) -> Money:
    return Money(Decimal(amount), NGN)


def build_service(tmp_path, name="wallet_limits.db"):
    factory = SqliteUnitOfWorkFactory(str(tmp_path / name))
    return WalletService(factory, actor=ACTOR), factory


def seed(factory, wallet):
    uow = factory.start()
    uow.wallets.save(wallet)
    uow.commit()


def store_profile(factory, **overrides):
    """Give the actor a profile - complete unless a field is knocked out.

    Written straight to the repository rather than through ``ProfileService``,
    and the reason is the one that keeps this file about the wallet: the service
    has its own tests and its own argument for what a save does. What matters
    here is only the state it leaves behind - a row, or no row.
    """
    fields = dict(
        display_name="Ada",
        legal_first_name="Adaeze",
        legal_last_name="Okafor",
        date_of_birth=date(1990, 5, 17),
        phone="+2348000000000",
        country="NG",
        address_line="12 Marina Road, Lagos",
    )
    fields.update(overrides)
    uow = factory.start()
    uow.profiles.save(
        Profile(user_id=ACTOR, created_at=MOMENT, updated_at=MOMENT, **fields)
    )
    uow.commit()


def outflow_today(factory, wallet_id, currency=NGN):
    """The wallet's outflow over the limit-day, read the way the guard reads it."""
    uow = factory.start()
    try:
        return uow.transactions.outflow_total_between(
            wallet_id, DAY_START, DAY_END, currency
        )
    finally:
        uow.rollback()


def stored_wallet(factory, wallet_id):
    uow = factory.start()
    try:
        return uow.wallets.get_owned(wallet_id, ACTOR)
    finally:
        uow.rollback()


def straddle(service, wallet_id, amount, kind=ConfirmationKind.WITHDRAWAL, **extra):
    """Record and answer a movement out, in the two calls the feature requires.

    ``**extra`` carries what a kind needs beyond its amount - a payout's
    destination, a pot's name - so that the three kinds below are one line each
    in the tests rather than three near-identical helpers.
    """
    request = service.request_confirmation(
        wallet_id, kind, MOMENT, amount=amount, **extra
    ).confirmation
    return service.confirm(request.confirmation_id, MOMENT)


def withdraw(service, wallet_id, amount):
    """The shape most of the tests below want: a withdrawal, recorded and answered."""
    return straddle(service, wallet_id, amount).transaction


def failed_row(factory, wallet_id):
    """This wallet's FAILED ledger rows, oldest first.

    Read through the port rather than by id, because the reference is minted by
    the service when a caller sends none - which is what ``request_confirmation``
    does - so there is no key for a test to look one up by.
    """
    uow = factory.start()
    try:
        return [
            row
            for row in uow.transactions.get_by_wallet_id(wallet_id)
            if row.status is TransactionStatus.FAILED
        ]
    finally:
        uow.rollback()


# --- the tier comes off the profile -----------------------------------------


def test_an_account_with_no_profile_moves_money_at_the_unverified_ceiling(
    tmp_path, build_wallet
):
    """**The ordinary state of every account on rollout**, and not an error.

    There is no profile row for this account at all, which is what every account
    that existed before this feature has. ``tier_for(None)`` answers
    ``UNVERIFIED`` rather than raising, so the movement is judged against the
    unverified ceilings and *succeeds* at exactly the ceiling - which is the pair
    of assertions that distinguishes this from an implementation that refused
    everything it could not identify.
    """
    service, factory = build_service(tmp_path)
    wallet = build_wallet(available="60000")
    seed(factory, wallet)

    withdraw(service, wallet.wallet_id, LIMITS.per_transaction)

    assert stored_wallet(factory, wallet.wallet_id).available_balance == money(
        "10000.00"
    )


def test_a_complete_profile_raises_the_ceiling_the_same_movement_meets(
    tmp_path, build_wallet
):
    """**The feature in one test**, and it is written as a pair for that reason.

    The same withdrawal, one row different. Unverified it is refused, because
    60,000 is over the 50,000 a single movement may be; with a complete profile
    stored it passes, because the identified ceiling is ten times that. Asserting
    only the refusal would pass for a system with no tiers at all, and asserting
    only the success would pass for a system with no limits at all.

    The wallet holds exactly the unverified balance cap, so nothing here is a
    wallet that only the fixture could have built.
    """
    service, factory = build_service(tmp_path)
    wallet = build_wallet(available="300000")
    seed(factory, wallet)
    amount = LIMITS.per_transaction + money("10000.00")

    with pytest.raises(TierLimitExceededError) as refused:
        withdraw(service, wallet.wallet_id, amount)

    assert refused.value.kind is LimitKind.PER_TRANSACTION

    store_profile(factory)

    withdraw(service, wallet.wallet_id, amount)

    assert stored_wallet(factory, wallet.wallet_id).available_balance == money(
        "240000.00"
    )


def test_an_incomplete_profile_leaves_the_account_where_it_was(
    tmp_path, build_wallet
):
    """It is *completeness* that raises the tier, not the existence of a row.

    Four fields are what an identity check would confirm, and a profile missing
    one of them is a person mid-form rather than a person identified. So the row
    here exists and is not empty, and the ceiling does not move - which is the
    assertion a check for "has a profile" instead of "has a complete one" would
    fail.
    """
    service, factory = build_service(tmp_path)
    wallet = build_wallet(available="300000")
    seed(factory, wallet)
    store_profile(factory, country=None)

    with pytest.raises(TierLimitExceededError) as refused:
        withdraw(
            service, wallet.wallet_id, LIMITS.per_transaction + money("10000.00")
        )

    assert refused.value.kind is LimitKind.PER_TRANSACTION


# --- a refusal is a ledger row ----------------------------------------------


def test_a_movement_over_the_transaction_ceiling_is_refused_and_recorded(
    tmp_path, build_wallet
):
    """**The claim that needs the real unit of work**, and the one worth reading.

    Three things at once, and each would fail on its own for a different design:

    - the error is ``TierLimitExceededError`` carrying ``PER_TRANSACTION``, so a
      caller can act on *which* ceiling rather than on prose;
    - the ledger holds a FAILED row, so "how many movements did this ceiling turn
      away?" is answered from the rows and not from a log line - which is the
      whole reason the check runs inside ``execute``'s ``try`` rather than one
      layer up, where a refusal would leave nothing behind;
    - the balance is untouched, because the guard runs before ``_apply`` and a
      refusal is not half a movement.

    The FAILED row is what a caller cannot see. It is the audit half of the
    control, and it is the half that would be missing silently.
    """
    service, factory = build_service(tmp_path)
    wallet = build_wallet(available="60000")
    seed(factory, wallet)
    over = LIMITS.per_transaction + money("0.01")

    with pytest.raises(TierLimitExceededError) as refused:
        withdraw(service, wallet.wallet_id, over)

    assert refused.value.kind is LimitKind.PER_TRANSACTION
    assert refused.value.attempted == over
    assert refused.value.limit == LIMITS.per_transaction

    rows = failed_row(factory, wallet.wallet_id)
    assert [row.type for row in rows] == [TransactionType.WITHDRAWAL]
    assert [row.amount for row in rows] == [over]
    assert stored_wallet(factory, wallet.wallet_id).available_balance == money(
        "60000.00"
    )


def test_a_payout_faces_the_same_ceiling_as_a_withdrawal(tmp_path, build_wallet):
    """Both members of ``OUTBOUND_TYPES``, because the type decides the check.

    A withdrawal and a payout take value across the same edge, so both are
    judged against the per-transaction ceiling - and a classification that named
    only one of them would leave the other with no ceiling at all, silently, since
    the daily total would still count it. That asymmetry is invisible from the
    outside, which is why it is asserted rather than assumed.
    """
    service, factory = build_service(tmp_path)
    wallet = build_wallet(available="60000")
    seed(factory, wallet)

    with pytest.raises(TierLimitExceededError) as refused:
        straddle(
            service,
            wallet.wallet_id,
            LIMITS.per_transaction + money("0.01"),
            kind=ConfirmationKind.PAYOUT_FROM_AVAILABLE,
            destination=DESTINATION,
        )

    assert refused.value.kind is LimitKind.PER_TRANSACTION
    assert [row.type for row in failed_row(factory, wallet.wallet_id)] == [
        TransactionType.PAYOUT
    ]


# --- the day's total --------------------------------------------------------


def test_the_twentieth_movement_lands_on_the_cap_and_the_twenty_first_crosses_it(
    tmp_path, build_wallet
):
    """**Two boundaries in one test, because the second is only meaningful with the first.**

    Ten thousand at a time against a two-hundred-thousand day: the twentieth
    movement lands *exactly* on the cap and is allowed - ``check_outflow``
    compares with ``>``, so an allowance of 200,000 is worth 200,000 - and the
    twenty-first would make it 210,000 and is refused. An implementation that
    refused at the boundary would fail on the twentieth; one that never counted
    the held rows would fail on the twenty-first.

    The refusal is asserted to be for the *limit* and not for insufficient funds,
    which is not padding: the wallet still holds the 10,000 this movement needs,
    so the two refusals differ only in their class and a client cannot act on the
    difference. And the day's total is re-read afterwards to show it stayed *at*
    the cap - a refused movement that counted toward the day would spend the
    allowance it was refused for.
    """
    service, factory = build_service(tmp_path)
    wallet = build_wallet(available="210000")
    seed(factory, wallet)

    for _ in range(20):
        withdraw(service, wallet.wallet_id, money("10000.00"))

    assert outflow_today(factory, wallet.wallet_id) == money("200000.00")

    with pytest.raises(TierLimitExceededError) as refused:
        withdraw(service, wallet.wallet_id, money("10000.00"))

    assert refused.value.kind is LimitKind.DAILY_OUTFLOW
    assert outflow_today(factory, wallet.wallet_id) == money("200000.00")


def test_a_held_movement_counts_toward_the_day(tmp_path, build_wallet):
    """A withdrawal is PENDING, and a cap that ignored held money is not a cap.

    A payout has left the wallet and has not been confirmed by anybody outside
    this system - which is why the row stays PENDING and the balance is already
    debited. A ceiling that counted only settled movements would let a burst of
    in-flight payouts each pass on its own, and the burst is the case the daily
    cap exists for. See ``TransactionRepository.outflow_total_between``.
    """
    service, factory = build_service(tmp_path)
    wallet = build_wallet(available="600000")
    seed(factory, wallet)

    transaction = withdraw(service, wallet.wallet_id, money("10000.00"))

    assert transaction.status is TransactionStatus.PENDING
    assert outflow_today(factory, wallet.wallet_id) == money("10000.00")


def test_moving_money_into_a_pot_is_not_an_outflow(tmp_path, build_wallet):
    """**Moving money into a pot is not spending it**, and the amount proves it.

    The locked amount is chosen above the per-transaction ceiling, so an
    implementation that judged ``LOCK_FUNDS`` as a movement out would refuse this
    - and a test written with a small amount would pass under either reading.
    Nothing left the wallet: the available balance fell, the pot rose by the same
    amount, and the day's outflow total is still zero.
    """
    service, factory = build_service(tmp_path)
    wallet = build_wallet(available="300000")
    seed(factory, wallet)
    service.open_fund(wallet.wallet_id, "Vacation", FundKind.PERSONAL, as_of=MOMENT)
    amount = LIMITS.per_transaction + money("10000.00")

    service.lock_into_fund(wallet.wallet_id, "Vacation", amount, str(uuid4()), MOMENT)

    held = stored_wallet(factory, wallet.wallet_id)
    assert held.available_balance == money("240000.00")
    assert held.locked_balance == amount
    assert outflow_today(factory, wallet.wallet_id) == money("0.00")


def test_releasing_money_back_out_of_a_pot_is_not_an_outflow_either(
    tmp_path, build_wallet
):
    """The other internal member, which is the one that *looks* like an inflow.

    ``UNLOCK_FUNDS`` moves value from a pot to the available balance, so a
    classification written from the direction of the *balance* rather than from
    the edge of the system would call it a credit and judge it against the balance
    ceiling - which would refuse to give somebody their own money back on a wallet
    sitting at its cap. Nothing crosses the edge in either direction and neither
    internal type is classified; this is the test that says so for the second one.
    """
    service, factory = build_service(tmp_path)
    wallet = build_wallet(available="10000", locked="60000")
    seed(factory, wallet)

    service.release_from_fund(
        wallet.wallet_id, "Locked", money("60000.00"), str(uuid4()), MOMENT
    )

    held = stored_wallet(factory, wallet.wallet_id)
    assert held.available_balance == money("70000.00")
    assert held.locked_balance == money("0.00")
    assert outflow_today(factory, wallet.wallet_id) == money("0.00")


# --- the balance ceiling, which is the one that faces money arriving ---------


def test_a_deposit_that_would_cross_the_balance_ceiling_is_refused(
    tmp_path, build_wallet
):
    """The boundary and the refusal, on the side that faces inbound value.

    Two deposits into a wallet that is already near its cap: the first lands the
    wallet *exactly* on the balance ceiling and is allowed, and a single unit more
    is refused as ``MAX_BALANCE``. That pairing is the whole of the credit rule -
    the balance the wallet *would* hold, compared with ``>`` - and it is asserted
    through the store because the ceiling depends on the profile, which is a fact
    this layer supplies and the domain cannot check for itself.

    The FAILED row is checked here too, and matters more than it looks: a deposit
    is the one operation whose refusal happens *before* the payer has paid
    anything, so a refusal that left no trace would be an attempted deposit no
    accounting could find.
    """
    service, factory = build_service(tmp_path)
    wallet = build_wallet(available="290000")
    seed(factory, wallet)

    service.deposit(wallet.wallet_id, money("10000.00"), str(uuid4()))

    assert stored_wallet(factory, wallet.wallet_id).available_balance == money(
        "300000.00"
    )

    with pytest.raises(TierLimitExceededError) as refused:
        service.deposit(wallet.wallet_id, money("0.01"), str(uuid4()))

    assert refused.value.kind is LimitKind.MAX_BALANCE
    assert refused.value.attempted == money("300000.01")
    assert [row.type for row in failed_row(factory, wallet.wallet_id)] == [
        TransactionType.DEPOSIT
    ]
    assert stored_wallet(factory, wallet.wallet_id).available_balance == money(
        "300000.00"
    )


def test_a_pot_is_part_of_what_the_wallet_holds(tmp_path, build_wallet):
    """**A credit is judged against available *and* locked**, and this is why.

    The money in a pot is money the account still holds, so a ceiling on what a
    wallet may hold has to count it - otherwise the cap is a cap on one balance
    and a person moves past it by opening a pot. The fixture starts this wallet at
    the unverified ceiling with everything available, which makes the first
    deposit into the pot the one that crosses.
    """
    service, factory = build_service(tmp_path)
    wallet = build_wallet(available="300000")
    seed(factory, wallet)
    service.open_fund(wallet.wallet_id, "Vacation", FundKind.PERSONAL, as_of=MOMENT)

    with pytest.raises(TierLimitExceededError) as refused:
        service.deposit_into_fund(
            wallet.wallet_id, "Vacation", money("0.01"), str(uuid4()), MOMENT
        )

    assert refused.value.kind is LimitKind.MAX_BALANCE
    assert refused.value.attempted == money("300000.01")


def test_the_balance_ceiling_reads_the_balance_the_credit_would_leave(
    tmp_path, build_wallet
):
    """The other half of the same claim, with the pot already holding the money.

    A wallet with 290,000 available and 10,000 in a pot holds exactly its cap, so
    the next unit is refused *although the available balance is far below it*. An
    implementation that read ``available_balance`` alone would let this through -
    and would let the same wallet be filled to double its cap, one balance at a
    time.
    """
    service, factory = build_service(tmp_path)
    wallet = build_wallet(available="290000", locked="10000")
    seed(factory, wallet)

    with pytest.raises(TierLimitExceededError) as refused:
        service.deposit(wallet.wallet_id, money("0.01"), str(uuid4()))

    assert refused.value.kind is LimitKind.MAX_BALANCE
    assert refused.value.attempted == money("300000.01")

