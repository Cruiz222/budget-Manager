"""The reconciler: ask the provider about every payment that is still in flight.

The webhook is the fast path and the reconciler is the honest one. Everything in
``SettlePayment`` assumes a provider's message arrived; this assumes one did not.
A charge that succeeds while the process is down, a webhook a proxy swallowed, a
deployment whose endpoint was misconfigured for an afternoon - in all three the
payer's money has left their account, the ledger row still says PENDING, and
nothing in the system is capable of noticing. This is the thing that notices.

**It settles nothing itself.** Every row that turns out to be settled is handed
to ``SettlePayment`` as the same ``ProviderOutcome`` a webhook would have carried,
so a payment recovered here and a payment delivered by webhook leave *identical*
state - same balance, same row status, same queued receipt. There is no second
settlement path in this codebase and this class is the reason there is not: what
it adds is a second *caller*, and the whole of its own logic is deciding which
rows to ask about and what to do with an answer.

**Nothing is ever failed, and that is a decision about a hazard rather than
about optimism.** A checkout the provider reports as ``abandoned`` will very
probably never be paid, and marking that row FAILED would be honest about the
deposit and dangerous about the money: ``SettlePayment`` refuses a
``CHARGE_SUCCEEDED`` on a row that is not PENDING, so a row failed a minute
before its payment arrived would take the payer's money and never credit it, with
nothing left in the system that could. Leaving it PENDING costs a row that is
asked about every fifteen minutes forever, which is a small, visible,
recoverable cost. The README carries the sweep as an open item for exactly this
reason: it needs a fifth event and an answer to "what does it mean to fail a
charge that might still arrive", and neither exists yet.
"""

from datetime import datetime, timedelta

from app.application.payments.reconciledPayment import (
    ReconciliationOutcome,
    ReconciliationReport,
    ReconciledPayment,
)
from app.application.payments.settle_payment import SettlePayment
from app.application.unit_of_work import UnitOfWorkFactory
from app.domain.payments.exception import PaymentError
from app.domain.payments.paymentProvider import PaymentProvider
from app.domain.payments.providerAnswerStatus import ProviderAnswerStatus

#: How long a payment must have been in flight before the reconciler asks about
#: it, in minutes.
#:
#: **A courtesy, not the guard.** The guard is that only a settled answer makes
#: anything happen, so a payer who is still on the checkout page when the run
#: goes out is protected by the answer they get (``NOT_SETTLED``) rather than by
#: this window - no delay could make asking unsafe, and no delay is needed for
#: correctness. What the window buys is not spending provider calls on payments
#: that are seconds old and almost certainly fine, because a webhook is normally
#: the thing that settles a deposit and this job exists for the times it is not.
#:
#: Fifteen minutes is chosen rather than derived: long enough that a live
#: checkout is never asked about, short enough that a lost webhook is noticed
#: inside a coffee break. A deployment that disagrees changes one number.
DEFAULT_GRACE = timedelta(minutes=15)

#: How many payments one run will ask about.
#:
#: The bound ``RunDuePlans`` achieves by running one occurrence per plan, reached
#: differently because the work is different: this job's cost is one network call
#: per row, not one execution per row. A provider outage that leaves five
#: thousand rows PENDING must not make the next run five thousand calls long - it
#: clears a batch, reports what it left behind, and the next run continues from
#: there. Fifty is chosen so that an ordinary run finishes in seconds and a
#: backlog clears inside a few hours at a fifteen-minute cadence.
DEFAULT_LIMIT = 50


