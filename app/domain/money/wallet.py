from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import uuid

from .currency import Currency
from .exception import (
    CurrencyMismatchError,
    DuplicateFundNameError,
    FundNotFoundError,
    InsufficientFundsError,
    InvalidAmountError,
    InvalidWalletAvailableBalanceError,
    InvalidWalletCurrencyError,
    InvalidWalletFundsError,
    InvalidWalletIDError,
    InvalidWalletStatusError,
    InvalidWalletUserIDError,
    NegativeAmountWithdrawalError,
    WalletAlreadyActiveError,
    WalletAlreadyClosedError,
    WalletAlreadyFrozenError,
    WalletClosedError,
    WalletFrozenError,
    ZeroAmountWithdrawalError,
)
from .fund import Fund
from .money import Money
from .walletStatus import WalletStatus


@dataclass
class Wallet:
    """A wallet's money, split into two balances: available and locked.

    **The locked balance is derived, not stored.** It is the sum of the wallet's
    ``Fund``s - the named pots a user locks money into - and there is deliberately
    no second field holding it. A stored copy beside the funds would be two
    records of one fact with a "they must agree" rule enforced by hope, and every
    "the pot says 5,000 but the wallet says 4,900" bug lives in exactly that gap.
    Summing is the only way to know the locked balance and the only way to be
    right about it.

    The consequence to hold on to: **locking is not one operation any more**. A
    pot must be named, because "the locked balance" can no longer say where money
    goes. ``lock_funds`` and ``release_funds`` - which moved an amount between two
    anonymous numbers - are gone, replaced by the fund-scoped methods below.

    ``_funds`` is a tuple, not a list, for the reason ``SavingsPlan.instructions``
    is (`savingsPlan.py`): a caller holding a list could append a fund that had
    skipped every check in ``open_fund``. Changing the collection only happens
    through the methods, and they run the checks.
    """

    wallet_id: uuid.UUID
    user_id: uuid.UUID
    currency: Currency
    status: WalletStatus
    _available_balance: Money
    _funds: tuple[Fund, ...] = ()

    @property
    def available_balance(self) -> Money:
        return self._available_balance

    @property
    def funds(self) -> tuple[Fund, ...]:
        """The wallet's pots, in the order they were opened.

        That order is load-bearing rather than incidental: it is the order a
        payout draws on them when no pot is named (``payout_from_locked``), so a
        read of this tuple is a read of the draw order.
        """
        return self._funds

    @property
    def locked_balance(self) -> Money:
        """Everything locked, summed across every pot, matured or not."""
        return self._total(self._funds)

    def matured_locked_balance(self, as_of: datetime) -> Money:
        """The locked money that may actually leave as of ``as_of``.

        The difference between this and ``locked_balance`` is the whole reason a
        scheduled run asks this one: a plan drawing on locked money may only
        spend the pots that have come due, and judging it against the full locked
        total would wave through a run that then fails partway - the
        half-executed run the pre-flight exists to prevent.
        """
        return self._total(fund for fund in self._funds if fund.is_matured(as_of))

    # --- pots ---------------------------------------------------------------

    def open_fund(
        self,
        name: str,
        kind,
        maturity_date=None,
        as_of: datetime | None = None,
    ) -> Fund:
        """Open a new pot, empty, with its own name and due date.

        Refused for a name the wallet already has. The name is the handle a
        human types on the command line - ``fund release Vacation 5000`` - so two
        pots called "Vacation" would make the command ambiguous, and the failure
        would not be "no such pot" but "some pot, chosen by the wrong rule".
        """
        if self.status is WalletStatus.CLOSED:
            raise WalletClosedError("this wallet is closed")

        if any(fund.name == name for fund in self._funds):
            raise DuplicateFundNameError(
                f"this wallet already has a fund named {name!r}"
            )

        fund = Fund(
            fund_id=uuid.uuid4(),
            name=name,
            kind=kind,
            _balance=Money(Decimal("0"), self.currency),
            maturity_date=maturity_date,
            created_at=as_of or datetime.now(),
        )
        self._funds = self._funds + (fund,)
        return fund

    def fund_by_name(self, name: str) -> Fund:
        """Find a pot by its name, which is how a human refers to it."""
        for fund in self._funds:
            if fund.name == name:
                return fund
        raise FundNotFoundError(f"no fund named {name!r} on this wallet")

    def fund_by_id(self, fund_id: uuid.UUID) -> Fund:
        """Find a pot by identity, which is how persisted rows refer to it."""
        for fund in self._funds:
            if fund.fund_id == fund_id:
                return fund
        raise FundNotFoundError(f"no fund {fund_id} on this wallet")

    def deposit_into_fund(self, fund_id: uuid.UUID, amount: Money) -> None:
        """Bring money in from outside straight into a pot.

        Note this does not pass through the available balance, so it is a single
        move rather than a deposit followed by a lock. That is the shape the
        request asked for - "it should still be able to accept additions or
        further deposits" - and it matters for the ledger, which records one
        DEPOSIT rather than a DEPOSIT and a LOCK_FUNDS.

        A frozen wallet still accepts this: freezing stops value *leaving*, and
        this is value arriving.
        """
        if self.status is WalletStatus.CLOSED:
            raise WalletClosedError("this wallet is closed")
        self.fund_by_id(fund_id).deposit(amount)

    def lock_into_fund(self, fund_id: uuid.UUID, amount: Money) -> None:
        """Move money from the available balance into a pot.

        The pot-scoped successor to the old ``lock_funds``. The pot is looked up
        before the balance is checked, so an unknown pot is reported as an unknown
        pot rather than as insufficient funds - the caller got the name wrong, and
        that is the more useful thing to say.
        """
        if self.status is WalletStatus.CLOSED:
            raise WalletClosedError("this wallet is closed")
        if amount.currency != self.currency:
            raise CurrencyMismatchError("currency must be the same")
        if amount.amount <= 0:
            raise InvalidAmountError("amount must be greater than zero")

        fund = self.fund_by_id(fund_id)
        if self._available_balance < amount:
            raise InsufficientFundsError("insufficient available balance")

        fund.deposit(amount)
        self._available_balance = self._available_balance - amount

    def release_from_fund(
        self, fund_id: uuid.UUID, amount: Money, as_of: datetime
    ) -> None:
        """Move money out of a pot, back into the available balance.

        The refusal this method exists for is inside ``Fund.release``: money
        leaves only once the pot has come due. A frozen wallet permits this, as
        it permitted the old ``release_funds`` - the money does not leave the
        wallet, it only changes which balance holds it.
        """
        if self.status is WalletStatus.CLOSED:
            raise WalletClosedError("this wallet is closed")

        fund = self.fund_by_id(fund_id)
        fund.release(amount, as_of)
        self._available_balance = self._available_balance + amount

    def extend_fund(self, fund_id: uuid.UUID, new_date, as_of: datetime) -> None:
        """Push a pot's due date later, re-sealing it if it had already opened."""
        if self.status is WalletStatus.CLOSED:
            raise WalletClosedError("this wallet is closed")
        self.fund_by_id(fund_id).extend_to(new_date, as_of)

    # --- the balances -------------------------------------------------------

    def apply_deposit(self, amount: Money):
        if self.status == WalletStatus.CLOSED:
            raise WalletClosedError

        if amount.currency != self.currency:
            raise CurrencyMismatchError

        if amount.amount <= 0:
            raise InvalidAmountError

        self._available_balance = self._available_balance + amount

    def withdraw(self, amount: Money):
        if self.status == WalletStatus.CLOSED:
            raise WalletClosedError("this wallet is closed")

        if self.status == WalletStatus.FROZEN:
            raise WalletFrozenError("this wallet is frozen")

        if amount.amount == 0:
            raise ZeroAmountWithdrawalError("amount must be greater than zero")

        if amount.amount < 0:
            raise NegativeAmountWithdrawalError("amount can not be negative")

        if self.currency != amount.currency:
            raise CurrencyMismatchError("currency must be thes ame")

        if self._available_balance < amount:
            raise InsufficientFundsError

        self._available_balance = self._available_balance - amount

    def payout_from_locked(self, amount: Money, as_of: datetime) -> None:
        """Send money out of the wallet, spending the locked pots.

        This is the counterpart of withdraw() for reserved funds. The
        distinction that matters: releasing and locking only *move* money between
        balances, so the wallet still holds it all. A payout reduces what the
        wallet holds - value actually leaves - which is why it refuses a frozen
        wallet exactly as withdraw() does. Freezing stops value from leaving; it
        does not stop internal reshuffling.

        Which pot pays is ``_draw_from_matured``'s answer, not this method's -
        see there for the rule and for why it is temporary.
        """
        if self.status == WalletStatus.CLOSED:
            raise WalletClosedError("this wallet is closed")

        if self.status == WalletStatus.FROZEN:
            raise WalletFrozenError("this wallet is frozen")

        self._draw_from_matured(amount, as_of, Fund.pay)

    def release_from_locked(self, amount: Money, as_of: datetime) -> None:
        """Move money out of the locked pots, back into the available balance.

        The pooled counterpart of ``release_from_fund``, and the one a *plan's*
        RELEASE instruction reaches: a plan in this phase names no pot, exactly as
        its payouts do not, so the same placeholder draw applies. Naming a pot on
        a plan is the next phase's work.

        No frozen check, deliberately. The wallet still holds every unit
        afterwards - only which balance holds it has changed - so this stays
        allowed on a frozen wallet, as the whole-pool ``release_funds`` it
        replaces did. A frozen wallet can be released from; it cannot be paid out
        of.

        **The credit is what makes this a release rather than a payout**, and it
        is the half that is easy to leave out: ``_draw_from_matured`` empties the
        pots, and it is shared with ``payout_from_locked``, where nothing is
        credited because the money is leaving the wallet. Here the money is not
        leaving - it moves to the other balance - so this method adds it back.
        Draw first, then credit, and not the other way round: the draw is the
        half that can raise, and it validates the matured total before touching
        anything, so a shortage leaves the wallet exactly as it was.
        """
        if self.status == WalletStatus.CLOSED:
            raise WalletClosedError("this wallet is closed")

        self._draw_from_matured(amount, as_of, Fund.release)
        self._available_balance = self._available_balance + amount

    def _draw_from_matured(self, amount: Money, as_of: datetime, spend) -> None:
        """Take ``amount`` out of the matured pots, oldest first, until covered.

        **One rule, shared by the two ways locked money leaves without a pot being
        named.** A payout out of the wallet and a release back into the available
        balance differ only in the verb - ``Fund.pay`` or ``Fund.release`` - so
        that verb arrives as an argument and everything else is written once.
        Two copies of this loop could take different amounts, or draw in
        different orders, and the difference would be invisible until someone
        compared the two by hand.

        **Maturity comes first.** A pot that has not come due is not a candidate
        at all, so the total checked against the request is the *matured* total -
        never the whole locked balance. That is the rule the product is built on:
        money you reserved does not leave early because something else wanted it.

        **The draw order and the missing ledger attribution are placeholders**,
        and both are deliberate. "Which pot did this come from?" has no honest
        single answer while several pots may fund one payment, so this spends the
        oldest opened first and records no pot on the ledger row at all, rather
        than recording a split nobody asked for. The real answer is for the
        payment to name its pot, which the next phase brings; until then this is
        the least-wrong rule available and it is written down as such.
        """
        if amount.currency != self.currency:
            raise CurrencyMismatchError("currency must be the same")

        if amount.amount <= 0:
            raise InvalidAmountError("amount must be greater than zero")

        spendable = [fund for fund in self._funds if fund.is_matured(as_of)]
        if self._total(spendable) < amount:
            raise InsufficientFundsError("insufficient matured locked balance")

        remaining = amount
        for fund in spendable:
            if remaining.amount <= 0:
                break
            # An empty pot is a candidate that can cover nothing. Skipping it is
            # not just an optimisation: paying zero is not a payment, and both
            # ``Fund.pay`` and ``Fund.release`` refuse it.
            if fund.balance.amount <= 0:
                continue
            take = fund.balance if fund.balance < remaining else remaining
            spend(fund, take, as_of)
            remaining = remaining - take

    # --- status -------------------------------------------------------------

    def freeze(self):
        if self.status == WalletStatus.FROZEN:
            raise WalletAlreadyFrozenError

        if self.status == WalletStatus.CLOSED:
            raise WalletAlreadyClosedError

        self.status = WalletStatus.FROZEN

    def unfreeze(self):
        if self.status == WalletStatus.ACTIVE:
            raise WalletAlreadyActiveError

        if self.status == WalletStatus.CLOSED:
            raise WalletClosedError

        self.status = WalletStatus.ACTIVE

    # --- helpers ------------------------------------------------------------

    def _total(self, funds) -> Money:
        """Sum a sequence of pots, starting from a zero in the wallet's currency.

        The zero matters: ``Money(0, NGN)`` is what makes an empty wallet report
        "0.00 NGN" rather than raising on an empty ``sum``, and what fixes the
        currency of the answer even when there is nothing to add up.
        """
        total = Money(Decimal("0"), self.currency)
        for fund in funds:
            total = total + fund.balance
        return total

    def __post_init__(self):
        if not isinstance(self.wallet_id, uuid.UUID):
            raise InvalidWalletIDError("invalid wallet id")

        if not isinstance(self.user_id, uuid.UUID):
            raise InvalidWalletUserIDError("invalid user id")

        if not isinstance(self.currency, Currency):
            raise InvalidWalletCurrencyError("invalid wallet currency")

        if not isinstance(self.status, WalletStatus):
            raise InvalidWalletStatusError("invalid wallet status")

        if not isinstance(self._available_balance, Money):
            raise InvalidWalletAvailableBalanceError("invalid wallet balance")

        if not isinstance(self._funds, tuple):
            raise InvalidWalletFundsError(
                f"funds must be a tuple, got {type(self._funds).__name__}"
            )

        if self._available_balance.currency != self.currency:
            raise CurrencyMismatchError(
                "available balance currency must match wallet currency"
            )

        for fund in self._funds:
            if not isinstance(fund, Fund):
                raise InvalidWalletFundsError(
                    f"every fund must be a Fund, got {type(fund).__name__}"
                )
            # A pot is in its wallet's currency by construction, and there is no
            # currency column on the funds table to disagree with. Checking here
            # is what makes that true rather than merely intended.
            if fund.balance.currency != self.currency:
                raise CurrencyMismatchError(
                    "fund currency must match wallet currency"
                )

        names = [fund.name for fund in self._funds]
        if len(names) != len(set(names)):
            raise DuplicateFundNameError(
                "two funds on one wallet cannot share a name"
            )
