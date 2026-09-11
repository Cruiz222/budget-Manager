"""``release_from_fund``: moving pot money back to the available balance.

The pot-scoped successor to the whole-pool ``release_funds`` tests, and the file
where the phase's headline rule is pinned: **money leaves a pot only once the pot
has come due.** The whole-pool version had no such test because it had no such
rule - releasing was unconditional - so this is not a translation of the old file
so much as the reason the phase exists.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

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
from app.domain.money.walletStatus import WalletStatus

#: A moment after the fixture's pot (which has no maturity date) and before the
#: dated pots below. Any moment works for an open pot; using one that is *not*
#: "now" is the point - these tests never read a clock.
MOMENT = datetime(2026, 1, 1)


def ngn(amount: str) -> Money:
    return Money(Decimal(amount), Currency.NGN)


def test_releasing_moves_pot_money_back_to_available(build_wallet):
    wallet = build_wallet(available="4000", locked="10000")
    pot = wallet.funds[0]

    wallet.release_from_fund(pot.fund_id, ngn("6000"), MOMENT)

    assert wallet.available_balance == ngn("10000")
    assert pot.balance == ngn("4000")
    assert wallet.locked_balance == ngn("4000")


def test_releasing_more_than_the_pot_holds_raises_insufficient_funds(build_wallet):
    wallet = build_wallet(available="4000", locked="4000")

    with pytest.raises(InsufficientFundsError):
        wallet.release_from_fund(wallet.funds[0].fund_id, ngn("5000"), MOMENT)


def test_an_unknown_pot_is_reported_as_unknown(build_wallet):
    wallet = build_wallet(available="4000", locked="4000")

    with pytest.raises(FundNotFoundError):
        wallet.release_from_fund(uuid4(), ngn("1000"), MOMENT)


@pytest.mark.parametrize("amount", ["0", "-2000"])
def test_a_pot_refuses_a_non_positive_release(build_wallet, amount):
    wallet = build_wallet(available="4000", locked="4000")

    with pytest.raises(InvalidAmountError):
        wallet.release_from_fund(wallet.funds[0].fund_id, ngn(amount), MOMENT)


def test_the_amount_must_be_in_the_wallets_currency(build_wallet):
    wallet = build_wallet(available="4000", locked="4000")

    with pytest.raises(CurrencyMismatchError):
        wallet.release_from_fund(
            wallet.funds[0].fund_id, Money(Decimal("2000"), Currency.USD), MOMENT
        )


def test_a_frozen_wallet_can_still_be_released_from(build_wallet):
    """The wallet still holds every naira - only which balance holds it changes."""
    wallet = build_wallet(
        available="4000", locked="4000", status=WalletStatus.FROZEN
    )

    wallet.release_from_fund(wallet.funds[0].fund_id, ngn("2000"), MOMENT)

    assert wallet.available_balance == ngn("6000")
    assert wallet.locked_balance == ngn("2000")


def test_a_closed_wallet_refuses(build_wallet):
    wallet = build_wallet(
        available="4000", locked="4000", status=WalletStatus.CLOSED
    )

    with pytest.raises(WalletClosedError):
        wallet.release_from_fund(wallet.funds[0].fund_id, ngn("2000"), MOMENT)


def test_a_pot_that_has_not_come_due_refuses_release(build_wallet):
    """The headline rule, and the one case the pool version could not express.

    Note what is asserted on both sides of the boundary. Refusing before the date
    is only half the promise; a pot that refused *forever* would satisfy that half
    and be useless, so the same pot is released successfully on the day itself.
    """
    wallet = build_wallet(available="0")
    due = date(2026, 6, 1)
    pot = wallet.open_fund("Vacation", FundKind.PERSONAL, maturity_date=due)
    wallet.deposit_into_fund(pot.fund_id, ngn("5000"))

    with pytest.raises(FundNotMaturedError):
        wallet.release_from_fund(
            pot.fund_id, ngn("1000"), datetime(2026, 5, 31, 23, 59)
        )

    assert wallet.available_balance == ngn("0")
    assert pot.balance == ngn("5000")

    # The day itself, and the whole of it - a maturity is a date a human wrote,
    # so it is not midnight-to-midnight in some narrower sense.
    wallet.release_from_fund(pot.fund_id, ngn("1000"), datetime(2026, 6, 1, 0, 0))

    assert wallet.available_balance == ngn("1000")
    assert pot.balance == ngn("4000")


def test_a_refused_release_leaves_the_pot_untouched(build_wallet):
    """A refusal raises *before* mutating, which is what makes it safe to retry.

    ``WalletOperation`` records a FAILED ledger row and re-raises; nothing is
    half-applied. Pinned here because "raises" and "raises without having already
    taken the money" are different claims, and only the second one is useful.
    """
    wallet = build_wallet(available="0")
    pot = wallet.open_fund(
        "Vacation", FundKind.PERSONAL, maturity_date=date(2026, 6, 1)
    )
    wallet.deposit_into_fund(pot.fund_id, ngn("5000"))

    for _ in range(3):
        with pytest.raises(FundNotMaturedError):
            wallet.release_from_fund(pot.fund_id, ngn("5000"), MOMENT)

    assert pot.balance == ngn("5000")
    assert wallet.available_balance == ngn("0")
