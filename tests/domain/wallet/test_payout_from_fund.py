"""``payout_from_fund``: spending one *named* pot, and when it may go early.

The successor to ``payout_from_locked``'s placeholder rule, and the file where
the phase's product rule lands. Two questions are being pinned here, and they
are separable:

  - **Which pot paid?** Naming one means the draw is not a search. The pooled
    draw spends whatever matured pots cover the amount, oldest first; this spends
    the pot it was told to, and refuses if that pot cannot cover it - even when
    the wallet as a whole easily could. That is the point: a plan that names a
    pot has committed to *that* money.
  - **May it go early?** A ``BUSINESS`` pot funds a scheduled payment before its
    maturity date, but only when the commitment predates the money. The ordering
    is ``seal -> commit -> fund``, and every test in the second half of this file
    is one of those three steps being moved out of order.

The moments below are fixed rather than read from a clock, because the whole
subject is an *ordering* - and an ordering expressed against ``datetime.now()``
would be a test that only passes in the year it was written.
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
    WalletFrozenError,
)
from app.domain.money.fundKind import FundKind
from app.domain.money.money import Money
from app.domain.money.walletStatus import WalletStatus

NGN = Currency.NGN
USD = Currency.USD

#: The pot's terms come into force - ``open_fund``.
SEALED_AT = datetime(2026, 1, 1)

#: A plan is created and commits money to the pot.
COMMITTED_AT = datetime(2026, 1, 2)

#: The money actually arrives - ``deposit_into_fund``.
FUNDED_AT = datetime(2026, 1, 3)

#: The pot's due date, and a moment well before it. Every "may this go early?"
#: case below pays at ``EARLY``, so a refusal is always about the date.
MATURES = date(2026, 6, 1)
EARLY = datetime(2026, 3, 1)


def ngn(amount: str) -> Money:
    return Money(Decimal(amount), NGN)


def sealed_business_pot(wallet, name="Supplier", funded=True, matures=MATURES):
    """A business pot, sealed at ``SEALED_AT``, funded at ``FUNDED_AT``.

    The two moments are separate arguments to the wallet for a reason - they are
    two events, and the rule between them is the subject of half this file. A
    helper that used one moment for both would make the ordering untestable.

    ``matures`` is a parameter rather than a constant because of the order the
    pot's own checks run in: maturity is asked *before* the amount, so a test
    about a bad amount against a sealed pot would get ``FundNotMaturedError`` and
    never reach the rule it is about. Those tests pass ``None`` - a pot with no
    date is always spendable - so the amount check is the one that answers.
    """
    fund = wallet.open_fund(
        name, FundKind.BUSINESS, maturity_date=matures, as_of=SEALED_AT
    )
    if funded:
        wallet.deposit_into_fund(fund.fund_id, ngn("5000"), FUNDED_AT)
    return fund


# --- which pot paid --------------------------------------------------------


def test_a_named_payout_spends_that_pot_and_no_other(build_wallet):
    """The difference from the pooled draw, stated as the fact that matters.

    Both pots are matured and either could cover the payment, so a pooled draw
    would have an answer too - it would just be a different one. The assertion is
    that the pot that was named is the pot that moved, and that the other is
    untouched rather than merely restored.
    """
    wallet = build_wallet(available="0")
    named = wallet.open_fund("Supplier", FundKind.BUSINESS, as_of=SEALED_AT)
    other = wallet.open_fund("Rent", FundKind.BUSINESS, as_of=SEALED_AT)
    wallet.deposit_into_fund(named.fund_id, ngn("3000"), FUNDED_AT)
    wallet.deposit_into_fund(other.fund_id, ngn("3000"), FUNDED_AT)

    wallet.payout_from_fund(named.fund_id, ngn("2000"), EARLY)

    assert named.balance == ngn("1000")
    assert other.balance == ngn("3000")


def test_the_named_pot_must_cover_the_payment_itself(build_wallet):
    """No falling back to the wallet's other pots, which is the strict reading.

    A wallet holding 10,000 across two pots cannot pay 5,000 out of a pot holding
    1,000. The alternative - quietly reaching into a second pot - would make
    "this plan draws on Vacation" a preference rather than a commitment, and the
    user would discover the difference when the wrong pot was empty.
    """
    wallet = build_wallet(available="0")
    small = wallet.open_fund("Supplier", FundKind.BUSINESS, as_of=SEALED_AT)
    large = wallet.open_fund("Rent", FundKind.BUSINESS, as_of=SEALED_AT)
    wallet.deposit_into_fund(small.fund_id, ngn("1000"), FUNDED_AT)
    wallet.deposit_into_fund(large.fund_id, ngn("9000"), FUNDED_AT)

    with pytest.raises(InsufficientFundsError):
        wallet.payout_from_fund(small.fund_id, ngn("5000"), EARLY)

    assert small.balance == ngn("1000")
    assert large.balance == ngn("9000")


def test_an_unknown_pot_is_reported_as_unknown(build_wallet):
    wallet = build_wallet(available="0")

    with pytest.raises(FundNotFoundError):
        wallet.payout_from_fund(uuid4(), ngn("1000"), EARLY)


@pytest.mark.parametrize("amount", ["0", "-2000"])
def test_a_named_payout_refuses_a_non_positive_amount(build_wallet, amount):
    """A pot with no date, so the amount is what the pot objects to.

    Held open deliberately: against a sealed pot the maturity check answers
    first and this test would pass while proving nothing about amounts.
    """
    wallet = build_wallet(available="0")
    fund = sealed_business_pot(wallet, matures=None)

    with pytest.raises(InvalidAmountError):
        wallet.payout_from_fund(fund.fund_id, ngn(amount), EARLY)


def test_the_amount_must_be_in_the_wallets_currency(build_wallet):
    """Held open for the same reason as the amount test above."""
    wallet = build_wallet(available="0")
    fund = sealed_business_pot(wallet, matures=None)

    with pytest.raises(CurrencyMismatchError):
        wallet.payout_from_fund(
            fund.fund_id, Money(Decimal("1000"), USD), EARLY
        )


def test_a_frozen_wallet_refuses_a_payout(build_wallet):
    """Value is leaving the wallet, so freezing stops it - as it does a withdraw.

    The contrast with release is the whole content of what freezing means: a
    release moves money between the wallet's own balances and stays allowed, and
    a payout does not.
    """
    wallet = build_wallet(available="0")
    fund = sealed_business_pot(wallet)
    wallet.status = WalletStatus.FROZEN

    with pytest.raises(WalletFrozenError):
        wallet.payout_from_fund(fund.fund_id, ngn("1000"), EARLY)


def test_a_closed_wallet_refuses_a_payout(build_wallet):
    wallet = build_wallet(available="0")
    fund = sealed_business_pot(wallet)
    wallet.status = WalletStatus.CLOSED

    with pytest.raises(WalletClosedError):
        wallet.payout_from_fund(fund.fund_id, ngn("1000"), EARLY)


# --- may it go early? ------------------------------------------------------


def test_a_matured_pot_pays_with_no_commitment_at_all(build_wallet):
    """The ordinary case, pinned so the exemption is not mistaken for the rule.

    Once the date has passed the pot needs no authority from anyone, which is why
    an ad-hoc payout is refused *before* maturity rather than always.
    """
    wallet = build_wallet(available="0")
    fund = sealed_business_pot(wallet)

    wallet.payout_from_fund(fund.fund_id, ngn("1000"), datetime(2026, 6, 1))

    assert fund.balance == ngn("4000")


def test_a_personal_pot_refuses_to_pay_early(build_wallet):
    """Even with a commitment that predates the money, and that is the kind's rule.

    A personal pot pays early by no route - the exemption exists for a scheduled
    obligation to an external payee, not for spending, and no ordering of events
    turns spending into one.
    """
    wallet = build_wallet(available="0")
    fund = wallet.open_fund(
        "Vacation", FundKind.PERSONAL, maturity_date=MATURES, as_of=SEALED_AT
    )
    wallet.deposit_into_fund(fund.fund_id, ngn("5000"), FUNDED_AT)

    with pytest.raises(FundNotMaturedError):
        wallet.payout_from_fund(fund.fund_id, ngn("1000"), EARLY, COMMITTED_AT)


def test_a_business_pot_refuses_an_adhoc_early_payment(build_wallet):
    """The temptation route, closed at the pot rather than at the command.

    A hand-typed payout has no plan behind it, so it passes no commitment and the
    pot refuses. This is deliberate: the ruling covers *scheduled* payments, and
    a payment someone typed just now is precisely not one.
    """
    wallet = build_wallet(available="0")
    fund = sealed_business_pot(wallet)

    with pytest.raises(FundNotMaturedError):
        wallet.payout_from_fund(fund.fund_id, ngn("1000"), EARLY)

    assert fund.balance == ngn("5000")


def test_a_business_pot_pays_early_when_the_commitment_predates_the_money(
    build_wallet,
):
    """The headline case: ``seal -> commit -> fund``, and the money may go early.

    The pot is sealed on 1 January, a plan commits on the 2nd, the money arrives
    on the 3rd, and the payment lands in March - three months before the pot's
    date. This is the ordering the whole feature exists to make possible, and it
    is the only ordering that does.
    """
    wallet = build_wallet(available="0")
    fund = sealed_business_pot(wallet)

    wallet.payout_from_fund(fund.fund_id, ngn("2000"), EARLY, COMMITTED_AT)

    assert fund.balance == ngn("3000")


def test_a_business_pot_refuses_when_the_money_predates_the_commitment(
    build_wallet,
):
    """The other order, and the case the rule was written for.

    Here the money arrived on 2 January and the commitment was made a day later.
    The pot holds plenty and the plan is a real one - but the commitment came
    *after* the money, so the money is money that could already have been spent,
    and it waits for its date. Trying it the other way round is the manoeuvre the
    rule stops: fund first, then invent a plan that unlocks what you funded.
    """
    wallet = build_wallet(available="0")
    fund = wallet.open_fund(
        "Supplier", FundKind.BUSINESS, maturity_date=MATURES, as_of=SEALED_AT
    )
    wallet.deposit_into_fund(fund.fund_id, ngn("5000"), COMMITTED_AT)

    with pytest.raises(FundNotMaturedError):
        wallet.payout_from_fund(
            fund.fund_id, ngn("2000"), EARLY, datetime(2026, 1, 3)
        )


def test_a_commitment_made_before_the_pot_was_sealed_does_not_authorise_payment(
    build_wallet,
):
    """A commitment older than the pot's own terms is not a commitment to them.

    The three-step ordering has two comparisons, and this is the other one. A
    plan created before the pot existed could not have been made against it, so
    the pot's terms win and the payment waits.
    """
    wallet = build_wallet(available="0")
    fund = sealed_business_pot(wallet)

    with pytest.raises(FundNotMaturedError):
        wallet.payout_from_fund(
            fund.fund_id, ngn("2000"), EARLY, datetime(2025, 12, 1)
        )


def test_an_extension_lapses_the_exemption(build_wallet):
    """``fund extend`` re-seals, and a commitment already made does not survive it.

    The ruling: pushing the date out is the owner saying "not yet", and they are
    held to it. The mechanism is that ``sealed_at`` moves forward past the
    commitment's moment, so the ordering that authorised the early payment stops
    holding - no extra state, and no second rule to keep in step.

    Both halves are asserted, because a pot that simply stopped paying early
    forever would satisfy the first assertion and break the feature. After the
    extension the same payment succeeds on the new date.
    """
    wallet = build_wallet(available="0")
    fund = sealed_business_pot(wallet)

    wallet.payout_from_fund(fund.fund_id, ngn("1000"), EARLY, COMMITTED_AT)

    later = date(2026, 9, 1)
    wallet.extend_fund(fund.fund_id, later, datetime(2026, 3, 2))

    with pytest.raises(FundNotMaturedError):
        wallet.payout_from_fund(fund.fund_id, ngn("1000"), EARLY, COMMITTED_AT)

    wallet.payout_from_fund(fund.fund_id, ngn("1000"), datetime(2026, 9, 1), COMMITTED_AT)
    assert fund.balance == ngn("3000")
