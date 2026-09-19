from abc import ABC, abstractmethod
from datetime import datetime

from app.domain.money.currency import Currency
from app.domain.money.money import Money
from app.domain.money.transaction import Transaction

class TransactionRepository(ABC):

    @abstractmethod
    def save(self, transaction: Transaction) -> Transaction :
        pass
    @abstractmethod
    def get_by_id(self, transaction_id) -> Transaction:
        pass

    @abstractmethod
    def get_by_internal_reference(self, internal_reference: str) -> Transaction | None:
        """Return the transaction recorded under this idempotency key, or None.

        Not-found is an *expected* outcome here (it drives duplicate checks),
        so it returns None instead of raising like get_by_id.
        """
        pass

    @abstractmethod
    def get_by_wallet_id(self, wallet_id) -> list[Transaction]:
        """Return every transaction on a wallet's ledger, oldest first.

        A wallet with no transactions returns an empty list. Ordering is part
        of the contract so a caller can render the ledger as-is.
        """
        pass

    @abstractmethod
    def get_by_provider_reference(self, provider_reference: str) -> Transaction | None:
        """Return the transaction recorded under a provider key, or None.

        A provider reference is the pay-out processor's own idempotency key
        (e.g. a Paystack reference), so at most one transaction carries it.
        Not-found returns None, mirroring get_by_internal_reference.
        """
        pass

    @abstractmethod
    def list_awaiting_provider(self) -> list[Transaction]:
        """Every PENDING row that a payment provider was actually told about.

        The reconciler's discovery read, and the fourth unscoped read in this
        codebase - so it gets the same defence ``SavingsPlanRepository``'s
        ``list_by_status`` gets, which is worth restating because it is the whole
        argument: **this is discovery, not access.** It crosses owners because
        the question it answers is an installation-wide one ("which payments are
        still in flight?"), and the rows it returns each name their own wallet.
        The caller reads a reference off them and asks a third party about it;
        the wallet read that eventually follows is scoped to the owner the row
        named, through ``WalletRepository.owner_of`` and then ``get_owned``, and
        is therefore not this method's business or its doing. Nothing
        user-facing can reach it, which is ``PlanService``'s rule - the service
        fronting the plans deliberately does not expose ``list_by_status`` -
        applied to the ledger side.

        **Both halves of the filter are load-bearing.** PENDING alone would hand
        back every withdrawal and every plan-run payout typed at the CLI, none of
        which any provider has ever heard of; a provider asked about one of those
        references would answer "no such reference", and the reconciler would
        raise an alarm about a payment that was never anyone's to make. A
        provider reference alone would hand back settled rows and re-ask
        questions already answered. Only the intersection is a question worth
        asking.

        **Takes no argument, deliberately.** There is no status parameter to
        point at something else and no wallet to point at somebody else - the
        narrower the door, the fewer ways a caller can get it wrong.

        Oldest first, the contract ``get_by_wallet_id`` states and for the
        stronger version of the same reason: the reconciler works through a
        bounded batch and leaves the rest for the next run, so the order decides
        which rows wait. Oldest-first asks about the longest-waiting payments
        first, and it means a row cannot be starved by newer ones arriving behind
        it.
        """
        pass

    @abstractmethod
    def outflow_total_between(
        self,
        wallet_id,
        start: datetime,
        end: datetime,
        currency: Currency,
    ) -> Money:
        """How much value has left this wallet in ``[start, end)``, in ``currency``.

        The read behind the daily outflow cap, and one of the two methods on this
        port that *aggregate* rather than returning rows - ``pending_credit_total``
        below is the other, and it is this method's mirror for the incoming
        direction. It returns a ``Money``
        because that is what the rule compares - ``check_outflow`` adds the
        movement being attempted to this total and refuses if the sum is over the
        tier's ceiling - and a count of rows or a bare ``Decimal`` would leave the
        caller to build the ``Money`` itself, which is a currency decision made
        twice.

        **The filter is one rule with one spelling: a row counts when its money is
        not in the wallet.** Concretely, ``WITHDRAWAL`` and ``PAYOUT`` rows -
        the two types that move value across the system's edge, as against
        ``DEPOSIT`` which brings it in and ``LOCK_FUNDS``/``UNLOCK_FUNDS`` which
        move it between the wallet's own two balances - and of those, the
        ``PENDING`` and ``SUCCESSFUL`` ones. The status half is the half worth
        reading slowly:

        - ``PENDING`` counts, because the debit has already happened: a requested
          payout has been taken out of the available balance and is being held.
          A cap that ignored held money would let a burst of in-flight payouts
          each pass on its own.
        - ``SUCCESSFUL`` counts, because the money is gone.
        - ``FAILED`` does not, because the hold was given back. The money never
          left.
        - ``REVERSED`` does not, because the bank returned it. The money left and
          came back, and what a *value* ceiling bounds is value that stayed out.

        That last pair is where this method takes a position rather than
        reporting one, and the position is worth naming because the safe-looking
        choice is the other one: counting reversals would refuse less, and there
        is no rate limit in this system at all, so a day in which everything was
        reversed can be spent again. The reading is that the daily cap bounds the
        value that leaves an account *for good*, and that bounding how *often*
        money may move is a different control - the one the README still owes
        under rate limiting. If that control is ever built it will need a read of
        its own, because it will have to count the rows this one deliberately
        does not.

        **The currency is a parameter, and it is not a filter the caller can get
        interestingly wrong.** A wallet holds one currency and its ledger is
        written in that currency by construction, so the value a caller passes is
        the one it has already loaded off the wallet - the same value, read from
        the same row, that every movement against that wallet is built with. The
        column is compared against it all the same, because the alternative is a
        sum that would silently mix two currencies if a row were ever written
        wrongly, and ``Money`` cannot refuse that on this side - it can only
        refuse arithmetic between currencies it can see, and a SQL sum hands back
        one number with no currency attached.

        **``start`` is inclusive and ``end`` is exclusive**, so a movement stamped
        exactly at midnight belongs to the day beginning rather than to the one
        ending, and a day's two bounds partition every moment exactly once. See
        ``limit_day_bounds`` for where the pair comes from and for the timezone
        this deliberately does not have.

        A wallet with nothing to count gets ``Money("0.00", currency)`` rather
        than ``None``: no outflow is a total of zero, not an absent answer, and
        the one caller adds a movement to it on every path.
        """
        pass

    @abstractmethod
    def pending_credit_total(self, wallet_id, currency: Currency) -> Money:
        """How much is in flight *into* this wallet, in ``currency``.

        The mirror of ``outflow_total_between``, and it exists for the same
        reason that method counts ``PENDING`` rows - stated there as *"a cap that
        ignored held money would let a burst of in-flight payouts each pass on
        its own."* The balance cap had no equivalent for credits, so a burst of
        in-flight **deposits** each passed on its own: every one of them read the
        same untouched balance, every one fit under the ceiling, and all of them
        settled. That is the same bypass, on the other side of the ledger, and it
        is the side where the money has already left the payer.

        **The filter is one rule: a row counts when its money is coming and has
        neither arrived nor been given up on.** Concretely, ``DEPOSIT`` rows -
        the only type that brings value in - whose status is ``PENDING``:

        - ``PENDING`` counts, because a collection has been opened and the payer
          may already be looking at a payment page. This is the whole point of
          the read.
        - ``SUCCESSFUL`` does not, because the money has arrived and is in the
          wallet's balances, which the caller adds separately. Counting it here
          would double it.
        - ``FAILED`` does not, because the attempt is over and no money is
          coming.
        - ``REVERSED`` does not, for ``outflow_total_between``'s reason read the
          other way: what is in flight is money that has not landed yet, and a
          reversal is a finished fact about money that did.

        ``LOCK_FUNDS`` and ``UNLOCK_FUNDS`` are excluded along with the rest
        because they move value between the wallet's own two balances - the
        balance cap counts what the wallet *holds*, and a pot is already held.

        **No time window, unlike ``outflow_total_between``.** An outflow is
        bounded by a day because its ceiling is a daily allowance; a pending
        collection has no day - it is in flight until it settles or is
        abandoned, whenever that is. Bounding it by a day would drop a payment
        that has been waiting across midnight, which is exactly the row that
        most needs counting.

        The currency is a parameter for the reason the other method gives, and a
        wallet with nothing in flight gets ``Money("0.00", currency)`` rather
        than ``None``: nothing pending is a total of zero, and the caller adds it
        to a movement on every path.
        """
        pass
