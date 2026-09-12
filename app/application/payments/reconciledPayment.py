from dataclasses import dataclass
from enum import Enum

from app.application.payments.settledPayment import SettledPayment


class ReconciliationOutcome(Enum):
    """What one row's question came back as. Four words, and only one is good news.

    **Three of these are the provider's answers and the fourth is our own
    failure.** ``SETTLED``, ``NOT_SETTLED`` and ``NO_SUCH_REFERENCE`` are
    ``ProviderAnswerStatus`` restated in this layer's vocabulary - they are what
    the provider said. ``FAILED`` is not an answer at all: it is the absence of
    one, because the call could not be made or could not be understood. Keeping it
    in the same enum rather than as an exception is the same decision
    ``SettlementOutcome`` makes about its refusals, for the same reason: an
    operator reading one line per row should not have to look somewhere else for
    the rows that went wrong, and a cron job has nowhere to raise to.

    **``SETTLED`` describes the provider, not this system**, and the collision
    with ``SettlementOutcome``'s members is worth defusing rather than renaming
    away. "The provider says the money arrived" and "we credited the wallet" are
    two facts, and they can disagree: an amount that does not match the row is
    ``SETTLED`` here and ``AMOUNT_DISAGREES`` there, which is a row the provider
    has taken money for and this system has deliberately not credited. What we
    did about it is in ``ReconciledPayment.settlement``, and ``moved_money`` reads
    *that* rather than this member, so the disagreement cannot hide.

    **``NO_SUCH_REFERENCE`` is the alarm**, and it is the one member an operator
    should be woken by. Nothing is wrong with the payment it names - it is a row
    this deployment wrote the moment a provider accepted a collection, so a
    provider that now denies knowing it means the rows and the secret key belong
    to different accounts. Nothing is settled, failed or changed when it arrives,
    for the reason the report-only policy gives.
    """

    SETTLED = "settled"
    """The provider has the money. What we did about it is in ``settlement``."""

    NOT_SETTLED = "not_settled"
    """The provider knows the reference and nothing has arrived under it."""

    NO_SUCH_REFERENCE = "no_such_reference"
    """The provider denies the reference exists. Nothing moved; this is an alarm."""

    FAILED = "failed"
    """We could not get an answer - the provider was unreachable, or made no sense.

    Ordinary rather than alarming, which is why the CLI exits 0 on it: the
    provider being briefly unreachable is a fact about the last five minutes, and
    the next run asks again. See ``ReconcilePayments``.
    """


@dataclass(frozen=True)
class ReconciledPayment:
    """One row the run asked about, and what came of asking.

    ``reference`` is the provider's name for the payment - the thing the question
    was asked about, and the only identifier a provider would recognise. The
    ledger row's own id is deliberately absent: the row belongs to a wallet and to
    an owner, and a report is a thing that gets written to a log. What the
    reconciler knows about a payment is its reference, which is also all it is
    entitled to know without naming whose money it is.

    ``settlement`` is present exactly when the provider said ``SETTLED``, and it
    is a ``SettledPayment`` - which may itself be a refusal. That is the whole
    point of carrying it rather than a boolean: the interesting row is the one
    where the provider says the money arrived and this system declined to credit
    it, and a report that flattened that to "settled" would be the one line an
    operator most needs and cannot get.
    """

    reference: str
    outcome: ReconciliationOutcome
    settlement: SettledPayment | None = None
    detail: str = ""

    @property
    def moved_money(self) -> bool:
        """Whether this row's money actually moved.

        Reads the ``SettledPayment`` rather than the outcome, because "the
        provider settled it" and "the wallet was credited" are different claims
        and only the second is about this system. False for every outcome that
        carries no settlement, which includes all three failures - so a test can
        assert "nothing moved" without re-listing the members, the same way
        ``SettledPayment.moved_money`` does.
        """
        return self.settlement is not None and self.settlement.moved_money


@dataclass(frozen=True)
class ReconciliationReport:
    """A whole run, in the three numbers and the rows an operator needs.

    ``reconciled`` is the rows that were asked about, in the order they were
    asked - oldest first, which is the order the discovery read returns them in.
    It is a tuple rather than a list because this is a value: a report of a run
    that has already happened cannot be edited into a report of a different one.

    ``too_young`` is the rows the grace window skipped without asking, and it is a
    count rather than a list on purpose. Those rows are not interesting
    individually - they are payments that were started moments ago - and the only
    thing an operator can want from them is "yes, there are some of those", which
    is what stops a quiet run from looking like a run that found nothing to do.

    ``remaining`` is the rows a bounded batch left behind, and it is the number
    worth watching over time: a run that consistently leaves a backlog is a run
    whose batch is too small, and one whose backlog grows is a provider that has
    stopped answering.
    """

    reconciled: tuple[ReconciledPayment, ...] = ()
    too_young: int = 0
    remaining: int = 0

    @property
    def moved(self) -> tuple[ReconciledPayment, ...]:
        """The rows this run actually credited or released.

        The answer to "did this run do anything", which is not the same as "did
        the provider settle anything" - see ``ReconciledPayment.moved_money``.
        """
        return tuple(one for one in self.reconciled if one.moved_money)

    @property
    def unanswered(self) -> tuple[ReconciledPayment, ...]:
        """The rows the provider could not be asked about.

        Separate from the rest because it is the one outcome that says something
        about *this* system rather than about a payment, and because it is the
        only one a re-run is likely to change.
        """
        return tuple(
            one
            for one in self.reconciled
            if one.outcome is ReconciliationOutcome.FAILED
        )
