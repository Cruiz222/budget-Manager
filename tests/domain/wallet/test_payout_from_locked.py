"""``payout_from_locked``: the locked pots paying value out of the wallet.

Pot-scoped. The rule that shaped this file is that a payout may only spend pots
that have **come due**, which the pool version had no notion of - so alongside
the translated cases are the ones that pin the boundary, and the draw order that
this phase uses as a placeholder (see ``Wallet._draw_from_matured``).
"""

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.domain.money.currency import Currency
from app.domain.money.exception import (
    CurrencyMismatchError,
    InsufficientFundsError,
    InvalidAmountError,
    WalletClosedError,
    WalletFrozenError,
)
from app.domain.money.fundKind import FundKind
from app.domain.money.money import Money
from app.domain.money.walletStatus import WalletStatus

NGN = Currency.NGN
USD = Currency.USD

#: Any moment after the fixture's pot, which has no maturity date and so is open
#: at every moment. Deliberately not "now": no test here reads a clock.
MOMENT = datetime(2026, 1, 1)


def ngn(amount: str) -> Money:
    return Money(Decimal(amount), NGN)


def test_payout_spends_locked_and_leaves_available_alone(build_wallet):
    """The defining difference from a release.

    A release moves locked -> available, so total holdings are unchanged. A payout
    *spends* the locked pots: locked falls, available is untouched, and what the
    wallet holds in total drops.
    """
    wallet = build_wallet(available="4000", locked="10000")

    wallet.payout_from_locked(ngn("6000"), MOMENT)

    assert wallet.locked_balance == ngn("4000")
    assert wallet.available_balance == ngn("4000")


def test_payout_reduces_total_holdings(build_wallet):
    wallet = build_wallet(available="1000", locked="5000")

    wallet.payout_from_locked(ngn("2000"), MOMENT)

    total = wallet.available_balance + wallet.locked_balance
    assert total == ngn("4000")


def test_payout_exactly_the_locked_balance_empties_it(build_wallet):
    wallet = build_wallet(available="1000", locked="5000")

    wallet.payout_from_locked(ngn("5000"), MOMENT)

    assert wallet.locked_balance == ngn("0")
    assert wallet.available_balance == ngn("1000")


def test_payout_more_than_locked_cannot_reach_into_available(build_wallet):
    """Available balance is not a fallback - a payout is funded by locked only."""
    wallet = build_wallet(available="10000", locked="500")

    with pytest.raises(InsufficientFundsError):
        wallet.payout_from_locked(ngn("5000"), MOMENT)


def test_insufficient_locked_leaves_both_balances_untouched(build_wallet):
    wallet = build_wallet(available="1000", locked="4000")

    with pytest.raises(InsufficientFundsError):
        wallet.payout_from_locked(ngn("4001"), MOMENT)

    assert wallet.locked_balance == ngn("4000")
    assert wallet.available_balance == ngn("1000")


def test_payout_zero_amount_error(build_wallet):
    wallet = build_wallet(locked="4000")

    with pytest.raises(InvalidAmountError):
        wallet.payout_from_locked(ngn("0"), MOMENT)


def test_payout_negative_amount_error(build_wallet):
    wallet = build_wallet(locked="4000")

    with pytest.raises(InvalidAmountError):
        wallet.payout_from_locked(ngn("-2000"), MOMENT)


def test_payout_currency_mismatch_error(build_wallet):
    wallet = build_wallet(locked="4000")

    with pytest.raises(CurrencyMismatchError):
        wallet.payout_from_locked(Money(Decimal("2000"), USD), MOMENT)


def test_payout_from_closed_wallet_error(build_wallet):
    wallet = build_wallet(status=WalletStatus.CLOSED, locked="4000")

    with pytest.raises(WalletClosedError):
        wallet.payout_from_locked(ngn("2000"), MOMENT)


def test_payout_from_frozen_wallet_error(build_wallet):
    """Freezing stops value leaving the wallet.

    Locking into a pot and releasing out of one still work on a frozen wallet,
    because they only move money between balances. A payout is money leaving, so
    it is refused - the same call withdraw() makes.
    """
    wallet = build_wallet(status=WalletStatus.FROZEN, locked="4000")

    with pytest.raises(WalletFrozenError):
        wallet.payout_from_locked(ngn("2000"), MOMENT)


# --- the maturity boundary ------------------------------------------------


