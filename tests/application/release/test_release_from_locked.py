"""``ReleaseFromLocked``: the pooled release, which a plan reaches.

The narrow successor to the old ``ReleaseFunds`` tests. It differs from them in
one way that matters and one that does not. It does not matter that it now
reasons about maturity - that is covered next door in the domain tests. It does
matter that there is now a *second* release operation, so the cases here are
chosen to be the ones that only the pooled one can reach: no pot is named, more
than one pot may pay, and the ledger row says nothing about which.

Everything about amount validation, the PENDING/FAILED trail and idempotency is
``WalletOperation``'s and is tested in ``tests/application/fund/``.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.release.release_from_locked import ReleaseFromLocked
from app.domain.money.currency import Currency
from app.domain.money.exception import (
    CurrencyMismatchError,
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

#: Open at every moment the fixture's pots care about - the fixture's pot has no
#: maturity date. Deliberately not "now": no test here reads a clock.
MOMENT = datetime(2026, 1, 1)


def ngn(amount: str) -> Money:
    return Money(Decimal(amount), NGN)


def test_a_pooled_release_moves_locked_money_to_available(build_wallet):
    wallet = build_wallet(available="1000", locked="5000")
    repository = InMemoryTransactionRepository()

    transaction = ReleaseFromLocked(wallet, repository, MOMENT).execute(
        ngn("2000"),
        internal_reference=str(uuid4()),
    )

    assert wallet.available_balance == ngn("3000")
    assert wallet.locked_balance == ngn("3000")

    stored = repository.get_by_id(transaction.transaction_id)
    assert stored.status is TransactionStatus.SUCCESSFUL
    assert stored.type is TransactionType.UNLOCK_FUNDS


def test_the_ledger_row_names_no_pot(build_wallet):
    """A deliberate omission, and worth an assertion rather than a comment.

    This operation cannot say which pot it drew from, because the caller did not
    say and more than one pot may have paid. ``Wallet._draw_from_matured`` splits
    the amount across as many as it needs, so any single ``fund_id`` here would
    be a guess recorded as a fact. The next phase makes a plan name its pot, and
    *that* is when the column starts being written on these rows.
    """
    wallet = build_wallet(available="0", locked="5000")

    transaction = ReleaseFromLocked(
        wallet, InMemoryTransactionRepository(), MOMENT
    ).execute(ngn("2000"), internal_reference=str(uuid4()))

    assert transaction.fund_id is None


def test_a_pooled_release_draws_across_several_pots_when_it_has_to(build_wallet):
    """The only behaviour here a pot-scoped release cannot express.

    Two pots, neither big enough for the whole amount, and the wallet covers it
    from both - oldest first. ``fund release`` could not do this: it names one
    pot and either that pot covers the amount or the call fails.
    """
    wallet = build_wallet(available="0")
    older = wallet.open_fund("Rent", FundKind.PERSONAL)
    newer = wallet.open_fund("Car", FundKind.PERSONAL)
    wallet.deposit_into_fund(older.fund_id, ngn("3000"))
    wallet.deposit_into_fund(newer.fund_id, ngn("3000"))

    ReleaseFromLocked(wallet, InMemoryTransactionRepository(), MOMENT).execute(
        ngn("4000"), internal_reference=str(uuid4())
    )

    assert older.balance == ngn("0")
    assert newer.balance == ngn("2000")
    assert wallet.available_balance == ngn("4000")


def test_a_pooled_release_cannot_reach_into_the_available_balance(build_wallet):
    """Locked money is the only source, exactly as it is for ``payout_from_locked``."""
    wallet = build_wallet(available="10000", locked="500")

    with pytest.raises(InsufficientFundsError):
        ReleaseFromLocked(wallet, InMemoryTransactionRepository(), MOMENT).execute(
            ngn("5000"), internal_reference=str(uuid4())
        )

    assert wallet.available_balance == ngn("10000")
    assert wallet.locked_balance == ngn("500")


def test_a_pooled_release_leaves_no_pot_sealed_early(build_wallet):
    """A sealed pot is not a candidate, even though the wallet holds the money.

    The failure of this case and the failure of "not enough money" are the same
    exception on purpose - see ``_draw_from_matured`` - but they are different
    situations, so the wallet's state afterwards is asserted to be untouched
    rather than merely the exception type.
    """
    wallet = build_wallet(available="0")
    sealed = wallet.open_fund(
        "Vacation", FundKind.PERSONAL, maturity_date=date(2027, 1, 1)
    )
    wallet.deposit_into_fund(sealed.fund_id, ngn("5000"))
    repository = InMemoryTransactionRepository()

    with pytest.raises(InsufficientFundsError):
        ReleaseFromLocked(wallet, repository, MOMENT).execute(
            ngn("1000"), internal_reference=str(uuid4())
        )

    assert sealed.balance == ngn("5000")
    assert wallet.available_balance == ngn("0")
    # Recorded as an attempt, like every rejection the wallet itself makes.
    assert list(repository.transactions.values())[0].status is TransactionStatus.FAILED


def test_a_frozen_wallet_can_still_be_released_from(build_wallet):
    """The money does not leave the wallet, so freezing has nothing to say here."""
    wallet = build_wallet(available="0", locked="5000", status=WalletStatus.FROZEN)

    ReleaseFromLocked(wallet, InMemoryTransactionRepository(), MOMENT).execute(
        ngn("2000"), internal_reference=str(uuid4())
    )

    assert wallet.available_balance == ngn("2000")
    assert wallet.locked_balance == ngn("3000")


def test_a_closed_wallet_refuses_a_pooled_release(build_wallet):
    wallet = build_wallet(available="0", locked="5000", status=WalletStatus.CLOSED)

    with pytest.raises(WalletClosedError):
        ReleaseFromLocked(wallet, InMemoryTransactionRepository(), MOMENT).execute(
            ngn("2000"), internal_reference=str(uuid4())
        )


def test_a_pooled_release_in_the_wrong_currency_fails_and_is_recorded(build_wallet):
    wallet = build_wallet(available="0", locked="5000")
    repository = InMemoryTransactionRepository()

    with pytest.raises(CurrencyMismatchError):
        ReleaseFromLocked(wallet, repository, MOMENT).execute(
            Money(Decimal("2000"), USD), internal_reference=str(uuid4())
        )

    assert list(repository.transactions.values())[0].status is TransactionStatus.FAILED


@pytest.mark.parametrize("amount", ["0", "-2000"])
def test_a_non_positive_pooled_release_fails_and_persists_nothing(
    build_wallet, amount
):
    wallet = build_wallet(available="0", locked="5000")
    repository = InMemoryTransactionRepository()

    with pytest.raises(InvalidAmountError):
        ReleaseFromLocked(wallet, repository, MOMENT).execute(
            ngn(amount), internal_reference=str(uuid4())
        )

    assert not repository.transactions


def test_replaying_the_same_internal_reference_releases_only_once(build_wallet):
    wallet = build_wallet(available="0", locked="5000")
    repository = InMemoryTransactionRepository()
    service = ReleaseFromLocked(wallet, repository, MOMENT)
    reference = str(uuid4())

    first = service.execute(ngn("1000"), reference)
    second = service.execute(ngn("1000"), reference)

    assert second.transaction_id == first.transaction_id
    assert wallet.available_balance == ngn("1000")
    assert wallet.locked_balance == ngn("4000")


def test_release_is_persisted_as_pending_before_the_wallet_is_touched(
    build_wallet, recording_transactions
):
    wallet = build_wallet(available="0", locked="5000")

    ReleaseFromLocked(wallet, recording_transactions, MOMENT).execute(
        ngn("1000"), internal_reference=str(uuid4())
    )

    assert recording_transactions.saved_statuses == [
        TransactionStatus.PENDING,
        TransactionStatus.SUCCESSFUL,
    ]
