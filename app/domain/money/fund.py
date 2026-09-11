from dataclasses import dataclass, field
from datetime import date, datetime
import uuid

from .exception import (
    CurrencyMismatchError,
    FundNotMaturedError,
    InvalidAmountError,
    InvalidFundBalanceError,
    InvalidFundCreatedAtError,
    InvalidFundIDError,
    InvalidFundKindError,
    InvalidFundMaturityError,
    InvalidFundNameError,
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

        may this pot fund a scheduled payout before its date?

    A ``BUSINESS`` pot may - that is what it is for. A ``PERSONAL`` pot may not,
    by any route.
    """

    fund_id: uuid.UUID
    name: str
    kind: FundKind
    _balance: Money
    maturity_date: date | None = None
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

    def deposit(self, amount: Money) -> None:
        """Add money to the pot, at any time, before or after its date.

        No maturity check and no wallet-status check: both of those guard money
        *leaving*, and this is money arriving. A closed wallet is refused by the
        wallet, which is the only place that knows it is closed.
        """
        self._check_amount(amount)
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

    def pay(self, amount: Money, as_of: datetime) -> None:
        """Spend money out of the pot, to an external account.

        A ``BUSINESS`` pot may do this before its date - a scheduled obligation
        does not wait for the float to mature, and refusing would make the kind
        pointless. A ``PERSONAL`` pot may not, because for it a payment is just
        spending, and spending is what the date is there to stop.

        Note this rule is about the *pot*, not the caller. Whether a given price
        counts as "scheduled" is the plan's business, not the pot's; by the time
        a caller reaches here it has already decided this pot is the right one to
        spend from, and the pot only answers for itself.
        """
        if self.kind is FundKind.PERSONAL:
            self._refuse_if_sealed(as_of)
        self._check_withdrawable(amount)
        self._balance = self._balance - amount

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

    def __str__(self) -> str:
        if self.maturity_date is None:
            until = "open"
        else:
            until = f"until {self.maturity_date.isoformat()}"
        return (
            f"fund {self.name!r} ({self.kind.value}, {until}) "
            f"{self.balance}"
        )
