from dataclasses import dataclass, field
from datetime import date, datetime
import uuid

from .exception import (
    CurrencyMismatchError,
    FundNotMaturedError,
    InvalidAmountError,
    InvalidFundBalanceError,
    InvalidFundCreatedAtError,
    InvalidFundFirstFundedAtError,
    InvalidFundIDError,
    InvalidFundKindError,
    InvalidFundMaturityError,
    InvalidFundNameError,
    InvalidFundSealedAtError,
    InsufficientFundsError,
    MaturityNotExtendedError,
)
from .fundKind import FundKind
from .money import Money


@dataclass
class Fund:
    """A named pot of locked money, with a date it comes due.

    A fund is not a second wallet and not a separate account. It is a *breakdown*
    of the wallet's locked balance, which is why it is part of the ``Wallet``
    aggregate rather than an aggregate of its own: the wallet's ``locked_balance``
    is defined as the sum of its funds, so there is no second number to keep in
    step and nothing to reconcile.

    **Deposits are never refused for a maturity reason.** That is the point of
    the thing. The request that produced this class was "I locked 50,000; I want
    to keep adding to it without opening a new plan for every deposit" - so
    ``deposit`` has no date check at all, and a fund can be fed forever. What the
    maturity date gates is money *leaving*.

    **``maturity_date`` is a day, and ``None`` is a real value.** ``None`` means
    "no maturity" - the pot is open and always has been - and it is deliberately
    *not* "unknown" or "never". It is the state every pre-existing locked balance
    is migrated into, which is what makes that migration invisible: money locked
    before funds existed stays exactly as releasable as it was.

    The two kinds differ on one question only, and it is asked in ``pay``:

        may this pot fund a *scheduled* payout before its date?

    A ``BUSINESS`` pot may - that is what it is for: a scheduled obligation does
    not wait for the float to mature. A ``PERSONAL`` pot may not, by any route.

    **But the exemption is not free, and that is what ``first_funded_at`` and
    ``sealed_at`` are for.** Answering "yes" to the question above is what a
    person in charge of paying people would be tempted to do with their own
    money - lock it, change their mind, and redirect the payment to a secondary
    account. So the pot asks a second question first (``authorises_early_payout``):

        was this commitment made *before* the money was?

    Only money that arrived after the commitment did may leave early. The
    ordering that falls out of it is one sentence, and it is the whole feature:

        **seal the pot -> commit money to it -> fund it**

    Money that is already in the pot has no commitment that predates it, so it
    waits for its date like any other. ``sealed_at`` is in that ordering because
    moving a date later with ``extend_to`` is a *re-sealing*: it pushes the pot's
    terms past the commitment, and the exemption lapses with them.
    """

    fund_id: uuid.UUID
    name: str
    kind: FundKind
    _balance: Money
    maturity_date: date | None = None
    #: When this pot's *current* maturity date came into force: the moment it was
    #: opened, and again every moment ``extend_to`` moves the date. It is one
    #: half of what ``authorises_early_payout`` compares, and the half that makes
    #: an extension re-seal the pot against a commitment already made.
    sealed_at: datetime = field(default_factory=datetime.now)
    #: The moment money first arrived, or ``None`` while the pot is empty. It is
    #: stamped once, by the first ``deposit``, and never again - a pot funded
    #: continuously has one funding moment, not one per deposit.
    #:
    #: It is deliberately the *first* funding and not the most recent, and the
    #: difference is the whole anti-temptation guarantee. "Funded at" would let
    #: anyone unlock a sealed pot by creating the plan and then depositing a
    #: token amount to reset the clock. The first funding can never move later,
    #: so that door does not exist.
    first_funded_at: datetime | None = None
    #: Fixes the order in which pots are drawn on when a payout has no pot named
    #: - see ``Wallet.payout_from_locked``. A moment, not a date: two pots opened
    #: on the same day still have a stable order, so the draw is deterministic
    #: rather than merely "whatever SQLite returned".
    created_at: datetime = field(default_factory=datetime.now)

    @property
    def balance(self) -> Money:
        return self._balance

    @property
    def is_open(self) -> bool:
        """Whether this pot has no maturity date at all.

        A different question from ``is_matured``: an open pot *is* matured, but a
        pot with a past date is matured without being open. ``is_open`` is here
        because "this one never had a date" is what ``extend_to`` needs to
        distinguish, and asking it of a date is clearer than comparing to None at
        every call site.
        """
        return self.maturity_date is None

    # --- asking -------------------------------------------------------------

    def is_matured(self, as_of: datetime) -> bool:
        """Whether money may leave this pot as of ``as_of``.

        The moment is reduced to its day, so a pot maturing on 1 June is open for
        the whole of 1 June and not merely from midnight onwards in some other
        timezone's sense of it. This is the same reduction ``SavingsPlan`` makes
        of a run's moment against ``ends_on``, and for the same reason: a
        maturity is a date a human wrote, so it is a whole day.
        """
        if self.maturity_date is None:
            return True
        return as_of.date() >= self.maturity_date

    # --- moving money -------------------------------------------------------

    def deposit(self, amount: Money, as_of: datetime) -> None:
        """Add money to the pot, at any time, before or after its date.

        No maturity check and no wallet-status check: both of those guard money
        *leaving*, and this is money arriving. A closed wallet is refused by the
        wallet, which is the only place that knows it is closed.

        ``as_of`` is here for one reason, and it is not a check: this is where
        ``first_funded_at`` is stamped. Only the first call does it, so a pot fed
        forever still has exactly one funding moment - see the field for why that
        has to be the first and not the latest.
        """
        self._check_amount(amount)
        if self.first_funded_at is None:
            self.first_funded_at = as_of
        self._balance = self._balance + amount

    def release(self, amount: Money, as_of: datetime) -> None:
        """Move money out of the pot, back to the wallet's available balance.

        Refused before the maturity date, for **both** kinds. This is the move
        the product rule is about: money you reserved is not money you may spend
        on impulse, and "release" is precisely the door impulse would use.
        """
        self._refuse_if_sealed(as_of)
        self._check_withdrawable(amount)
        self._balance = self._balance - amount

    def pay(self, amount: Money, as_of: datetime, committed_at: datetime | None = None) -> None:
        """Spend money out of the pot, to an external account.

        A ``BUSINESS`` pot may do this before its date - a scheduled obligation
        does not wait for the float to mature, and refusing would make the kind
        pointless. A ``PERSONAL`` pot may not, because for it a payment is just
        spending, and spending is what the date is there to stop.

        Note this rule is about the *pot*, not the caller. Whether a given price
        counts as "scheduled" is the plan's business, not the pot's; by the time
        a caller reaches here it has already decided this pot is the right one to
        spend from, and the pot only answers for itself.

        ``committed_at`` is the moment the caller's commitment was made - for a
        plan, its creation. It is passed rather than derived because the pot
        cannot see a plan; what the pot *can* do is judge whether a commitment
        made then is old enough to authorise this. A hand-typed payout has no
        commitment at all and passes ``None``, which is why one can never spend a
        sealed business pot early: an ad-hoc payout is not a scheduled payment.

        **The date is asked before the amount, and that order is the decision
        rather than the layout.** A pot that may not be spent at all cannot be
        helped by a well-formed amount, so "locked until 1 June" is the useful
        answer to ``pay(-500)`` and "amount must be positive" is not - the second
        sends someone off to fix a typo that would not have freed the money. Both
        statements are true; only one of them tells the caller what to do next.
        Reversing these two lines would be a silent change to what a user is
        told, which is why it is written down here (see decision 44).
        """
        if not self.is_matured(as_of) and not self.authorises_early_payout(
            committed_at
        ):
            self._refuse_if_sealed(as_of)
        self._check_withdrawable(amount)
        self._balance = self._balance - amount

    def authorises_early_payout(self, committed_at: datetime | None) -> bool:
        """Whether a commitment made at ``committed_at`` may spend this pot early.

        The anti-temptation rule, in one place, so that the pre-flight and the
        operation that actually moves the money cannot answer it differently -
        see ``ExecutePlanRun._blocking_reason``, which asks this exact method.

        It is false unless all three hold, and each one is a different refusal:

          - the pot is a ``BUSINESS`` one. A personal pot pays early by no route.
          - there *is* a commitment. ``None`` means the caller has none, which is
            every hand-typed payment.
          - the ordering is ``sealed_at <= committed_at <= first_funded_at``: the
            pot's terms were in force, the commitment was made, and only then did
            the money arrive.

        That middle comparison is what an ``extend_to`` breaks. Pushing the date
        out moves ``sealed_at`` past a commitment that was already made, the
        ordering stops holding, and the pot waits for the new date - which is
        what "extend" has always meant, now applied to a committed pot too.

        The third comparison is what a pot funded before the plan breaks, and it
        is the case the rule exists for: money already in the pot has no
        commitment that predates it.
        """
        if self.kind is not FundKind.BUSINESS or committed_at is None:
            return False
        if self.first_funded_at is None:
            return False
        return self.sealed_at <= committed_at <= self.first_funded_at

    def extend_to(self, new_date: date, as_of: datetime) -> None:
        """Move the maturity date later. Never earlier.

        Forward-only, and that is the whole rule rather than a detail. Pulling a
        date *earlier* is an early release wearing a different hat: it would free
        money the owner committed, by a route that never mentions the word
        "release". Refusing it is what keeps "locked until 1 June" a promise
        rather than a preference.

        Two checks, because they catch different mistakes. The date must be in the
        future (re-sealing a pot to a day that has already passed would look like
        a lock and behave like none), and it must be later than the current date
        if there is one (otherwise "extend" would be shortening).

        Setting a date on a pot that has none *is* allowed: that is how an open
        pot - including the migrated one holding every pre-existing locked
        balance - is turned into a real commitment.

        **It also re-stamps ``sealed_at``**, and that is not bookkeeping. Moving
        the date later re-seals the pot against *everything*, including a
        commitment already made to a scheduled payout: the ordering
        ``authorises_early_payout`` checks no longer holds, so a business pot
        that was paying early on schedule stops and waits for the new date. That
        is the ruling - an extension is the owner saying "not yet" and being held
        to it - and it is deliberately loud rather than silent: a plan that runs
        into it is *blocked*, recording a reason and queueing a receipt, not
        quietly skipped.
        """
        if not isinstance(new_date, date) or isinstance(new_date, datetime):
            raise InvalidFundMaturityError(
                f"maturity must be a date or None, got {type(new_date).__name__}"
            )
        if new_date <= as_of.date():
            raise MaturityNotExtendedError(
                f"{new_date.isoformat()} is not in the future; "
                "there is nothing to extend to"
            )
        if self.maturity_date is not None and new_date <= self.maturity_date:
            raise MaturityNotExtendedError(
                f"a maturity date can only be moved later: "
                f"{new_date.isoformat()} is not after "
                f"{self.maturity_date.isoformat()}"
            )
        self.maturity_date = new_date
        self.sealed_at = as_of

    # --- guards -------------------------------------------------------------

    def _refuse_if_sealed(self, as_of: datetime) -> None:
        if not self.is_matured(as_of):
            raise FundNotMaturedError(
                f"fund {self.name!r} is locked until "
                f"{self.maturity_date.isoformat()}"
            )

    def _check_amount(self, amount: Money) -> None:
        """Whatever is true of any amount, on the way in or out."""
        if not isinstance(amount, Money):
            raise InvalidAmountError("amount must be a Money")
        if amount.currency != self._balance.currency:
            raise CurrencyMismatchError(
                f"a {amount.currency.name} amount cannot move through a "
                f"{self._balance.currency.name} fund"
            )
        if amount.amount <= 0:
            raise InvalidAmountError("amount must be greater than zero")

    def _check_withdrawable(self, amount: Money) -> None:
        self._check_amount(amount)
        if self._balance < amount:
            raise InsufficientFundsError(
                f"fund {self.name!r} holds {self._balance}, not {amount}"
            )

    # --- construction -------------------------------------------------------

    def __post_init__(self):
        if not isinstance(self.fund_id, uuid.UUID):
            raise InvalidFundIDError("invalid fund id")

        if not isinstance(self.name, str) or not self.name.strip():
            raise InvalidFundNameError(
                f"name must be a non-empty string, got {self.name!r}"
            )

        if not isinstance(self.kind, FundKind):
            raise InvalidFundKindError(
                f"kind must be a FundKind, got {type(self.kind).__name__}"
            )

        if not isinstance(self._balance, Money):
            raise InvalidFundBalanceError(
                f"balance must be a Money, got {type(self._balance).__name__}"
            )

        # The subclass trap, in the fifth class to have to name it: datetime
        # passes isinstance(x, date), so a check written against the wider type
        # would accept either. The narrower type is required by name, and a
        # datetime fails for that reason rather than by a special case - which
        # matters because a pot whose deadline carried a time of day would
        # unlock at an hour the user never chose.
        if self.maturity_date is not None:
            if not isinstance(self.maturity_date, date) or isinstance(
                self.maturity_date, datetime
            ):
                raise InvalidFundMaturityError(
                    f"maturity must be a date or None, "
                    f"got {type(self.maturity_date).__name__}"
                )

        if not isinstance(self.created_at, datetime):
            raise InvalidFundCreatedAtError(
                f"created_at must be a datetime, got {type(self.created_at).__name__}"
            )

        # Both of these are moments, and neither is checked *against* the other.
        # It is tempting to require ``sealed_at <= first_funded_at`` - a pot is
        # sealed and then funded - and it would be wrong, because ``extend_to``
        # makes that ordering go backwards on purpose. The relationship between
        # the two is a question ``authorises_early_payout`` answers, not an
        # invariant construction can enforce.
        if not isinstance(self.sealed_at, datetime):
            raise InvalidFundSealedAtError(
                f"sealed_at must be a datetime, got {type(self.sealed_at).__name__}"
            )

        if self.first_funded_at is not None and not isinstance(
            self.first_funded_at, datetime
        ):
            raise InvalidFundFirstFundedAtError(
                f"first_funded_at must be a datetime or None, "
                f"got {type(self.first_funded_at).__name__}"
            )

    def __str__(self) -> str:
        if self.maturity_date is None:
            until = "open"
        else:
            until = f"until {self.maturity_date.isoformat()}"
        return (
            f"fund {self.name!r} ({self.kind.value}, {until}) "
            f"{self.balance}"
        )
