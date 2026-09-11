"""``apply_deposit``: money arriving into the wallet's available balance.

The pot is not mentioned anywhere in this file, which is the point of the last
test: depositing and locking into a pot are different doors. This one always
lands in the available balance no matter how many pots the wallet has.
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


def test_depositing_positive_amount_increases_available_balance():
    wallet = a_wallet()

    wallet.apply_deposit(Money(2000, Currency.NGN))

    assert wallet.available_balance.amount == 7000


def test_depositing_zero_amount_raises_error():
    wallet = a_wallet()

    with pytest.raises(InvalidAmountError):
        wallet.apply_deposit(Money(0, Currency.NGN))


def test_depositing_negative_amount_raises_error():
    wallet = a_wallet()

    with pytest.raises(InvalidAmountError):
        wallet.apply_deposit(Money(-2000, Currency.NGN))


def test_depositing_different_currency_raises_error():
    wallet = a_wallet()

    with pytest.raises(CurrencyMismatchError):
        wallet.apply_deposit(Money(2000, Currency.USD))


def test_depositing_into_closed_wallet_raises_error():
    wallet = a_wallet(status=WalletStatus.CLOSED)

    with pytest.raises(WalletClosedError):
        wallet.apply_deposit(Money(2000, Currency.NGN))


def test_depositing_into_frozen_wallet_is_allowed():
    wallet = a_wallet(status=WalletStatus.FROZEN)

    wallet.apply_deposit(Money(2000, Currency.NGN))

    assert wallet.available_balance.amount == 7000


def test_a_plain_deposit_never_lands_in_a_pot():
    """The boundary between ``apply_deposit`` and ``deposit_into_fund``.

    Both are deposits and both come from outside the wallet, so it is worth
    pinning that they end somewhere different: this one always lands in the
    available balance, and the pots are left exactly as they were. Money reaches
    a pot either because the user named it, or not at all.
    """
    fund = a_fund()
    wallet = a_wallet(funds=(fund,))

    wallet.apply_deposit(Money(2000, Currency.NGN))

    assert wallet.available_balance == Money(7000, Currency.NGN)
    assert fund.balance == Money(Decimal("4000"), Currency.NGN)


def test_locked_balance_returns_the_sum_of_the_funds():
    """``locked_balance`` is a property, not a field, and it still answers.

    Kept from the version of this test written when the balance was stored - the
    assertion is unchanged, and what changed is that the wallet now has to be
    told about a pot for it to be true.
    """
    wallet = a_wallet(funds=(a_fund(name="Vacation", balance="4000"),))

    result = wallet.locked_balance

    assert result == Money(4000, Currency.NGN)
