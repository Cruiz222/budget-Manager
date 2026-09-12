"""``release_hold``: giving back money that was held for a movement that failed.

**This file is the mirror of ``test_apply_deposit.py``, and reading them side by
side is the fastest way to see why there are two methods.** They are nearly the
same operation - both add to the available balance, both apply the same currency
and positivity checks - and they differ in exactly one place, which is the status
check. Everything below the first two tests exists to pin that difference, and
the two "allowed" cases are not edge cases: they are the reason the method was
written at all.

The concrete shape of the problem, because it is easy to lose: a payout holds its
money by debiting the available balance and leaving the ledger row PENDING.
Money held that way is in neither ``available_balance`` nor a pot, so it does not
stop ``close()`` succeeding - a wallet can therefore be CLOSED with a payout still
in flight. When that payout fails, the hold has to go back, and ``apply_deposit``
refuses a closed wallet. Without this method the money would be stranded in a
wallet nobody can use, which is how a hold becomes a hole.
"""

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.money.currency import Currency
from app.domain.money.exception import (
    CurrencyMismatchError,
    InvalidAmountError,
    WalletClosedError,
)
from app.domain.money.fund import Fund
from app.domain.money.fundKind import FundKind
from app.domain.money.money import Money
from app.domain.money.wallet import Wallet
from app.domain.money.walletStatus import WalletStatus


def a_wallet(status=WalletStatus.ACTIVE, available=5000, funds=()):
    return Wallet(
        wallet_id=uuid4(),
        user_id=uuid4(),
        currency=Currency.NGN,
        status=status,
        _available_balance=Money(available, Currency.NGN),
        _funds=funds,
    )


def a_fund(name="Vacation", balance="4000"):
    return Fund(
        fund_id=uuid4(),
        name=name,
        kind=FundKind.PERSONAL,
        _balance=Money(Decimal(balance), Currency.NGN),
        created_at=datetime(2026, 1, 1),
    )


def test_releasing_a_hold_increases_the_available_balance():
    wallet = a_wallet()

    wallet.release_hold(Money(2000, Currency.NGN))

    assert wallet.available_balance.amount == 7000


def test_releasing_a_zero_hold_raises_error():
    with pytest.raises(InvalidAmountError):
        a_wallet().release_hold(Money(0, Currency.NGN))


def test_releasing_a_negative_hold_raises_error():
    """The sign check is not decoration - it is a debit by another name.

    The amount reaching this method is the ledger row's, which is always
    positive, so a negative one means a caller has confused a hold with a
    withdrawal. Accepting it would quietly subtract from the balance through a
    method whose entire contract is that it adds.
    """
    with pytest.raises(InvalidAmountError):
        a_wallet().release_hold(Money(-2000, Currency.NGN))


def test_releasing_a_hold_in_another_currency_raises_error():
    with pytest.raises(CurrencyMismatchError):
        a_wallet().release_hold(Money(2000, Currency.USD))


def test_a_hold_is_released_into_a_closed_wallet():
    """**The case this method exists for**, and ``apply_deposit`` refuses it.

    The assertion is deliberately paired with the same call against the other
    method below, so that the difference is pinned as a difference rather than as
    two unrelated facts about two methods. If ``apply_deposit``'s closed guard is
    ever relaxed, or this method gains one, exactly one of these two tests fails -
    and the failure says which of the two rules moved.
    """
    wallet = a_wallet(status=WalletStatus.CLOSED)

    wallet.release_hold(Money(2000, Currency.NGN))

    assert wallet.available_balance.amount == 7000


def test_a_hold_is_released_into_a_frozen_wallet():
    """Freezing stops value leaving, and this is value that never left.

    The money was debited when the transfer was requested, so returning it puts
    the wallet back where it already was rather than moving anything new across
    the freeze's line. A frozen wallet is therefore no reason to refuse - and
    refusing would strand the money until somebody unfroze the wallet, which is a
    hold with a hostage.
    """
    wallet = a_wallet(status=WalletStatus.FROZEN)

    wallet.release_hold(Money(2000, Currency.NGN))

    assert wallet.available_balance.amount == 7000


def test_the_closed_wallet_refusal_is_still_apply_deposits():
    """The other half of the pair above, asserted here so the contrast is local.

    ``apply_deposit`` refusing a closed wallet is correct and stays: money
    arriving from *outside* into a wallet nobody can use is money this system has
    nowhere to put. The rule is not "closed wallets cannot receive money" - the
    test above pays into one - it is "money from outside cannot be accepted for a
    wallet that cannot be used". Two rules, two methods, and the difference is
    the direction the money came from.
    """
    wallet = a_wallet(status=WalletStatus.CLOSED)

    with pytest.raises(WalletClosedError):
        wallet.apply_deposit(Money(2000, Currency.NGN))


def test_releasing_a_hold_never_lands_in_a_pot():
    """The same boundary ``apply_deposit`` has, for the same reason.

    A hold was taken from the available balance, so it goes back to the available
    balance. Paying it into a pot would be moving money into a reservation its
    owner never made, and - worse - it would leave the pot's balance disagreeing
    with what the pot was funded with.
    """
    fund = a_fund()
    wallet = a_wallet(funds=(fund,))

    wallet.release_hold(Money(2000, Currency.NGN))

    assert wallet.available_balance == Money(7000, Currency.NGN)
    assert fund.balance == Money(Decimal("4000"), Currency.NGN)
