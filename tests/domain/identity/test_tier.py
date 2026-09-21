"""``Tier``, ``LIMITS``, ``check_credit`` and ``check_outflow``: what an account
may move, and why.

Four things are tested here and they are four different kinds of claim.

**One: the table is complete.** ``LIMITS`` is keyed by ``(Tier, Currency)``, and a
missing pair is a ``KeyError`` raised inside a money movement - in production, on
whichever currency nobody thought about, in the code path where an error is worst.
Only an inspection of the whole table catches that, which is why ``LIMITS`` is
exposed rather than hidden behind ``limits_for``. The test below is the reason the
alias exists.

**Two: the boundaries are where the numbers say they are.** Every comparison is
``>`` and not ``>=``, so a movement *exactly at* a ceiling is allowed. That is the
one case a test can get wrong without noticing: a limit of 50,000 that refuses
50,000 is a limit of 49,999.99, and the number in the table would not be the
number in force. ``Session.is_expired`` takes the opposite decision - ``>=``,
because a session is over *at* its expiry - and the two are consistent rather than
contradictory: this is an allowance being spent, and an allowance of 50,000 is
worth 50,000.

**Three: each direction faces only the ceilings that can bind it.** This is the
claim the two functions exist to make, and it is tested as hard as the boundaries
because getting it wrong is silent in both directions: a credit judged against the
daily allowance refuses money arriving for a reason that says "spent today", and a
movement out judged against the balance cap refuses somebody the right to spend
their way back under it. The tests below pin that an outflow cannot be refused by
a balance ceiling and that a credit cannot be refused by the per-transaction one.

**Four: the tier is derived and never stored.** ``tier_for`` reads completeness
off a profile and nothing else. There is no ``set_tier`` to test because there is
none to call, and the tests below are the assertion that completing a profile is
the only route between the two members.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
import uuid

import pytest

from app.domain.identity.exception import TierLimitExceededError
from app.domain.identity.limitKind import LimitKind
from app.domain.identity.profile import Profile
from app.domain.identity.tier import (
    LIMITS,
    Tier,
    check_credit,
    check_outflow,
    limit_day_bounds,
    limits_for,
    tier_for,
)
from app.domain.identity.user import User
from app.domain.money.currency import Currency
from app.domain.money.exception import CurrencyMismatchError, MoneyError
from app.domain.money.money import Money

#: A moment to build fixtures from, naive like every timestamp in this system.
NOON = datetime(2026, 3, 10, 12, 0, 0)


def money(amount: str, currency: Currency = Currency.NGN) -> Money:
    """A ``Money`` from a decimal string, which is how this file writes amounts.

    A helper because ``Money`` refuses a ``str`` and every test below wants to
    write ``"50000.00"`` rather than ``Decimal("50000.00")``. Quoted strings
    rather than float literals throughout, for the reason the table itself uses
    them: ``50000.5`` is a float, and a float is the one thing this codebase does
    not do with money.
    """
    return Money(Decimal(amount), currency)


def a_profile(complete: bool = True) -> Profile:
    """A profile that is either complete or missing exactly one of the four facts.

    ``complete=False`` drops the *date of birth*, and which field is dropped is
    arbitrary - the test that each of the four is individually necessary lives in
    ``test_profile.py``, and what this file cares about is the tier that follows
    from completeness rather than which field produced it.
    """
    return Profile(
        user_id=uuid.uuid4(),
        display_name="Ada",
        legal_first_name="Ada",
        legal_last_name="Lovelace",
        date_of_birth=date(1990, 5, 17) if complete else None,
        phone="+2348000000000",
        country="NG",
        address_line=None,
        created_at=NOON,
        updated_at=NOON,
    )


# --- the table --------------------------------------------------------------


def test_the_limits_table_covers_every_tier_and_every_currency():
    """**The completeness test, and the reason ``LIMITS`` is a public name.**

    The failure this prevents is a ``KeyError`` raised the day somebody adds a
    sixth ``Currency`` member - inside a money movement, on the one currency
    nobody extended the table for, where the error is least welcome and least
    obviously about a table. A test that only exercised the currencies in use
    today would pass on that day and every day until the new one was created.

    Written as an exhaustive product rather than a spot check, because a spot
    check is exactly what would miss the missing pair.
    """
    missing = [
        (tier, currency)
        for tier in Tier
        for currency in Currency
        if (tier, currency) not in LIMITS
    ]

    assert missing == []
    assert len(LIMITS) == len(Tier) * len(Currency)


def test_no_limits_table_entry_is_none():
    """A ``TierLimits`` field may be ``None`` to mean "no ceiling" - and none is.

    ``None`` is a real value in the dataclass, deliberately, so a future tier can
    say "this ceiling does not apply" without a sentinel. Today every tier has all
    three ceilings, and asserting that keeps a ``None`` from appearing by accident
    - which would silently remove a control rather than fail anything, since a
    ``None`` is read as "skip this check".

    This is the test that turns "somebody deleted a number" from a silent
    downgrade into a failure.
    """
    for (tier, currency), limits in LIMITS.items():
        assert limits.per_transaction is not None, (tier, currency)
        assert limits.daily_outflow is not None, (tier, currency)
        assert limits.max_balance is not None, (tier, currency)


def test_the_three_ceilings_rise_together_for_every_currency():
    """A per-transaction ceiling above the daily one would be unreachable.

    The relationship that has to hold is ``per_transaction <= daily_outflow <=
    max_balance``: a single movement that is allowed must not be refused by the
    day, and a day's worth of movements must not be impossible to hold. Asserting
    it over every row is what catches a transposed pair in ``_limits`` - which is
    the mistake the helper's positional strings are shaped to make noticeable, and
    this is the net under it.

    Note it is ``<=`` and not ``<``: equality is a coherent choice, just a tight
    one, and this test is about impossibility rather than about taste.
    """
    for (tier, currency), limits in LIMITS.items():
        assert limits.per_transaction <= limits.daily_outflow, (tier, currency)
        assert limits.daily_outflow <= limits.max_balance, (tier, currency)


def test_identifying_an_account_raises_every_ceiling():
    """The whole point of a tiered programme, asserted for every currency at once.

    Written over currencies rather than against one, because a table where the
    higher tier was worth more in NGN and *less* in the other currency would be a
    typo nobody noticed - the kind of mistake that only shows up as a support
    ticket from the one user holding it. It used to name EUR; decision 267 removed
    that currency rather than this paragraph, which is why the sentence is about a
    set rather than about a member.
    """
    for currency in Currency:
        unverified = limits_for(Tier.UNVERIFIED, currency)
        identified = limits_for(Tier.IDENTIFIED, currency)

        assert unverified.per_transaction < identified.per_transaction, currency
        assert unverified.daily_outflow < identified.daily_outflow, currency
        assert unverified.max_balance < identified.max_balance, currency


def test_a_limit_is_in_the_currency_it_is_keyed_by():
    """The row and the ceiling cannot disagree, which is what keying by currency buys.

    A limit that was stored without its currency would be a number whose meaning
    depended on which wallet it was compared against - and ``Money`` would catch
    the mismatch at comparison time, but only for the comparisons that happened to
    run. Asserting it here means the *table* is wrong rather than a movement.
    """
    for (tier, currency), limits in LIMITS.items():
        assert limits.per_transaction.currency is currency
        assert limits.daily_outflow.currency is currency
        assert limits.max_balance.currency is currency


def test_limits_for_raises_rather_than_defaulting_for_a_pair_it_does_not_have():
    """**The missing pair fails loudly, and this pins that it still does.**

    ``limits_for`` has no default and that is deliberate - a default ceiling is a
    ceiling somebody chose without knowing they chose it. The completeness test
    above is the guard; this is what fails if the guard is ever removed, which is
    a different failure from the guard's and worth having its own test.
    """
    with pytest.raises(KeyError):
        limits_for("not a tier", Currency.NGN)


# --- the tier is derived ----------------------------------------------------


def test_no_profile_is_unverified_and_is_not_an_error():
    """**``None`` is a normal input, not a fault.**

    Every account that existed before this feature has no profile row, so this is
    the answer for the entire installed base on the day this ships - and the shape
    is ``settings.from_environment`` returning ``None``: an absence to be read
    rather than a failure to be raised. It is also what makes "tier 0 for
    everyone on rollout" true without a backfill.
    """
    assert tier_for(None) is Tier.UNVERIFIED


def test_an_incomplete_profile_is_unverified():
    assert tier_for(a_profile(complete=False)) is Tier.UNVERIFIED


def test_a_complete_profile_is_identified():
    assert tier_for(a_profile(complete=True)) is Tier.IDENTIFIED


def test_the_tier_reads_completeness_and_nothing_else():
    """**The single dependency, asserted so it cannot quietly grow a second.**

    A profile's *values* have no bearing on the tier - only whether the four are
    present. If ``tier_for`` ever started inspecting an address, or a country
    against a list, this test would still pass, so it is not a complete guard;
    what it does pin is that a profile differing only in the four fields' contents
    derives the same tier, which is the property the rest of the system relies on.
    """
    one = a_profile()
    two = Profile(
        user_id=uuid.uuid4(),
        display_name="Someone else entirely",
        legal_first_name="Grace",
        legal_last_name="Hopper",
        date_of_birth=date(1906, 12, 9),
        phone="+10000000000",
        country="US",
        address_line="Somewhere",
        created_at=NOON,
        updated_at=NOON,
    )

    assert tier_for(one) is tier_for(two) is Tier.IDENTIFIED


def test_a_verified_number_on_the_account_does_not_identify_the_person():
    """``User.phone`` and ``Profile.phone`` are different fields, and stay different.

    Both hold a number and nothing makes them agree - that drift is a decision
    taken with the phone-signup slice, recorded in the README, and this test is
    where its consequence is pinned. The consequence is that the tier does not
    move: ``tier_for`` takes a profile and has no parameter for an account, so a
    person who proved they hold a handset at signup and has filled in nothing
    else is still ``UNVERIFIED``. That is correct rather than an oversight - a
    tier needs a name and a date of birth too, and the number on the account says
    which account this is, not who the person is.

    The assertion is deliberately *behavioural* rather than a check of
    ``tier_for``'s signature. What would break this is somebody deciding a proven
    number counts towards ``IDENTIFIED``, and that decision would arrive as a
    parameter, a lookup, or a new field on ``Profile`` - all three of which this
    catches and a signature check catches only one of.
    """
    account = User(
        user_id=uuid.uuid4(),
        email=None,
        phone="2348012345678",
        google_subject=None,
        created_at=NOON,
    )
    # Every KYC field named rather than defaulted, because ``Profile`` has no
    # defaults for them - a profile is built from a form submission, and the
    # aggregate takes the six values the form had fields for. ``a_profile`` above
    # does the same thing for the same reason.
    profile = Profile(
        user_id=account.user_id,
        display_name="Chinedu",
        legal_first_name=None,
        legal_last_name=None,
        date_of_birth=None,
        phone=None,
        country=None,
        address_line=None,
        created_at=NOON,
        updated_at=NOON,
    )

    assert account.phone is not None
    assert profile.phone is None
    assert tier_for(profile) is Tier.UNVERIFIED


# --- the boundaries ---------------------------------------------------------
#
# Two functions, two sets of boundaries, and which set a movement meets is the
# direction the money is going. Note what is missing from each: ``check_outflow``
# has no balance ceiling and ``check_credit`` has nothing else, and the section
# after this one is the assertion that neither absence can be reached.


def test_a_movement_out_exactly_at_the_per_transaction_ceiling_is_allowed():
    """**The ``>`` decision, pinned at the ceiling itself.**

    This is the test the whole file exists for. An allowance of 50,000 is worth
    50,000, and an implementation using ``>=`` would refuse this - making the
    limit in force 49,999.99 while the table still said 50,000. Nothing would
    look wrong: the refusals would all still happen, just one unit early, and no
    test that only checked "a big movement is refused" would catch it.
    """
    limit = limits_for(Tier.UNVERIFIED, Currency.NGN).per_transaction

    check_outflow(Tier.UNVERIFIED, amount=limit, outflow_today=money("0.00"))


def test_one_unit_over_is_refused():
    limit = limits_for(Tier.UNVERIFIED, Currency.NGN).per_transaction

    with pytest.raises(TierLimitExceededError) as refused:
        check_outflow(
            Tier.UNVERIFIED,
            amount=limit + money("0.01"),
            outflow_today=money("0.00"),
        )

    assert refused.value.kind is LimitKind.PER_TRANSACTION


def test_a_day_that_lands_exactly_on_the_cap_is_allowed():
    """The same boundary one ceiling over, and it is not the same test.

    ``daily_outflow`` is compared against ``outflow_today + amount`` rather than
    against the movement, so the arithmetic is genuinely different code - and a
    boundary test on one ceiling proves nothing about the other.
    """
    daily = limits_for(Tier.UNVERIFIED, Currency.NGN).daily_outflow
    already = money("199999.99")

    check_outflow(Tier.UNVERIFIED, amount=daily - already, outflow_today=already)


def test_a_day_one_unit_over_the_cap_is_refused():
    daily = limits_for(Tier.UNVERIFIED, Currency.NGN).daily_outflow
    already = money("199999.99")

    with pytest.raises(TierLimitExceededError) as refused:
        check_outflow(
            Tier.UNVERIFIED,
            amount=(daily - already) + money("0.01"),
            outflow_today=already,
        )

    assert refused.value.kind is LimitKind.DAILY_OUTFLOW


def test_a_day_already_at_the_cap_refuses_any_further_movement():
    """The end state of a spent day, which is what the twentieth withdrawal meets.

    The smallest legal movement is refused, and reporting ``attempted`` as the
    day's total *including* this movement is what makes the two fields checkable
    against each other later: ``attempted`` is always the number that was compared
    against ``limit``, whatever the kind.
    """
    daily = limits_for(Tier.UNVERIFIED, Currency.NGN).daily_outflow

    with pytest.raises(TierLimitExceededError) as refused:
        check_outflow(Tier.UNVERIFIED, amount=money("0.01"), outflow_today=daily)

    assert refused.value.kind is LimitKind.DAILY_OUTFLOW
    assert refused.value.attempted == daily + money("0.01")
    assert refused.value.limit == daily


def test_a_balance_exactly_at_the_cap_is_allowed():
    cap = limits_for(Tier.UNVERIFIED, Currency.NGN).max_balance

    check_credit(Tier.UNVERIFIED, balance_after=cap)


def test_a_balance_one_unit_over_the_cap_is_refused():
    cap = limits_for(Tier.UNVERIFIED, Currency.NGN).max_balance

    with pytest.raises(TierLimitExceededError) as refused:
        check_credit(Tier.UNVERIFIED, balance_after=cap + money("0.01"))

    assert refused.value.kind is LimitKind.MAX_BALANCE


def test_the_balance_ceiling_is_judged_after_the_credit_not_before():
    """A wallet below its cap may be filled up to it, and the check is the result.

    ``balance_after`` is the balance the wallet *would hold* - not the balance it
    holds now - so the ceiling is a statement about where the deposit lands
    rather than about where it started. A check against the current balance would
    refuse the last deposit that fits, which is a cap of ``cap - 1`` in disguise.
    """
    cap = limits_for(Tier.UNVERIFIED, Currency.NGN).max_balance

    # One unit below the cap, and a deposit that lands exactly on it.
    check_credit(Tier.UNVERIFIED, balance_after=cap - money("0.01") + money("0.01"))

    with pytest.raises(TierLimitExceededError):
        check_credit(Tier.UNVERIFIED, balance_after=cap + money("0.01"))


# --- the ceilings a direction does not face ---------------------------------
#
# The two tests below are the reason there are two functions rather than one with
# a direction flag. Each absence is silent when it is honoured and catastrophically
# quiet when it is not: a balance ceiling on the way out freezes an over-cap
# account - it cannot spend down, because every movement leaving it still over the
# cap is refused - and a per-transaction ceiling on the way in refuses a payment
# nobody receiving it decided the size of.


def test_an_outflow_is_never_refused_by_the_balance_ceiling():
    """**The trap, stated as a test.** Spending down must always be possible.

    The wallet below holds 350,000 against a cap of 300,000, and withdraws 1,000
    of it. A check that judged the resulting balance would refuse - 349,000 is
    still above the cap - and the account would be stuck: every movement out of it
    would leave it over, so every movement out would be refused. The ceiling has
    nothing to say about money leaving, which is why ``check_outflow`` does not
    look at a balance at all.
    """
    with pytest.raises(TierLimitExceededError):
        # The same wallet, being credited: refused, and correctly.
        check_credit(Tier.UNVERIFIED, balance_after=money("350000.00"))

    # Being debited: allowed, and the refusal above is not a state it can be in.
    check_outflow(
        Tier.UNVERIFIED, amount=money("1000.00"), outflow_today=money("0.00")
    )


def test_a_credit_is_never_refused_by_the_per_transaction_ceiling():
    """A deposit may be larger than any single movement out of the same wallet.

    Filling a wallet to its cap in one payment is allowed; emptying it in one
    withdrawal is not. The same number, two answers - because a movement out is
    what a person decides the size of, and money arriving is what somebody else
    decides. The assertion that the two numbers really do differ is one test
    above, in ``test_the_three_ceilings_rise_together_for_every_currency``.
    """
    cap = limits_for(Tier.UNVERIFIED, Currency.NGN).max_balance

    check_credit(Tier.UNVERIFIED, balance_after=cap)

    with pytest.raises(TierLimitExceededError) as refused:
        check_outflow(Tier.UNVERIFIED, amount=cap, outflow_today=money("0.00"))

    assert refused.value.kind is LimitKind.PER_TRANSACTION


# --- which refusal wins ------------------------------------------------------


def test_the_first_breached_ceiling_is_the_one_reported():
    """**The ordering, asserted, because "first wins" is a decision not a default.**

    A movement out that breaches both ceilings it faces is reported as a
    per-transaction breach - *send less* - rather than as a daily one, because
    that is the remedy which works and the one a person can act on immediately.
    Checking both and reporting the worse would be more complete and less useful:
    a caller fixes one thing and tries again, and naming the breach furthest from
    passing does not tell them which to fix.

    The two values below are contrived to breach both at once, which is exactly
    the situation where a different implementation would report a different kind
    and no other test would notice.
    """
    with pytest.raises(TierLimitExceededError) as refused:
        check_outflow(
            Tier.UNVERIFIED,
            amount=money("999999.00"),  # over per-transaction
            outflow_today=money("999999.00"),  # and the day is already blown
        )

    assert refused.value.kind is LimitKind.PER_TRANSACTION


def test_a_movement_out_under_every_ceiling_it_faces_is_allowed():
    check_outflow(
        Tier.UNVERIFIED, amount=money("1000.00"), outflow_today=money("1000.00")
    )


def test_a_credit_under_the_cap_is_allowed():
    check_credit(Tier.UNVERIFIED, balance_after=money("2000.00"))


def test_a_movement_is_judged_against_its_own_tier_and_not_a_higher_one():
    """The same movement, two tiers, two answers - the feature in one assertion.

    A movement that is refused for an unverified account is permitted for an
    identified one, and if this test passed for both the limits table would be
    decorative. It is written as a pair for that reason: asserting only the
    refusal would pass for a system with no tiers at all.
    """
    amount = limits_for(Tier.IDENTIFIED, Currency.NGN).per_transaction

    with pytest.raises(TierLimitExceededError):
        check_outflow(Tier.UNVERIFIED, amount=amount, outflow_today=money("0.00"))

    check_outflow(Tier.IDENTIFIED, amount=amount, outflow_today=money("0.00"))


def test_a_credit_is_judged_against_its_own_tier_and_not_a_higher_one():
    """The same pairing on the other side, because these are two rules and not one.

    A deposit that fits an identified account's cap is refused a tier down.
    Written as a pair for the reason above, and kept separate from it because a
    boundary proven in one direction proves nothing about the other.
    """
    identified = limits_for(Tier.IDENTIFIED, Currency.NGN).max_balance

    with pytest.raises(TierLimitExceededError):
        check_credit(Tier.UNVERIFIED, balance_after=identified)

    check_credit(Tier.IDENTIFIED, balance_after=identified)


def test_a_movement_in_one_currency_is_never_judged_against_anothers_limits():
    """``Money`` refuses the comparison, which is the check that costs nothing.

    ``check_outflow`` builds ``outflow_today + amount`` and compares against a
    limit keyed by ``amount.currency``. A caller that assembled those values from
    two different wallets is refused by ``Money`` mid-function rather than
    silently compared - a ``CurrencyMismatchError`` and not a ``TierLimit`` one,
    which is the honest report: nothing about a tier was breached.

    The credit check has no counterpart below, and that is worth noticing rather
    than leaving as a gap: it reads its limits from the balance's own currency and
    takes no second amount, so there is no pair for it to get wrong.
    """
    with pytest.raises(CurrencyMismatchError):
        check_outflow(
            Tier.UNVERIFIED,
            amount=money("100.00", Currency.NGN),
            outflow_today=money("100.00", Currency.USD),
        )


# --- the refusal carries what an audit needs --------------------------------


def test_the_refusal_carries_the_kind_the_limit_and_what_was_attempted():
    """**Not a bare sentence** - ``RunBlockReason``'s argument at the exception.

    The question an operator asks afterwards is countable: "how many withdrawals
    were refused for the daily cap this week?" A sentence cannot be grouped by,
    and a client switches on the kind because the remedy differs per kind. So the
    three values are attributes rather than prose, and this test is the assertion
    that they survive the raise.
    """
    limit = limits_for(Tier.UNVERIFIED, Currency.NGN).per_transaction

    with pytest.raises(TierLimitExceededError) as refused:
        check_outflow(
            Tier.UNVERIFIED,
            amount=limit + money("1.00"),
            outflow_today=money("0.00"),
        )

    error = refused.value
    assert error.kind is LimitKind.PER_TRANSACTION
    assert error.limit == limit
    assert error.attempted == limit + money("1.00")


def test_a_limit_refusal_is_a_money_error():
    """**Load-bearing, not tidy.**

    ``WalletService._run`` catches ``MoneyError`` to record a refusal in the audit
    trail and commit it. An exception root outside that tree would be a refusal
    the ledger never hears about - which is the one thing a financial control must
    not be. The test is one line because the claim is one line, and it is the only
    thing standing between this feature and a silent refusal.
    """
    assert issubclass(TierLimitExceededError, MoneyError)


# --- the limit day -----------------------------------------------------------


def test_the_limit_day_is_a_half_open_window_bounded_by_midnight():
    start, end = limit_day_bounds(NOON)

    assert start == datetime(2026, 3, 10, 0, 0, 0)
    assert end == datetime(2026, 3, 11, 0, 0, 0)


def test_a_moment_exactly_at_midnight_belongs_to_the_day_that_is_beginning():
    """**``[start, end)`` and not ``(start, end]``, pinned at the seam.**

    A movement stamped exactly at midnight belongs to the day beginning, which is
    the reading a person has and the one that makes the two days partition every
    moment exactly once. The alternative double-counts or drops the boundary
    instant depending on which comparison is written, and a boundary instant is
    exactly the value a test can get wrong without noticing.
    """
    midnight = datetime(2026, 3, 10, 0, 0, 0)
    start, end = limit_day_bounds(midnight)

    assert start == midnight
    assert end == datetime(2026, 3, 11, 0, 0, 0)


def test_the_window_is_exactly_one_day_wide_at_every_hour():
    """Every moment in a day names the same window, which is what makes it a day.

    A window that varied with the time of day - a sliding 24 hours, say - would
    mean the allowance never reset at a boundary anybody could predict, and a
    person could not tell when their day began. Asserting the width is constant
    across the day is the cheap version of that claim.
    """
    day = date(2026, 3, 10)
    first = limit_day_bounds(datetime(2026, 3, 10, 0, 0, 0))
    last = limit_day_bounds(datetime(2026, 3, 10, 23, 59, 59, 999999))

    assert first == last
    assert first[1] - first[0] == timedelta(days=1)
    assert first[0].date() == day


def test_the_window_ignores_the_microseconds_it_was_given():
    """Because a query bound is compared with ``>=`` and ``<``, not for equality.

    A start carrying microseconds would still work for the upper end of a day and
    would silently drop the first fraction of a second for the lower - one row
    unaccounted for, once, in a case nobody would reproduce.
    """
    start, end = limit_day_bounds(datetime(2026, 3, 10, 12, 0, 0, 123456))

    assert start.microsecond == 0
    assert end.microsecond == 0