def test_a_payout_cannot_spend_a_pot_that_has_not_come_due(build_wallet):
    """The rule the phase exists for, on the payout side.

    Note the wallet *has* the money - 5000 sits in the pot - so the refusal is
    about the date and nothing else. That is what makes this different from the
    insufficient-funds cases above.
    """
    wallet = build_wallet(available="0")
    sealed = wallet.open_fund(
        "Vacation", FundKind.PERSONAL, maturity_date=date(2026, 6, 1)
    )
    wallet.deposit_into_fund(sealed.fund_id, ngn("5000"))

    with pytest.raises(InsufficientFundsError):
        wallet.payout_from_locked(ngn("1000"), datetime(2026, 5, 31, 23, 59))

    assert sealed.balance == ngn("5000")
    # The wallet still reports it as locked - "not spendable yet" is not "gone".
    assert wallet.locked_balance == ngn("5000")


def test_a_payout_can_spend_a_pot_once_it_has_come_due(build_wallet):
    wallet = build_wallet(available="0")
    sealed = wallet.open_fund(
        "Vacation", FundKind.PERSONAL, maturity_date=date(2026, 6, 1)
    )
    wallet.deposit_into_fund(sealed.fund_id, ngn("5000"))

    wallet.payout_from_locked(ngn("1000"), datetime(2026, 6, 1, 0, 0))

    assert sealed.balance == ngn("4000")
    assert wallet.locked_balance == ngn("4000")


def test_only_the_matured_pots_count_towards_the_amount(build_wallet):
    """An immature pot is not part of the total a payout is measured against.

    If it were, the wallet would look able to cover a payout it cannot make, and
    the refusal would arrive from somewhere further down rather than as a plain
    "not enough spendable money".
    """
    wallet = build_wallet(available="0")
    open_pot = wallet.open_fund("Salary", FundKind.PERSONAL)
    wallet.open_fund("Vacation", FundKind.PERSONAL, maturity_date=date(2027, 1, 1))
    wallet.deposit_into_fund(open_pot.fund_id, ngn("1000"))
    wallet.deposit_into_fund(wallet.funds[1].fund_id, ngn("9000"))

    with pytest.raises(InsufficientFundsError):
        wallet.payout_from_locked(ngn("2000"), MOMENT)

    assert wallet.matured_locked_balance(MOMENT) == ngn("1000")
    assert wallet.locked_balance == ngn("10000")


# --- the placeholder draw rule --------------------------------------------


def test_a_payout_draws_from_pots_oldest_first(build_wallet):
    """Decision 38's placeholder rule: no pot is named, so the oldest pays.

    Pinned because it is a decision rather than an accident - the next phase
    replaces it with "the payout names its pot", and this test is what will have
    to change when it does.
    """
    wallet = build_wallet(available="0")
    older = wallet.open_fund("Rent", FundKind.PERSONAL)
    newer = wallet.open_fund("Car", FundKind.PERSONAL)
    wallet.deposit_into_fund(older.fund_id, ngn("3000"))
    wallet.deposit_into_fund(newer.fund_id, ngn("3000"))

    wallet.payout_from_locked(ngn("4000"), MOMENT)

    # The older pot is emptied, the newer one is only touched for the remainder.
    assert older.balance == ngn("0")
    assert newer.balance == ngn("2000")


def test_the_draw_stops_once_the_amount_is_covered(build_wallet):
    """Later pots are left exactly as they were, not even momentarily touched."""
    wallet = build_wallet(available="0")
    first = wallet.open_fund("Rent", FundKind.PERSONAL)
    second = wallet.open_fund("Car", FundKind.PERSONAL)
    wallet.deposit_into_fund(first.fund_id, ngn("3000"))
    wallet.deposit_into_fund(second.fund_id, ngn("3000"))

    wallet.payout_from_locked(ngn("1000"), MOMENT)

    assert first.balance == ngn("2000")
    assert second.balance == ngn("3000")


def test_an_empty_pot_does_not_break_the_draw(build_wallet):
    """A matured pot holding nothing is a candidate that can cover nothing.

    Without the skip it would be asked to pay zero, which ``Fund.pay`` refuses -
    so a pot opened and never funded, sitting alongside a funded one, would make
    every payout fail. Worth a test rather than a comment because the failing
    case is a wallet with an *untouched* pot in it, which is easy to end up with.
    """
    wallet = build_wallet(available="0")
    wallet.open_fund("Empty", FundKind.PERSONAL)
    funded = wallet.open_fund("Funded", FundKind.PERSONAL)
    wallet.deposit_into_fund(funded.fund_id, ngn("2000"))

    wallet.payout_from_locked(ngn("2000"), MOMENT)

    assert funded.balance == ngn("0")
    assert wallet.locked_balance == ngn("0")
