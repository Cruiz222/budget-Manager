"""``Fund``: a named pot of locked money with a date it comes due.

The heart of the phase. Three rules are pinned here and nowhere else, and the
shape of the file follows them:

  - **Deposits are never refused for a date reason.** The pot can be fed before,
    on, and after its date - that is the whole answer to "I want to keep adding
    without opening a new plan for each deposit".
  - **Money leaves a sealed pot by no route.** ``release`` refuses; ``pay``
    refuses for a ``PERSONAL`` pot. The one exception is a ``BUSINESS`` pot
    paying before its date, and it is the only place the two kinds differ.
  - **Extending goes one way.** Later, never earlier, and never into the past.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.money.currency import Currency
from app.domain.money.exception import (
    CurrencyMismatchError,
    FundNotMaturedError,
    InsufficientFundsError,
    InvalidAmountError,
    InvalidFundBalanceError,
    InvalidFundCreatedAtError,
    InvalidFundIDError,
    InvalidFundKindError,
    InvalidFundMaturityError,
    InvalidFundNameError,
    MaturityNotExtendedError,
)
from app.domain.money.fund import Fund
from app.domain.money.fundKind import FundKind
from app.domain.money.money import Money

NGN = Currency.NGN
USD = Currency.USD

#: A moment well before every dated pot below, and after every open one.
MOMENT = datetime(2026, 1, 1)

DUE = date(2026, 6, 1)


def ngn(amount: str) -> Money:
    return Money(Decimal(amount), NGN)


def a_fund(
    name="Vacation",
    kind=FundKind.PERSONAL,
    balance="0",
    maturity_date=None,
    currency=NGN,
) -> Fund:
    return Fund(
        fund_id=uuid4(),
        name=name,
        kind=kind,
        _balance=Money(Decimal(balance), currency),
        maturity_date=maturity_date,
        created_at=datetime(2026, 1, 1),
    )


# --- construction ---------------------------------------------------------


def test_a_fund_with_an_invalid_id_raises_error():
    with pytest.raises(InvalidFundIDError):
        Fund(
            fund_id="not-a-uuid",
            name="Vacation",
            kind=FundKind.PERSONAL,
            _balance=ngn("0"),
        )


@pytest.mark.parametrize("name", ["", "   ", None, 42])
def test_a_fund_needs_a_real_name(name):
    """The name is the handle a human types, so an empty one is not a name.

    A whitespace-only name is refused along with the empty string: it would be
    unique, printable, and impossible to type unambiguously on a command line.
    """
    with pytest.raises(InvalidFundNameError):
        a_fund(name=name)


def test_a_fund_needs_a_fundkind():
    with pytest.raises(InvalidFundKindError):
        a_fund(kind="personal")


def test_a_funds_balance_must_be_money():
    with pytest.raises(InvalidFundBalanceError):
        Fund(
            fund_id=uuid4(),
            name="Vacation",
            kind=FundKind.PERSONAL,
            _balance=5000,
        )


def test_a_maturity_must_be_a_date_or_none():
    with pytest.raises(InvalidFundMaturityError):
        a_fund(maturity_date="2026-06-01")


def test_a_maturity_may_not_be_a_datetime():
    """The subclass trap, in its fifth appearance in this codebase.

    ``datetime`` passes ``isinstance(x, date)``, so a check written against the
    wider type would accept a pot that unlocks at a time of day nobody chose.
    The narrow type is required by name so that a datetime fails on its own
    terms rather than by a special case.
    """
    with pytest.raises(InvalidFundMaturityError):
        a_fund(maturity_date=datetime(2026, 6, 1, 9, 30))


def test_created_at_must_be_a_datetime():
    with pytest.raises(InvalidFundCreatedAtError):
        Fund(
            fund_id=uuid4(),
            name="Vacation",
            kind=FundKind.PERSONAL,
            _balance=ngn("0"),
            created_at=date(2026, 1, 1),
        )


def test_a_new_fund_defaults_to_created_now():
    """So the draw order is never an accident of whoever built the object."""
    fund = Fund(
        fund_id=uuid4(),
        name="Vacation",
        kind=FundKind.PERSONAL,
        _balance=ngn("0"),
    )

    assert isinstance(fund.created_at, datetime)


# --- maturity -------------------------------------------------------------


def test_a_fund_with_no_maturity_date_is_always_matured():
    """``None`` means *no maturity*, not "unknown" and not "never".

    Every pre-existing locked balance is migrated into a fund with no date, and
    it is this rule that makes that migration invisible: money that was
    releasable unconditionally stays releasable unconditionally.
    """
    fund = a_fund(maturity_date=None)

    assert fund.is_matured(MOMENT) is True
    assert fund.is_matured(datetime(1999, 1, 1)) is True
    assert fund.is_open is True


def test_a_fund_is_not_matured_before_its_date():
    fund = a_fund(maturity_date=DUE)

    assert fund.is_matured(datetime(2026, 5, 31, 23, 59)) is False


def test_a_fund_is_matured_on_its_date_and_afterwards():
    """A maturity is a whole day, not an instant.

    Asserted at both ends of the day because ``as_of.date() >= maturity_date``
    and ``as_of >= maturity_date`` agree at midnight and disagree at every other
    hour of it - so a test at midnight alone would pass under either rule.
    """
    fund = a_fund(maturity_date=DUE)

    assert fund.is_matured(datetime(2026, 6, 1, 0, 0)) is True
    assert fund.is_matured(datetime(2026, 6, 1, 23, 59)) is True
    assert fund.is_matured(datetime(2026, 6, 2, 0, 0)) is True


def test_a_fund_with_a_date_is_not_open():
    """``is_open`` answers "was there ever a date", which is a different question
    from ``is_matured`` - a pot with a past date is matured without being open."""
    fund = a_fund(maturity_date=date(2020, 1, 1))

    assert fund.is_open is False
    assert fund.is_matured(MOMENT) is True


# --- deposits -------------------------------------------------------------


def test_a_deposit_increases_the_balance():
    fund = a_fund(balance="1000")

    fund.deposit(ngn("500"))

    assert fund.balance == ngn("1500")


def test_a_sealed_fund_still_accepts_deposits():
    """The headline rule of the whole feature.

    "I locked 50,000 and want to keep adding without opening a new plan for each
    deposit" - so ``deposit`` has no date check at all. Note the balance keeps
    growing while ``release`` below still refuses: those two facts together are
    the product.
    """
    fund = a_fund(balance="50000", maturity_date=DUE)

    fund.deposit(ngn("2000"))
    fund.deposit(ngn("1500"))

    assert fund.balance == ngn("53500")
    with pytest.raises(FundNotMaturedError):
        fund.release(ngn("1000"), MOMENT)


def test_a_matured_fund_still_accepts_deposits():
    """And so does one whose date has passed - deposits are not gated either way."""
    fund = a_fund(balance="0", maturity_date=date(2020, 1, 1))

    fund.deposit(ngn("500"))

    assert fund.balance == ngn("500")


@pytest.mark.parametrize("amount", ["0", "-1000"])
def test_a_deposit_must_be_positive(amount):
    fund = a_fund(balance="1000")

    with pytest.raises(InvalidAmountError):
        fund.deposit(ngn(amount))

    assert fund.balance == ngn("1000")


def test_a_deposit_must_be_in_the_funds_currency():
    fund = a_fund(balance="1000")

    with pytest.raises(CurrencyMismatchError):
        fund.deposit(Money(Decimal("500"), USD))

    assert fund.balance == ngn("1000")


def test_a_deposit_must_be_money():
    fund = a_fund(balance="1000")

    with pytest.raises(InvalidAmountError):
        fund.deposit(500)


# --- release --------------------------------------------------------------


def test_a_release_moves_money_out():
    fund = a_fund(balance="5000")

    fund.release(ngn("2000"), MOMENT)

    assert fund.balance == ngn("3000")


def test_a_release_from_a_sealed_fund_is_refused():
    fund = a_fund(balance="5000", maturity_date=DUE)

    with pytest.raises(FundNotMaturedError):
        fund.release(ngn("2000"), MOMENT)

    assert fund.balance == ngn("5000")


def test_a_release_on_the_due_date_is_allowed():
    fund = a_fund(balance="5000", maturity_date=DUE)

    fund.release(ngn("2000"), datetime(2026, 6, 1, 0, 0))

    assert fund.balance == ngn("3000")


def test_a_release_of_more_than_the_fund_holds_is_refused():
    fund = a_fund(balance="5000")

    with pytest.raises(InsufficientFundsError):
        fund.release(ngn("5001"), MOMENT)

    assert fund.balance == ngn("5000")


def test_a_release_of_the_whole_balance_empties_the_fund():
    fund = a_fund(balance="5000")

    fund.release(ngn("5000"), MOMENT)

    assert fund.balance == ngn("0")


@pytest.mark.parametrize("amount", ["0", "-1000"])
def test_a_release_must_be_positive(amount):
    """Paying zero is not a payment, and the wallet's draw loop relies on this:
    it skips empty pots rather than asking them for nothing."""
    fund = a_fund(balance="5000")

    with pytest.raises(InvalidAmountError):
        fund.release(ngn(amount), MOMENT)


def test_a_release_must_be_in_the_funds_currency():
    fund = a_fund(balance="5000")

    with pytest.raises(CurrencyMismatchError):
        fund.release(Money(Decimal("500"), USD), MOMENT)


# --- pay ------------------------------------------------------------------


def test_a_personal_fund_may_not_pay_before_its_date():
    """For a personal pot a payment is just spending, and spending is what the
    date exists to stop."""
    fund = a_fund(balance="5000", kind=FundKind.PERSONAL, maturity_date=DUE)

    with pytest.raises(FundNotMaturedError):
        fund.pay(ngn("2000"), MOMENT)

    assert fund.balance == ngn("5000")


def test_a_personal_fund_may_pay_once_it_has_come_due():
    fund = a_fund(balance="5000", kind=FundKind.PERSONAL, maturity_date=DUE)

    fund.pay(ngn("2000"), datetime(2026, 6, 1, 0, 0))

    assert fund.balance == ngn("3000")


def test_a_business_fund_may_pay_before_its_date():
    """The one rule that distinguishes the two kinds, and the reason
    ``FundKind`` exists at all.

    A scheduled obligation does not wait for the float to mature - that is what
    a business pot is for. Phase A never reaches this with an immature pot,
    because a plan cannot name its pot yet; the rule is here for the phase that
    can.
    """
    fund = a_fund(balance="5000", kind=FundKind.BUSINESS, maturity_date=DUE)

    fund.pay(ngn("2000"), MOMENT)

    assert fund.balance == ngn("3000")


def test_a_business_fund_still_refuses_an_early_release():
    """"May pay early" is not "may be emptied early".

    The distinction the kind makes is about *scheduled* money going to a named
    account. Releasing into the available balance is the impulsive route, and a
    business pot is not exempt from a rule about impulsiveness.
    """
    fund = a_fund(balance="5000", kind=FundKind.BUSINESS, maturity_date=DUE)

    with pytest.raises(FundNotMaturedError):
        fund.release(ngn("2000"), MOMENT)

    assert fund.balance == ngn("5000")


def test_a_payment_may_not_overdraw_the_fund():
    fund = a_fund(balance="5000")

    with pytest.raises(InsufficientFundsError):
        fund.pay(ngn("5001"), MOMENT)


@pytest.mark.parametrize("amount", ["0", "-1000"])
def test_a_payment_must_be_positive(amount):
    fund = a_fund(balance="5000")

    with pytest.raises(InvalidAmountError):
        fund.pay(ngn(amount), MOMENT)


# --- extend ---------------------------------------------------------------


def test_extending_pushes_the_date_later():
    fund = a_fund(balance="5000", maturity_date=DUE)

    fund.extend_to(date(2026, 9, 1), MOMENT)

    assert fund.maturity_date == date(2026, 9, 1)
    with pytest.raises(FundNotMaturedError):
        fund.release(ngn("1000"), datetime(2026, 6, 1))


def test_extending_a_matured_fund_re_seals_it():
    """"Extend" on a pot that has already opened is the only way to re-seal one,
    and it is allowed: the owner is choosing to commit money they could release."""
    fund = a_fund(balance="5000", maturity_date=date(2020, 1, 1))

    fund.extend_to(date(2027, 1, 1), MOMENT)

    assert fund.is_matured(MOMENT) is False


def test_extending_to_an_earlier_date_is_refused():
    """Forward-only, and that is the entire product promise.

    Pulling the date earlier is an early release wearing a different hat: it
    would free money the owner committed, by a route that never says "release".
    """
    fund = a_fund(balance="5000", maturity_date=DUE)

    with pytest.raises(MaturityNotExtendedError):
        fund.extend_to(date(2026, 3, 1), MOMENT)

    assert fund.maturity_date == DUE


def test_extending_to_the_same_date_is_refused():
    """Not merely "not earlier" - the date must actually move."""
    fund = a_fund(balance="5000", maturity_date=DUE)

    with pytest.raises(MaturityNotExtendedError):
        fund.extend_to(DUE, MOMENT)

    assert fund.maturity_date == DUE


def test_extending_into_the_past_is_refused():
    """A date that has already passed would look like a lock and behave like
    none, so it is refused even though it is technically "later" than nothing.

    Note the two checks don't overlap here: this pot has no current date, so the
    forward-only check has nothing to compare against, and the past check is the
    only thing standing between a user and a pot that is open the moment it is
    sealed.
    """
    fund = a_fund(balance="5000", maturity_date=None)

    with pytest.raises(MaturityNotExtendedError):
        fund.extend_to(date(2025, 1, 1), MOMENT)

    assert fund.maturity_date is None


def test_a_fund_with_no_date_can_be_sealed():
    """Setting a date on an open pot is allowed, and is how one is re-sealed.

    This is the route the migrated "Locked" pot takes if its owner wants it
    committed for real - the migration leaves it open, and this is the door.
    """
    fund = a_fund(balance="5000", maturity_date=None)

    fund.extend_to(DUE, MOMENT)

    assert fund.maturity_date == DUE
    with pytest.raises(FundNotMaturedError):
        fund.release(ngn("1000"), MOMENT)


def test_extending_a_fund_does_not_move_its_balance():
    fund = a_fund(balance="5000", maturity_date=DUE)

    fund.extend_to(date(2026, 9, 1), MOMENT)

    assert fund.balance == ngn("5000")


def test_an_extended_pot_still_accepts_deposits():
    """Re-sealing is not a freeze - the request's continuous-deposit promise
    survives it."""
    fund = a_fund(balance="5000", maturity_date=DUE)

    fund.extend_to(date(2027, 1, 1), MOMENT)
    fund.deposit(ngn("1000"))

    assert fund.balance == ngn("6000")


def test_extending_to_a_datetime_is_refused():
    """The subclass trap again, on the way in rather than at construction."""
    fund = a_fund(maturity_date=DUE)

    with pytest.raises(InvalidFundMaturityError):
        fund.extend_to(datetime(2026, 9, 1, 9, 0), MOMENT)


# --- presentation ---------------------------------------------------------


def test_a_fund_says_its_name_kind_and_date():
    fund = a_fund(name="Vacation", kind=FundKind.PERSONAL, balance="4000", maturity_date=DUE)

    assert str(fund) == "fund 'Vacation' (personal, until 2026-06-01) 4000.00 NGN"


def test_a_fund_with_no_date_says_it_is_open():
    """Not "until None", which is what a careless f-string would produce."""
    fund = a_fund(name="Locked", balance="4000")

    assert str(fund) == "fund 'Locked' (personal, open) 4000.00 NGN"
