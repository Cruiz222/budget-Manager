"""What an account is allowed to move, and how much of it.

The answer to "what may this account do" is a tier, and the answer to "how much"
is a table beside it. Both are **domain constants** rather than environment
settings or rows in a table, and the argument is ``settings.py``'s own: it says
configuration is not data, and nothing in the database is a setting - a row that
changed what the software did "would be the first of its kind, and it would need
its own interface, its own migration and its own validation for no gain". A limit
is a rule, not an installation detail. Two deployments of this software should
refuse the same movement, and the only way to guarantee that is for the number to
be in the code.

The cost is honest and worth naming: raising a limit is a release. For a
financial cap that is the correct trade - a limit that could be edited at
runtime by somebody with database access is a control an attacker who reached the
database can also turn off, and the whole point of a tier limit is that it holds
when something else has already gone wrong.

**Tier is derived, never stored.** ``tier_for`` reads it off a profile. A stored
tier is a column that can disagree with the profile it describes, and the
disagreement would present as an account reading "identified" on an empty
profile - which is the state a stored flag drifts into the first time somebody
writes one without the other. Deriving it makes that unrepresentable rather than
unlikely, and it is the same reasoning ``User`` uses for keeping credentials off
the identity.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum

from app.domain.money.currency import Currency
from app.domain.money.money import Money

from .exception import TierLimitExceededError
from .limitKind import LimitKind
from .profile import Profile


class Tier(Enum):
    """How much this system knows about who an account holder is.

    Two members rather than the three or four a matured KYC programme has, and
    the reason is that the third one is not reachable yet: a *verified* tier
    needs an identity provider to verify against, and there is none. The members
    here are the two states this system can actually tell apart today.

    Adding a member is free, and the note in ``RunBlockReason`` applies
    unchanged: the persisted contract is the member's *name*, so adding one is
    free, renaming one is a data migration, and *widening* what an existing one
    means is free only so long as every row already written stays true under the
    new meaning.
    """

    UNVERIFIED = "unverified"
    """No profile, or one that is missing something an identity check would confirm."""

    IDENTIFIED = "identified"
    """A complete profile: both halves of a legal name, a date of birth, a phone, a country.

    **The honest reading of this member is "we have their details", not "we
    checked them".** Nothing has verified that the name given is the holder's
    own, that the phone number reaches them, or that the date of birth is real -
    a complete profile is self-asserted. It is named for what it records rather
    than for what a reader might assume, and the tier that means *verified*
    arrives with the provider that verifies it.
    """


@dataclass(frozen=True)
class TierLimits:
    """The three ceilings a tier imposes, each ``None`` for "no ceiling".

    Three rather than one because they stop three different things, and a caller
    that had only a per-transaction ceiling would still allow every one of the
    failures the other two exist for:

    - ``per_transaction`` stops a single movement being enormous.
    - ``daily_outflow`` stops a thousand small ones adding up to the same thing,
      which is the bypass a per-transaction ceiling alone invites.
    - ``max_balance`` stops value accumulating in an account nobody has
      identified, which is the state a money-laundering or mule account is in.

    ``None`` is a real value and not a placeholder - it is how a tier says "this
    ceiling does not apply", and it is spelled ``None`` rather than a sentinel
    or a very large number so that a reader cannot mistake absence for a limit
    they have not found yet.
    """

    per_transaction: Money | None
    daily_outflow: Money | None
    max_balance: Money | None


def _limits(
    per_transaction: str, daily_outflow: str, max_balance: str, currency: Currency
) -> TierLimits:
    """One ``(tier, currency)`` row, from three strings.

    A helper rather than a dict literal per currency so the three numbers are
    read in the same order everywhere and a transposed pair is impossible to
    write without noticing - three positional strings type-check identically
    whatever order they arrive in, and a confused ``per_transaction`` with a
    ``max_balance`` would be a ceiling nobody could explain.

    **The strings are converted here rather than at the call sites**, and the
    conversion is not optional: ``Money`` accepts an ``int`` or a ``Decimal`` and
    *refuses a ``str``* (``InvalidMoneyOperationError``), and it refuses it at
    construction - which for this table is module import time, since ``_LIMITS``
    below is a module-level constant. So the literals stay quoted for the reason
    ``serialization.text_to_money`` keeps them quoted - a decimal string is exact
    where a float literal is not - and ``Decimal`` is what carries them across
    the last step. Writing the numbers unquoted instead would work for
    ``50000`` and would silently make ``50000.5`` a float, which is the one thing
    this codebase does not do with money.
    """
    return TierLimits(
        per_transaction=Money(Decimal(per_transaction), currency),
        daily_outflow=Money(Decimal(daily_outflow), currency),
        max_balance=Money(Decimal(max_balance), currency),
    )


#: The currency every other column is derived from, and the reason it is not a rate.
#:
#: **No conversion is ever performed, and the four columns below are written out
#: rather than calculated.** ``Money`` refuses arithmetic between two currencies
#: (``CurrencyMismatchError``), so a limit scaled by an exchange rate would need
#: a rate, and this system has none - no FX table, no rate provider, no moment at
#: which a rate could be said to be current. A limit computed from a rate would
#: silently change its meaning the day the rate moved, and a ceiling that moves on
#: its own is not a ceiling.
#:
#: So each currency's numbers are a deliberate choice, and the non-NGN ones are
#: *conservative*: they are set lower than the naira column rather than converted
#: from it, because the currencies this system does not actually collect in are
#: the ones it should be most careful about - see ``### Still open`` in the README
#: on the provider hard-coding ``NGN`` while wallets in four other currencies are
#: creatable. A USD wallet's deposits are already broken at the far end; it should
#: not also be the loose one.
_LIMITS: dict[tuple[Tier, Currency], TierLimits] = {
    # The row that carries almost all the traffic, and the one worth arguing
    # about. Every existing account is here on rollout (there is no profile for
    # any of them yet), so these numbers decide whether an account that is moving
    # money today keeps working tomorrow.
    #
    # 50,000 per movement and 200,000 a day. Chosen as the point below which a
    # person's ordinary use of this product is untouched - a savings plan
    # contribution, a withdrawal, a payout - and above which somebody should have
    # given their name. Deliberately not so low that the limit is the first thing
    # a new user meets: a tier 0 that refuses ordinary use teaches people to
    # complete a profile to stop being annoyed, which is exactly the wrong reason
    # to hand over a date of birth.
    #
    # The balance cap is 300,000, which is three days at the daily cap and so
    # can be reached rather than being decorative - a cap a person cannot reach
    # is a cap that is never tested.
    (Tier.UNVERIFIED, Currency.NGN): _limits(
        "50000.00", "200000.00", "300000.00", Currency.NGN
    ),
    # NGN, and then the same shape with the numbers held low on purpose. See
    # above: these are choices, not conversions.
    (Tier.UNVERIFIED, Currency.USD): _limits(
        "500.00", "2000.00", "3000.00", Currency.USD
    ),
    (Tier.UNVERIFIED, Currency.GHS): _limits(
        "5000.00", "20000.00", "30000.00", Currency.GHS
    ),
    (Tier.UNVERIFIED, Currency.KES): _limits(
        "50000.00", "200000.00", "300000.00", Currency.KES
    ),
    (Tier.UNVERIFIED, Currency.EUR): _limits(
        "500.00", "2000.00", "3000.00", Currency.EUR
    ),
    # A complete profile. Roughly an order of magnitude up, which is the shape a
    # tiered programme has in practice: the step between "we have your name" and
    # "we have not checked your name" is worth more than any specific number, and
    # the second step - to a verified tier - is the one that will want a provider.
    (Tier.IDENTIFIED, Currency.NGN): _limits(
        "500000.00", "2000000.00", "3000000.00", Currency.NGN
    ),
    (Tier.IDENTIFIED, Currency.USD): _limits(
        "5000.00", "20000.00", "30000.00", Currency.USD
    ),
    (Tier.IDENTIFIED, Currency.GHS): _limits(
        "50000.00", "200000.00", "300000.00", Currency.GHS
    ),
    (Tier.IDENTIFIED, Currency.KES): _limits(
        "500000.00", "2000000.00", "3000000.00", Currency.KES
    ),
    (Tier.IDENTIFIED, Currency.EUR): _limits(
        "5000.00", "20000.00", "30000.00", Currency.EUR
    ),
}

#: The table itself, so that a test can assert it covers every ``(Tier, Currency)``.
#:
#: Exposed rather than kept private behind ``limits_for`` because the test that
#: matters most about it is a *completeness* one: the failure this prevents is a
#: ``KeyError`` raised in production the day somebody adds a sixth ``Currency``
#: member, and only an inspection of the whole table can catch that. The accessor
#: below is for callers; this is for the assertion.
LIMITS = _LIMITS


def limits_for(tier: Tier, currency: Currency) -> TierLimits:
    """The ceilings this tier imposes on money held in ``currency``.

    ``KeyError`` rather than a default when the pair is missing, deliberately -
    a fallback would be the quiet version of the bug this table's completeness
    test exists to catch, and a default ceiling is a ceiling somebody chose
    without knowing they chose it. The test is the guard; this is what fails
    loudly if the guard is ever removed.
    """
    return LIMITS[(tier, currency)]


def tier_for(profile: Profile | None) -> Tier:
    """The tier an account is at, from the profile it may or may not have.

    **``None`` is a normal input and not an error**, which is the shape
    ``settings.from_environment`` returns ``None`` in: every account that existed
    before this feature has no profile row, and the absence of one is a state to
    be read rather than a fault to be raised. It answers ``UNVERIFIED``, which is
    both the safe answer and the true one - nothing about that account has been
    given to this system at all.

    The decision is ``Profile.is_complete``'s and not this function's, so that
    "what makes a profile complete" has one home. What is decided *here* is that
    completeness is the only thing the tier depends on.
    """
    if profile is None or not profile.is_complete:
        return Tier.UNVERIFIED
    return Tier.IDENTIFIED


def limit_day_bounds(as_of: datetime) -> tuple[datetime, datetime]:
    """The limit-day ``as_of`` falls in, as a half-open ``[start, end)`` pair.

    **The day is the server's local day, and that is a known simplification
    rather than an oversight.** Every timestamp in this system is a naive
    ``datetime`` written from a presentation's ``datetime.now()`` - there is no
    timezone anywhere in the codebase, no column that holds an offset, and no
    moment at which one is recorded. So the stored rows are in the server's
    wall-clock frame, and the boundary between one day and the next is only
    meaningful in that same frame. Applying a Lagos offset to a value that is
    already local would shift it twice and put the boundary in the wrong place
    on purpose.

    What that costs, stated plainly: a user in a different timezone from the
    server gets their allowance reset at a moment that is not their midnight.
    The remedy is a timezone concept this system does not have, and inventing
    one here would mean every stored moment disagreeing with every new one. It
    is recorded in the README as open.

    ``end`` is exclusive and ``start`` inclusive, so a movement stamped exactly at
    midnight belongs to the day that is beginning - which is the reading a person
    has, and the one that makes the two days partition every moment exactly once.
    """
    start = as_of.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def check_credit(tier: Tier, balance_after: Money) -> None:
    """Refuse value arriving that would leave the wallet above its cap.

    **A credit is judged by the balance it would produce, and by nothing else.**
    The asymmetry with ``check_outflow`` below is the design rather than an
    omission. The balance cap is what stops value accumulating in an account
    nobody has identified, and accumulation is the whole of the concern on this
    side: one large deposit into an empty wallet is bounded by the same number as
    a hundred small ones, so a per-transaction ceiling or a daily allowance would
    add nothing that this does not already say. The three fields of
    ``TierLimits`` are not three interchangeable dials - each stops a specific
    failure, and this is the one that faces inbound value.

    The comment on the unverified row of the table is the other half of the
    argument for keeping this narrow: a tier 0 that gets in the way of ordinary
    use teaches people to hand over a date of birth to stop being annoyed, which
    is the wrong reason to give it. ``50000.00`` per *outgoing* movement is a
    ceiling a person meets by spending; the same ceiling on incoming money is one
    they meet by being paid, which is not their decision to make.

    ``balance_after`` is the balance the wallet *would hold* - available and
    locked together, because a pot is money this account still holds - so this
    check cannot freeze a wallet that is above its cap. Value leaving is judged
    by ``check_outflow``, which has no balance ceiling in it at all.
    """
    limits = limits_for(tier, balance_after.currency)

    if limits.max_balance is not None and balance_after > limits.max_balance:
        raise TierLimitExceededError(
            LimitKind.MAX_BALANCE,
            limit=limits.max_balance,
            attempted=balance_after,
            message=(
                f"this would leave {balance_after} in the wallet, above the "
                f"{limits.max_balance} maximum balance for this account"
            ),
        )


def check_outflow(tier: Tier, amount: Money, outflow_today: Money) -> None:
    """Refuse a movement out that breaches a ceiling of ``tier``. Otherwise return.

    A free function rather than a method on either aggregate, because the rule
    spans the wallet and the account and **neither can hold it**. The precedent is
    written down one layer up in ``execute_plan_run.py``: "Neither aggregate can
    hold a rule that spans both, so the use case holds it." The rule itself is
    pure arithmetic over three values, so it lives in the domain and the
    application layer - which is the only place that can load a wallet and a
    profile at once - supplies them.

    **The two ceilings are checked in the order a person would expect to meet
    them, and the first breach wins**: is this single movement too big, has this
    day already spent too much. Checking both and reporting the worse would be
    more complete and less useful - a caller is going to fix one thing and try
    again, and naming the one furthest from passing does not tell them which to
    fix first.

    **There is no balance ceiling on this side, and that is deliberate.** The
    maximum balance is a statement about what a wallet may hold, and a movement
    out can only lower it - so applying it here could only ever refuse to let
    somebody spend down towards a cap they are already above, which turns the
    control into a trap. See ``check_credit`` for the ceiling that faces value
    arriving, and note that the two functions are the whole of the rule: a
    movement faces the ceilings that can bind it and no others.

    **Each comparison is ``>`` and not ``>=``**, so a movement *exactly at* the
    ceiling is allowed. The boundary is the one case worth being explicit about,
    because it is the only one a test can get wrong without noticing: a limit of
    50,000 that refuses 50,000 is a limit of 49,999.99, and the number in the
    table would not be the number in force. ``Session.is_expired`` takes the
    opposite decision (``>=``, because a session is over *at* its expiry) and the
    two are consistent: this is an allowance being spent, and an allowance of
    50,000 is worth 50,000.

    ``outflow_today`` is the wallet's external outflow over the limit-day *so
    far*, and the caller must read it before the movement it is judging writes
    its own row. A PENDING row counts toward the day - see
    ``TransactionRepository.outflow_total_between`` - so a total read afterwards
    would include the movement in its own allowance and refuse at half the cap.

    ``Money``'s arithmetic refuses two currencies mixed, so a caller that
    assembled these values from two wallets is refused by ``Money`` rather than
    silently compared - which is the check that costs nothing to rely on.
    """
    limits = limits_for(tier, amount.currency)

    if limits.per_transaction is not None and amount > limits.per_transaction:
        raise TierLimitExceededError(
            LimitKind.PER_TRANSACTION,
            limit=limits.per_transaction,
            attempted=amount,
            message=(
                f"{amount} is above the {limits.per_transaction} "
                f"per-transaction limit for this account"
            ),
        )

    if limits.daily_outflow is not None:
        spent_today = outflow_today + amount
        if spent_today > limits.daily_outflow:
            raise TierLimitExceededError(
                LimitKind.DAILY_OUTFLOW,
                limit=limits.daily_outflow,
                attempted=spent_today,
                message=(
                    f"{spent_today} would exceed the {limits.daily_outflow} "
                    f"daily limit for this account, of which {outflow_today} "
                    "has already been spent today"
                ),
            )
