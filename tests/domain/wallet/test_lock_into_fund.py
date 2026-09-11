"""``lock_into_fund``: moving available money into a named pot.

The pot-scoped successor to the whole-pool ``lock_funds`` tests. Every case is
the same case with a pot named, plus one that is genuinely new: an unknown pot is
now refused *as* an unknown pot, which the pool version had no way to say.
"""

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.money.currency import Currency
from app.domain.money.exception import (
    CurrencyMismatchError,
    FundNotFoundError,
    InsufficientFundsError,
    InvalidAmountError,
    WalletClosedError,
)
from app.domain.money.money import Money
from app.domain.money.walletStatus import WalletStatus

#: Any moment, since nothing in this file depends on one. Locks are refused for
#: amount and currency reasons and never for a date, so the value is arbitrary -
#: it is passed because the wallet requires it, not because a test reads it.
MOMENT = datetime(2026, 1, 1)


def ngn(amount: str) -> Money:
    return Money(Decimal(amount), Currency.NGN)


def test_locking_moves_available_money_into_the_named_pot(build_wallet):
    wallet = build_wallet(available="10000", locked="6000")
    pot = wallet.funds[0]

    wallet.lock_into_fund(pot.fund_id, ngn("4000"), MOMENT)

    assert wallet.available_balance == ngn("6000")
    # The pot grew by what the available balance lost. Both halves are asserted,
    # because a move that got one right and the other wrong still balances the
    # wallet's total and would otherwise look fine.
    assert pot.balance == ngn("10000")
    assert wallet.locked_balance == ngn("10000")


def test_locking_more_than_the_available_balance_raises_insufficient_funds(
    build_wallet,
):
    wallet = build_wallet(available="4000", locked="6000")

    with pytest.raises(InsufficientFundsError):
        wallet.lock_into_fund(wallet.funds[0].fund_id, ngn("6000"), MOMENT)


def test_an_unknown_pot_is_reported_as_unknown_not_as_insufficient_funds(build_wallet):
    """The pot is looked up before the balance is checked, and the order matters.

    A caller who mistyped a pot name has a different problem from one who is short
    of money. Reporting the second when the first is true sends them off to top up
    a wallet that was never the issue - so this wallet is deliberately given
    *nothing* available, making both errors true at once and the answer to which
    one wins observable.
    """
    wallet = build_wallet(available="0", locked="6000")

    with pytest.raises(FundNotFoundError):
        wallet.lock_into_fund(uuid4(), ngn("5000"), MOMENT)


@pytest.mark.parametrize("amount", ["0", "-1000"])
def test_a_pot_refuses_a_non_positive_lock(build_wallet, amount):
    wallet = build_wallet(available="10000", locked="6000")

    with pytest.raises(InvalidAmountError):
        wallet.lock_into_fund(wallet.funds[0].fund_id, ngn(amount), MOMENT)


def test_the_amount_must_be_in_the_wallets_currency(build_wallet):
    wallet = build_wallet(available="10000", locked="6000")

    with pytest.raises(CurrencyMismatchError):
        wallet.lock_into_fund(
            wallet.funds[0].fund_id, Money(Decimal("1000"), Currency.USD), MOMENT
        )


def test_a_frozen_wallet_can_still_lock(build_wallet):
    """Freezing stops value *leaving*, and this is value being reshuffled.

    Unchanged from the whole-pool version, and still worth pinning: this
    asymmetry with withdraw and payout is the whole content of what freezing
    means.
    """
    wallet = build_wallet(
        available="10000", locked="4000", status=WalletStatus.FROZEN
    )

    wallet.lock_into_fund(wallet.funds[0].fund_id, ngn("6000"), MOMENT)

    assert wallet.available_balance == ngn("4000")
    assert wallet.locked_balance == ngn("10000")


def test_a_closed_wallet_refuses(build_wallet):
    wallet = build_wallet(
        available="10000", locked="6000", status=WalletStatus.CLOSED
    )

    with pytest.raises(WalletClosedError):
        wallet.lock_into_fund(wallet.funds[0].fund_id, ngn("1000"), MOMENT)