class ReconcilePayments:
    """Ask the provider about each in-flight payment, and settle what it reports.

    **No actor, and unlike the scheduler it needs none.** ``RunDuePlans`` mints an
    executor per plan because a plan's wallet has to be read as its owner, and the
    owner is a fact the plan carries. Here the wallet read belongs to
    ``SettlePayment`` - through ``WalletRepository.owner_of`` and then
    ``get_owned`` - and this class touches no wallet at all: it reads a reference
    off a ledger row and hands an outcome to the settler. So there is no closure
    to build, no owner to thread, and nothing in this file that could read a
    wallet on behalf of nobody even if somebody tried to make it.

    **A third-party failure is per-row; our own data being wrong is not.** An ask
    that fails - the provider unreachable, a response that makes no sense - is
    recorded against that row and the run continues, because a provider being
    briefly unavailable about one reference says nothing about the next one, and
    a run that stopped at the first hiccup would leave every row behind it
    unreconciled. A ``settle`` that *raises* is deliberately not caught, and the
    asymmetry is the point: the only way it can raise is a ledger row naming a
    wallet that does not exist, which is an integrity problem rather than a
    transient one, and reporting it as one row's bad luck would be the silence
    this whole feature exists to prevent. It also cannot happen: the row was
    discovered through a provider reference, a reference is written only by
    ``InitiateDeposit`` after a collection was opened, and ``transactions.wallet_id``
    is a foreign key - so the wallet is there by construction, and a raise here
    means the database itself is broken. Failing loudly on that is the ``RunDuePlans``
    answer, and it costs nothing because the rows already settled are committed:
    each row is its own unit of work, so a run that dies on row nine leaves the
    first eight credited for good.
    """

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        settler: SettlePayment,
        provider: PaymentProvider,
        *,
        grace: timedelta = DEFAULT_GRACE,
        limit: int = DEFAULT_LIMIT,
    ):
        self._unit_of_work_factory = unit_of_work_factory
        # Injected rather than built here, and it is the same object the webhook
        # route uses - built once, at the composition root, from the same factory
        # this class is. That is what makes "recovered and delivered payments are
        # indistinguishable" a property of the wiring rather than a claim in a
        # docstring: there is one settler, so there is one settlement path.
        self._settler = settler
        self._provider = provider
        self._grace = grace
        self._limit = limit

    def execute(self, as_of: datetime) -> ReconciliationReport:
        """Ask about every eligible row, and report what each answer was.

        ``as_of`` rather than a clock read inside, for the rule the rest of this
        codebase follows: a moment is read at the edge and handed down, so a run
        can be replayed against a fixed instant and a test can put a row inside or
        outside the grace window without waiting fifteen minutes.

        An empty report means there was nothing in flight - the ordinary state of
        a healthy installation, since a webhook normally settles a deposit within
        seconds. That is worth saying because it is the opposite of the
        scheduler's quiet tick, where "nothing due" happens only when every plan
        has already run: here, quiet is the *good* case and a run that always
        finds work is a run reporting a broken webhook endpoint.
        """
        rows = self._awaiting()
        candidates, too_young = self._candidates(rows, as_of)

        reconciled = [self._ask(row) for row in candidates]

        return ReconciliationReport(
            reconciled=tuple(reconciled),
            too_young=too_young,
            remaining=len(rows) - too_young - len(candidates),
        )

    # --- discovery ----------------------------------------------------------

    def _awaiting(self) -> list:
        """The in-flight rows, read in one unit that is closed before any call.

        **The rollback is load-bearing here in a way it is not in ``_due_plans``.**
        Both read inside a unit and roll it back in a ``finally``; what differs is
        what happens next. The scheduler follows its read with local work, while
        this method is followed by up to ``limit`` network calls - and a unit left
        open holds a SQLite read transaction for the whole of them, so the
        database would be locked against its own writers for as long as a third
        party felt like taking to answer, multiplied by however many rows the
        batch contains. Closing the read before the first call is what makes that
        impossible rather than merely unlikely.

        The rows are plain aggregates by the time this returns, holding no
        connection of their own - the same property ``_due_plans`` relies on, and
        the reason each row can then settle inside a *different* unit.
        """
        uow = self._unit_of_work_factory.start()
        try:
            return uow.transactions.list_awaiting_provider()
        finally:
            uow.rollback()

    def _candidates(self, rows: list, as_of: datetime) -> tuple[list, int]:
        """The rows to ask about now, and how many were too young to ask about.

        A query, then a filter in Python - the ``_due_plans`` arrangement, and for
        the same reason: neither the age of a payment nor the size of a batch is a
        fact about storage, and materialising either as a column would create a
        second source of truth for a policy that this class already owns.

        The window is inclusive at its edge - a row exactly ``grace`` old is asked
        about - which mirrors the way ``Confirmation.is_expired`` treats its own
        boundary: the instant a window closes is the instant it stops being open,
        and the two must agree with each other rather than each being
        individually defensible.

        Oldest first, which the read already guarantees, and the batch takes from
        the front. That is what stops a row being starved: a backlog that is always
        longer than the batch still drains from its oldest end, so the payment
        that has been stuck longest is always the first one asked about.
        """
        cutoff = as_of - self._grace
        eligible = [row for row in rows if row.created_at <= cutoff]
        return eligible[: self._limit], len(rows) - len(eligible)

    # --- one row ------------------------------------------------------------

    def _ask(self, row) -> ReconciledPayment:
        """Put one row's question to the provider and record the answer.

        Four ways out, and three of them move nothing. A ``SETTLED`` answer is the
        only one that reaches the settler, and even then the settler has refusals
        of its own - which is why the ``SettledPayment`` travels into the report
        rather than being flattened into a boolean here. An amount that disagrees
        is the case worth knowing about: the provider has taken the payer's money,
        the row stays PENDING, and the report says so on every run until somebody
        looks.
        """
        reference = row.provider_reference

        try:
            answer = self._provider.outcome_for(reference)
        except PaymentError as failure:
            # The whole payments root rather than ``PaymentProviderError``: an
            # ask can fail by not completing (unreachable, refused) or by not
            # being understood (a response with no transaction in it, a status
            # this adapter does not know), and both are the same fact to this
            # loop - no answer was obtained. Catching the root is what keeps that
            # one branch instead of a list that a future error type would have to
            # be added to.
            return ReconciledPayment(
                reference=reference,
                outcome=ReconciliationOutcome.FAILED,
                detail=f"could not ask: {failure}",
            )

        if answer.status is ProviderAnswerStatus.SETTLED:
            settlement = self._settler.settle(answer.outcome)
            return ReconciledPayment(
                reference=reference,
                outcome=ReconciliationOutcome.SETTLED,
                settlement=settlement,
                detail=settlement.detail,
            )

        if answer.status is ProviderAnswerStatus.NOT_SETTLED:
            return ReconciledPayment(
                reference=reference,
                outcome=ReconciliationOutcome.NOT_SETTLED,
                detail="the provider reports that nothing has arrived",
            )

        return ReconciledPayment(
            reference=reference,
            outcome=ReconciliationOutcome.NO_SUCH_REFERENCE,
            detail=(
                "the provider has no transaction under this reference - check "
                "that this deployment's rows and its secret key belong to the "
                "same account"
            ),
        )
