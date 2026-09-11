"""``withdraw``: money leaving the wallet out of the available balance.

Withdraw never touches a pot, and the last test here is what says so explicitly:
a wallet can be holding plenty in pots and still have nothing to withdraw,
because reserved money is not a fallback for spending. That is the same rule
``payout_from_locked`` states from the other side.
"""

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.money.currency import Currency
from app.domain.money.exception import (
    CurrencyMismatchError,
    InsufficientFundsError,
    NegativeAmountWithdrawalError,
    WalletClosedError,
    WalletFrozenError,
    ZeroAmountWithdrawalError,
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


def test_withdrawing_positive_amount_reduces_available_balance():
    wallet = a_wallet()

    wallet.withdraw(Money(2000, Currency.NGN))

    assert wallet.available_balance.amount == 3000


def test_withdrawing_more_than_available_balance_raises_insufficient_funds():
    wallet = a_wallet()

    with pytest.raises(InsufficientFundsError):
        wallet.withdraw(Money(6000, Currency.NGN))


def test_withdrawing_from_frozen_wallet_raises_error():
    wallet = a_wallet(status=WalletStatus.FROZEN)

    with pytest.raises(WalletFrozenError):
        wallet.withdraw(Money(2000, Currency.NGN))


def test_withdrawing_from_closed_wallet_raises_error():
    wallet = a_wallet(status=WalletStatus.CLOSED)

    with pytest.raises(WalletClosedError):
        wallet.withdraw(Money(1000, Currency.NGN))


def test_withdrawing_zero_amount_error():
    wallet = a_wallet()

    with pytest.raises(ZeroAmountWithdrawalError):
        wallet.withdraw(Money(0, Currency.NGN))


def test_withdrawing_negative_amount_error():
    wallet = a_wallet()

    with pytest.raises(NegativeAmountWithdrawalError):
        wallet.withdraw(Money(-1000, Currency.NGN))


def test_withdrawing_mismatched_currency_error():
    wallet = a_wallet()

    with pytest.raises(CurrencyMismatchError):
        wallet.withdraw(Money(1000, Currency.USD))


def test_locked_money_is_not_a_fallback_for_a_withdrawal():
    """A wallet rich in pots and poor in available balance cannot withdraw.

    The counterpart of ``payout_from_locked``'s "available is not a fallback".
    Together the two say the same thing in both directions: the two balances are
    not one balance with a preference, and no operation quietly reads across
    them. Releasing a pot is how its money becomes spendable - which is exactly
    why the maturity date can mean anything at all.
    """
    fund = a_fund(balance="9000")
    wallet = a_wallet(available=100, funds=(fund,))

    with pytest.raises(InsufficientFundsError):
        wallet.withdraw(Money(1000, Currency.NGN))

    assert fund.balance == Money(Decimal("9000"), Currency.NGN)
