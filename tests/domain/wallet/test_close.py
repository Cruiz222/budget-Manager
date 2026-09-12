"""Closing a wallet: the one status change with a precondition.

``freeze`` and ``unfreeze`` are reversible and ask nothing of the wallet's
contents. ``close`` is not reversible and asks that there be nothing left to
lose - because every guarded operation refuses on a CLOSED wallet, and no
transition reopens one, so money left inside would be unreachable for good. That
asymmetry is why the emptiness rule exists at all rather than being a
convenience.

The rule is **empty in full**: the available balance and every pot, including
locked money in a pot that has not matured. The consequence is deliberate and
worth stating, because it is the part that surprises people - a wallet holding an
unmatured commitment cannot be closed until that commitment is honoured or its
plan is cancelled. That is coherent with what this codebase says everywhere else
(the promise is the product), and the alternative strands the money.

What this file does *not* cover is plans. A live plan on a closed wallet would
fail on every tick forever, but the domain cannot know that: it does not import
planning. That rule lives in ``WalletService.close_wallet``.
"""

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.money.currency import Currency
from app.domain.money.exception import (
    InsufficientFundsError,
    WalletAlreadyClosedError,
    WalletClosedError,
    WalletNotEmptyError,
)
from app.domain.money.fundKind import FundKind
from app.domain.money.money import Money
from app.domain.money.wallet import Wallet
from app.domain.money.walletStatus import WalletStatus

#: A fixed moment, so no test in this file reads a clock.
MOMENT = datetime(2026, 1, 1)


def a_wallet(status=WalletStatus.ACTIVE, available=0):
    return Wallet(
        wallet_id=uuid4(),
        user_id=uuid4(),
        currency=Currency.NGN,
        status=status,
        _available_balance=Money(Decimal(available), Currency.NGN),
    )


# --- the transition itself --------------------------------------------------

def test_an_empty_wallet_closes():
    wallet = a_wallet()

    wallet.close()

    assert wallet.status == WalletStatus.CLOSED


def test_a_frozen_wallet_closes_if_it_is_empty():
    """Freezing is a reversible hold; closing is not. Empty is empty either way.

    Worth pinning because the two statuses feel similar from a distance - both
    stop money leaving - so "a frozen wallet is not closable" is a plausible rule
    somebody might assume. There is nothing in a frozen wallet to lose.
    """
    wallet = a_wallet(status=WalletStatus.FROZEN)

    wallet.close()

    assert wallet.status == WalletStatus.CLOSED


# --- the emptiness rule -----------------------------------------------------

def test_a_wallet_with_an_available_balance_refuses_to_close():
    wallet = a_wallet(available=10000)

    with pytest.raises(WalletNotEmptyError):
        wallet.close()

    assert wallet.status == WalletStatus.ACTIVE


def test_a_wallet_with_money_in_a_pot_refuses_to_close():
    """The locked half of "empty in full", which is the half easy to forget.

    ``locked_balance`` is a sum over the pots, so ``close`` gets this from one
    expression. The test is what stops somebody later reading "empty" as "the
    available balance is zero" and closing a wallet with savings still inside it.
    """
    wallet = a_wallet()
    pot = wallet.open_fund("Vacation", FundKind.PERSONAL)
    wallet.deposit_into_fund(
        pot.fund_id, Money(Decimal("5000"), Currency.NGN), MOMENT
    )

    with pytest.raises(WalletNotEmptyError):
        wallet.close()

    assert wallet.status == WalletStatus.ACTIVE


def test_an_empty_pot_does_not_stop_a_close():
    """A pot is a shape, not a holding. Zero everywhere is empty everywhere.

    The distinction matters because ``locked_balance`` sums pots: a wallet with
    three empty ones has a locked balance of zero, so a rule written against
    *pots* rather than against *money* would refuse this close and leave the
    owner with no way to tidy up a wallet they had already emptied.
    """
    wallet = a_wallet()
    wallet.open_fund("Vacation", FundKind.PERSONAL)
    wallet.open_fund("Rent", FundKind.BUSINESS)

    wallet.close()

    assert wallet.status == WalletStatus.CLOSED


def test_the_refusal_names_both_balances():
    """The message says what is in the way, because the caller cannot see it.

    A close is refused for money the owner may well have forgotten about - the
    whole point of a locked balance is that it is out of the way - so "cannot
    close" without a figure sends them looking through every pot by hand.
    """
    wallet = a_wallet(available=10000)
    pot = wallet.open_fund("Vacation", FundKind.PERSONAL)
    wallet.deposit_into_fund(
        pot.fund_id, Money(Decimal("5000"), Currency.NGN), MOMENT
    )

    with pytest.raises(WalletNotEmptyError, match="10000.00 NGN available"):
        wallet.close()


def test_closing_an_already_closed_wallet_raises_its_own_error():
    """``WalletAlreadyClosedError``, which is not the name a freeze gets here.

    The split was settled in Phase 2b, when CLOSED stopped being unreachable.
    Freezing a closed wallet is refused because *the wallet is closed*
    (``WalletClosedError``); closing one is refused because *it was already
    closed*. Two situations, two names - and until this phase they were one name,
    because only one of them could happen.
    """
    wallet = a_wallet()
    wallet.close()

    with pytest.raises(WalletAlreadyClosedError):
        wallet.close()


# --- what a closed wallet refuses -------------------------------------------

def test_a_closed_wallet_refuses_money_coming_in():
    wallet = a_wallet()
    wallet.close()

    with pytest.raises(WalletClosedError):
        wallet.apply_deposit(Money(Decimal("5000"), Currency.NGN))


def test_a_closed_wallet_refuses_a_pot_being_opened():
    """The guard is in ``open_fund`` rather than in a caller, and this is why.

    Opening a pot writes no ledger row and moves nothing, so it looks harmless on
    a closed wallet - but a pot is a place money can later be sent, and every
    path in would then have to remember to refuse. Refusing the shape is the one
    check that covers all of them.
    """
    wallet = a_wallet()
    wallet.close()

    with pytest.raises(WalletClosedError):
        wallet.open_fund("Vacation", FundKind.PERSONAL)


def test_a_closed_wallet_refuses_money_going_out():
    """Which is what makes "empty" a protection rather than an inconvenience.

    If a closed wallet still paid out, refusing to close a non-empty one would
    cost the owner one extra command and protect nothing.

    **The wallet is built closed with money in it rather than closed after being
    filled, and it has to be.** ``close`` refuses a wallet that is not empty, so
    this state is unreachable by transitioning to it - every closed wallet this
    codebase can actually produce holds nothing. That is exactly why the guard
    needs testing here rather than only through the transitions: if it only held
    for empty wallets it would be indistinguishable from the balance check, and
    the balance check is not what a closed wallet is supposed to be refused for.
    """
    wallet = a_wallet(status=WalletStatus.CLOSED, available=10000)

    with pytest.raises(WalletClosedError):
        wallet.withdraw(Money(Decimal("5000"), Currency.NGN))


def test_an_insufficient_balance_is_still_reported_as_itself():
    """On an *open* wallet, the ordinary refusal is the ordinary error.

    Asserted beside the closed-wallet tests because the two travel different
    paths - one a 409, the other a 400 - and a status check placed carelessly
    could report every withdrawal from a small wallet as "this wallet is closed".
    """
    wallet = a_wallet(available=100)

    with pytest.raises(InsufficientFundsError):
        wallet.withdraw(Money(Decimal("5000"), Currency.NGN))
