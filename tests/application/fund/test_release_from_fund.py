"""``ReleaseFromFund``: pot money moved back into the available balance.

The pot-scoped successor to the old ``ReleaseFunds`` tests, and the file where
the phase's central rule meets the ledger: a release of a sealed pot is refused,
and the refusal is *recorded*. That combination is worth reading carefully,
because the two refusals this operation can produce look similar at the call
site and are not similar at all.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.fund.release_from_fund import ReleaseFromFund
from app.domain.money.currency import Currency
from app.domain.money.exception import (
    CurrencyMismatchError,
    FundNotFoundError,
    FundNotMaturedError,
    InsufficientFundsError,
    InvalidAmountError,
    WalletClosedError,
)
from app.domain.money.fundKind import FundKind
from app.domain.money.money import Money
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.transactionType import TransactionType
from app.domain.money.walletStatus import WalletStatus
from app.infrastructure.repositories.in_memory_transaction_repository import (
    InMemoryTransactionRepository,
)

NGN = Currency.NGN
USD = Currency.USD

#: Any moment after the pots built without a date. Deliberately not "now": no
#: test in this file reads a clock.
MOMENT = datetime(2026, 1, 1)

#: The due date the sealed pots in this file are sealed until.
DUE = date(2026, 6, 1)


def a_wallet_with_a_pot(
    build_wallet,
    balance="5000",
    maturity_date=None,
    status=WalletStatus.ACTIVE,
    **kwargs,
):
    """A wallet holding one pot with money in it.

    Deposits into the pot rather than locking - one move instead of two, and the
    interest here is what happens on the way *out*.

    The status is applied last, because ``open_fund`` refuses a closed wallet.
    """
    wallet = build_wallet(**kwargs)
    pot = wallet.open_fund("Vacation", FundKind.PERSONAL, maturity_date=maturity_date)
    if Decimal(balance) != 0:
        wallet.deposit_into_fund(pot.fund_id, Money(Decimal(balance), NGN))
    wallet.status = status
    return wallet


def ngn(amount: str) -> Money:
    return Money(Decimal(amount), NGN)


# --- Successful release ---

def test_a_release_moves_pot_money_back_to_available(build_wallet):
    wallet = a_wallet_with_a_pot(build_wallet, balance="5000", available="1000")
    repository = InMemoryTransactionRepository()

    transaction = ReleaseFromFund(wallet, repository, "Vacation", MOMENT).execute(
        ngn("2000"),
        internal_reference=str(uuid4()),
    )

    assert wallet.available_balance == ngn("3000")
    assert wallet.fund_by_name("Vacation").balance == ngn("3000")
    # The wallet still holds the same total. That is what separates a release
    # from a payout, and it is why a frozen wallet is allowed to do this one.
    assert wallet.available_balance + wallet.locked_balance == ngn("6000")

    stored = repository.get_by_id(transaction.transaction_id)
    assert stored.status is TransactionStatus.SUCCESSFUL
    assert stored.type is TransactionType.UNLOCK_FUNDS
    assert stored.fund_id == wallet.fund_by_name("Vacation").fund_id
    assert stored.completed_at is not None


def test_a_release_is_persisted_as_pending_before_the_wallet_is_touched(
    build_wallet, recording_transactions
):
    wallet = a_wallet_with_a_pot(build_wallet)

    ReleaseFromFund(wallet, recording_transactions, "Vacation", MOMENT).execute(
        ngn("1000"), internal_reference=str(uuid4())
    )

    assert recording_transactions.saved_statuses == [
        TransactionStatus.PENDING,
        TransactionStatus.SUCCESSFUL,
    ]


def test_a_frozen_wallet_can_still_be_released_from(build_wallet):
    wallet = a_wallet_with_a_pot(
        build_wallet, available="1000", status=WalletStatus.FROZEN
    )

    ReleaseFromFund(wallet, InMemoryTransactionRepository(), "Vacation", MOMENT).execute(
        ngn("2000"), internal_reference=str(uuid4())
    )

    assert wallet.available_balance == ngn("3000")


# --- Refusals the wallet makes, which the ledger remembers ---

def test_releasing_more_than_the_pot_holds_fails_and_is_recorded(build_wallet):
    wallet = a_wallet_with_a_pot(build_wallet, balance="5000")
    repository = InMemoryTransactionRepository()

    with pytest.raises(InsufficientFundsError):
        ReleaseFromFund(wallet, repository, "Vacation", MOMENT).execute(
            ngn("5001"),
            internal_reference=str(uuid4()),
        )

    assert wallet.available_balance == ngn("10000")
    assert wallet.fund_by_name("Vacation").balance == ngn("5000")

    stored = list(repository.transactions.values())[0]
    assert stored.status is TransactionStatus.FAILED


def test_releasing_the_wrong_currency_fails_and_is_recorded(build_wallet):
    wallet = a_wallet_with_a_pot(build_wallet, balance="5000")
    repository = InMemoryTransactionRepository()

    with pytest.raises(CurrencyMismatchError):
        ReleaseFromFund(wallet, repository, "Vacation", MOMENT).execute(
            Money(Decimal("1000"), USD),
            internal_reference=str(uuid4()),
        )

    stored = list(repository.transactions.values())[0]
    assert stored.status is TransactionStatus.FAILED


def test_releasing_from_a_closed_wallet_fails_and_is_recorded(build_wallet):
    wallet = a_wallet_with_a_pot(build_wallet, status=WalletStatus.CLOSED)
    repository = InMemoryTransactionRepository()

    with pytest.raises(WalletClosedError):
        ReleaseFromFund(wallet, repository, "Vacation", MOMENT).execute(
            ngn("1000"), internal_reference=str(uuid4())
        )

    stored = list(repository.transactions.values())[0]
    assert stored.status is TransactionStatus.FAILED


@pytest.mark.parametrize("amount", ["0", "-1000"])
def test_a_non_positive_release_fails_and_persists_nothing(build_wallet, amount):
    """Rejected by the base class before any row exists - unlike the cases above.

    An amount that is not an amount is not an attempt to move money, so there is
    nothing to remember. The distinction runs right through this file:
    command-level refusals persist nothing, wallet-level refusals persist FAILED.
    """
    wallet = a_wallet_with_a_pot(build_wallet, balance="5000")
    repository = InMemoryTransactionRepository()

    with pytest.raises(InvalidAmountError):
        ReleaseFromFund(wallet, repository, "Vacation", MOMENT).execute(
            ngn(amount), internal_reference=str(uuid4())
        )

    assert not repository.transactions


# --- The maturity rule, which is what this whole phase is for ---

def test_a_sealed_pot_refuses_a_release_and_the_attempt_is_recorded(build_wallet):
    """The rule, at the application layer.

    Two claims at once, and they are why this test exists rather than the domain
    one alone: the release is refused, **and** the refusal is on the ledger as a
    FAILED row. Somebody tried to take money out of a pot before its date, and
    the wallet remembers - which is a different thing from merely preventing it.
    """
    wallet = a_wallet_with_a_pot(build_wallet, balance="5000", maturity_date=DUE)
    repository = InMemoryTransactionRepository()

    with pytest.raises(FundNotMaturedError):
        ReleaseFromFund(wallet, repository, "Vacation", MOMENT).execute(
            ngn("1000"),
            internal_reference=str(uuid4()),
        )

    assert wallet.available_balance == ngn("10000")
    assert wallet.fund_by_name("Vacation").balance == ngn("5000")

    stored = list(repository.transactions.values())[0]
    assert stored.status is TransactionStatus.FAILED
    assert stored.type is TransactionType.UNLOCK_FUNDS


def test_the_same_pot_releases_once_it_has_come_due(build_wallet):
    """The other half of the promise, through the same operation.

    A pot that refused forever would satisfy the test above and be useless, so
    the identical call is made again on the due date itself and is expected to
    work. Only ``as_of`` differs between the two.
    """
    wallet = a_wallet_with_a_pot(build_wallet, balance="5000", maturity_date=DUE)
    repository = InMemoryTransactionRepository()

    with pytest.raises(FundNotMaturedError):
        ReleaseFromFund(wallet, repository, "Vacation", datetime(2026, 5, 31, 23, 59)).execute(
            ngn("1000"), internal_reference=str(uuid4())
        )

    transaction = ReleaseFromFund(
        wallet, repository, "Vacation", datetime(2026, 6, 1, 0, 0)
    ).execute(ngn("1000"), internal_reference=str(uuid4()))

    assert wallet.available_balance == ngn("11000")
    assert wallet.fund_by_name("Vacation").balance == ngn("4000")
    assert transaction.status is TransactionStatus.SUCCESSFUL


def test_a_failed_release_can_be_retried_and_the_retry_succeeds(build_wallet):
    """The refusal mutated nothing, so trying again is safe.

    ``WalletOperation`` records the failure before re-raising and leaves the
    wallet exactly as it found it - so a caller who fixes whatever was wrong
    (here, waiting) can simply call again with a *fresh* internal reference. The
    new reference matters and is the point of the test: replaying the same one
    would return the FAILED row instead of running, because the base class treats
    a seen reference as already processed.
    """
    wallet = a_wallet_with_a_pot(build_wallet, balance="5000", maturity_date=DUE)
    repository = InMemoryTransactionRepository()

    with pytest.raises(FundNotMaturedError):
        ReleaseFromFund(wallet, repository, "Vacation", MOMENT).execute(
            ngn("5000"), internal_reference="attempt-1"
        )

    ReleaseFromFund(
        wallet, repository, "Vacation", datetime(2026, 6, 1, 12, 0)
    ).execute(ngn("5000"), internal_reference="attempt-2")

    assert wallet.fund_by_name("Vacation").balance == ngn("0")
    assert wallet.available_balance == ngn("15000")


# --- The pot the operation was pointed at ---

def test_releasing_an_unknown_pot_is_refused_before_anything_is_recorded(
    build_wallet,
):
    """No ledger row at all - and the contrast with the sealed-pot case above is
    the whole reason this test is here.

    A pot that does not exist is refused in the constructor, before the base
    class has recorded anything: nothing was attempted, so nothing claims to
    have been. A pot that exists and is sealed is refused *inside* the attempt,
    which is why that one leaves a FAILED row. Two refusals, two ledgers.
    """
    wallet = a_wallet_with_a_pot(build_wallet)
    repository = InMemoryTransactionRepository()

    with pytest.raises(FundNotFoundError):
        ReleaseFromFund(wallet, repository, "Holiday", MOMENT)

    assert not repository.transactions


def test_a_release_drains_the_named_pot_and_not_another(build_wallet):
    wallet = build_wallet(available="0")
    vacation = wallet.open_fund("Vacation", FundKind.PERSONAL)
    salary = wallet.open_fund("Salary", FundKind.PERSONAL)
    wallet.deposit_into_fund(vacation.fund_id, ngn("3000"))
    wallet.deposit_into_fund(salary.fund_id, ngn("3000"))

    ReleaseFromFund(wallet, InMemoryTransactionRepository(), "Salary", MOMENT).execute(
        ngn("3000"), internal_reference=str(uuid4())
    )

    assert salary.balance == ngn("0")
    assert vacation.balance == ngn("3000")
    assert wallet.available_balance == ngn("3000")


def test_replaying_the_same_internal_reference_releases_only_once(build_wallet):
    wallet = a_wallet_with_a_pot(build_wallet, balance="5000")
    repository = InMemoryTransactionRepository()
    service = ReleaseFromFund(wallet, repository, "Vacation", MOMENT)
    reference = str(uuid4())

    first = service.execute(ngn("1000"), reference)
    second = service.execute(ngn("1000"), reference)

    assert second.transaction_id == first.transaction_id
    assert wallet.fund_by_name("Vacation").balance == ngn("4000")
