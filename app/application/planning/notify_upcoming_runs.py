"""Warn about plans that are about to fire.

The other half of a tick. ``RunDuePlans`` spends the money when a moment
arrives; this raises the courtesy warning shortly before it does. The two never
touch, and that is the design rather than an accident - see ``NotifyUpcomingRuns``
for why.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.application.unit_of_work import UnitOfWorkFactory
from app.domain.money.money import Money
from app.domain.notifications.outboundMessage import OutboundMessage
from app.domain.planning.planNotice import PlanNotice
from app.domain.planning.planStatus import PlanStatus
from app.domain.planning.savingsPlan import SavingsPlan

#: How long before a run the warning is raised.
#:
#: This is also the constraint the driver has to respect: a tick that runs less
#: often than the window is wide can step straight over it, and the warning then
#: silently never happens. A cron interval of five minutes against a thirty
#: minute window gives several chances; an hourly one would give none.
NOTICE_WINDOW = timedelta(minutes=30)


@dataclass(frozen=True)
class UpcomingNotice:
    """A warning that was issued, described for whoever has to report it.

    The persisted ``PlanNotice`` is deliberately thinner than this: it holds only
    what makes the warning once-only and auditable. But the presenter has to say
    *which* plan and *how much*, and the plan has already been loaded to decide
    the warning was due at all - so the use case hands back this view rather than
    throwing that work away and having the CLI load the plan a second time.

    A view, not a record: nothing here is stored, and nothing reads it to make a
    decision. It exists so the warning can be read by a human.
    """

    plan_id: uuid.UUID
    plan_name: str
    amount: Money
    due_at: datetime
    raised_at: datetime


class NotifyUpcomingRuns:
    """Raise a warning for every plan whose next run is inside the window.

    Three things have to be true of this, and only the middle one is about
    arithmetic:

    1. **It must not gate anything.** The warning reports; it never delays or
       blocks. If the process was down and the window was missed, no warning is
       raised - and the payout still runs at its set time. What protects that is
       structural rather than a rule written down somewhere: this class never
       loads a wallet. It has no balance in hand and no reason to look at one, so
       there is nothing here for a payment to be conditional on. A "should we
       warn?" that consulted affordability would be the thing this is meant not
       to be.

    2. **It must not repeat.** See ``PlanNotice`` - the store keys on
       ``(plan_id, due_at)`` and the write is a claim.

    3. **The payout must still happen.** Which is mostly a consequence of (1):
       the run use case does not consult notices, so a missed, late or failed
       warning changes nothing about whether money moves.

    Note there is no ``PlanRun`` and no status change here. A warning is not an
    outcome - nothing about the plan's life changed because someone was told -
    so this method writes one kind of row and touches nothing else.

    **What it does do now is queue.** Raising the warning used to mean only
    writing a notice and returning it for the CLI to print; it now also composes
    the words and enqueues them, so the warning has somewhere to go besides a log
    file. The two writes are the point of the method and must land together - see
    ``_claim_and_enqueue``. Nothing is *sent* here: sending is a network call,
    and a network call belongs nowhere near the transaction that records the
    decision to warn.
    """

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        recipient: str | None = None,
        window: timedelta = NOTICE_WINDOW,
    ):
        self._unit_of_work_factory = unit_of_work_factory
        # ``None`` means this installation has no notification address, which is
        # the ordinary state of a fresh install rather than an error. The warning
        # still happens and is still recorded; it simply has nowhere to be sent,
        # so nothing is queued. See ``_claim_and_enqueue`` for why the guarantee
        # survives that case intact.
        self._recipient = recipient
        self._window = window

    def execute(self, as_of: datetime) -> list[UpcomingNotice]:
        """Warn about every plan coming due, and return only the new warnings.

        Returns the notices raised *by this call*. An empty list is the ordinary
        case by a wide margin: most ticks find nothing inside the window, and the
        ticks that do find something return a warning exactly once. A caller can
        therefore print this list verbatim and never repeat itself.

        ``as_of`` is taken rather than read from the clock for the same reason it
        is everywhere else - testability, and so one tick has one idea of what
        "now" is. It serves a second purpose here: it is also the moment recorded
        as ``raised_at``, so replaying a tick produces the same row rather than a
        slightly different one.
        """
        raised = []
        for plan in self._upcoming(as_of):
            notice = UpcomingNotice(
                plan_id=plan.plan_id,
                plan_name=plan.name,
                amount=plan.total_to_move,
                due_at=plan.next_due_at,
                raised_at=as_of,
            )
            if self._claim_and_enqueue(notice):
                raised.append(notice)
        return raised

    def _upcoming(self, as_of: datetime) -> list[SavingsPlan]:
        """Every ACTIVE plan whose next run is inside the window.

        A query, then a filter in Python - the same shape as
        ``RunDuePlans._due_plan_ids``, and for the same reason: a plan's next due
        moment is derived from its anchor and its run count, so there is no
        column to ask SQLite about, and materialising one would create a second
        source of truth for when a plan fires.

        The plans themselves come back, not just their ids, because the warning
        has to name the plan and say what it costs. A ``SavingsPlan`` is a plain
        in-memory object, so it stays usable after the read-only unit is rolled
        back - the rollback releases the connection, not the value.
        """
        uow = self._unit_of_work_factory.start()
        try:
            candidates = uow.plans.list_by_status(PlanStatus.ACTIVE)
        finally:
            # A read-only unit. Nothing was written, so rollback is free - and
            # doing it in a finally clause means the connection is released even
            # if the filter below were to raise.
            uow.rollback()

        return [plan for plan in candidates if self._warns_for(plan, as_of)]

    def _warns_for(self, plan: SavingsPlan, as_of: datetime) -> bool:
        """Whether this plan's next run falls inside the warning window.

        The window is not a new rule; it is the existing one asked about a
        different moment. A plan is *coming up* when it will be due shortly but
        is not yet due - which is exactly ``is_due_at`` at a moment shifted
        forward, minus ``is_due_at`` now.

        Writing the lower bound as a strict comparison against zero would be the
        same arithmetic and the same trap: at ``as_of == due_at`` the plan is
        due, and the tick should *pay*, not warn. The subtraction form hides that
        behind a ``<=``; asking ``is_due_at`` twice states it. A plan that is
        already overdue fails the second half too, so it warns nothing either -
        correctly, because it is not coming up, it is owed, and the run will
        settle it.
        """
        return plan.is_due_at(as_of + self._window) and not plan.is_due_at(as_of)

    def _claim_and_enqueue(self, notice: UpcomingNotice) -> bool:
        """Record the warning *and* queue its message, in one unit of work.

        This is the one place in the codebase where two writes must share a
        transaction, and the reason is not tidiness. A claimed notice is never
        raised again - that is the whole of ``PlanNotice`` - so if the claim
        could commit while the message was never written, the warning would be
        lost **permanently**. No later tick would know to say it, because the
        only record that it was owed is the claim that already exists. Queuing
        the message in the same transaction as the claim is what makes "we
        decided to warn" imply "the warning will be delivered".

        The invariant, stated precisely: **a message is never orphaned from its
        claim.** When there is no recipient configured there is no message to
        queue, and the claim commits alone - not a broken invariant, but the
        absence of the thing the invariant is about.

        One unit per notice, which is the rule the scheduler follows too:
        **the transaction boundary follows the aggregate, not the batch.** A tick
        that hits an error on the fourth plan must not roll back the warning it
        already gave about the first three - those warnings were really issued,
        and forgetting them would mean issuing them twice.

        The losing path matters as much as the winning one: a tick that finds the
        occurrence already claimed rolls back and queues nothing, so an
        overlapping second tick cannot post a duplicate message.
        """
        uow = self._unit_of_work_factory.start()
        try:
            claimed = uow.notices.claim(
                PlanNotice(
                    plan_id=notice.plan_id,
                    due_at=notice.due_at,
                    raised_at=notice.raised_at,
                )
            )
            if not claimed:
                # Nothing was written, so there is nothing to undo - but the
                # connection still has to be released, and saying so explicitly
                # reads better than falling out of the function with a live unit.
                uow.rollback()
                return False

            message = self._compose(notice)
            if message is not None:
                uow.outbound_messages.enqueue(message)
            uow.commit()
            return True
        except BaseException:
            uow.rollback()
            raise

    def _compose(self, notice: UpcomingNotice) -> OutboundMessage | None:
        """Write the warning's words, once, at the moment it is queued.

        ``None`` when no recipient is configured - see ``__init__``.

        The text is rendered *here* rather than in the channel, and that is a
        deliberate split. Words written at send time would mean two channels
        could word the same warning differently, and that changing a sentence
        would silently rewrite messages already sitting in the queue. Composing
        once at enqueue makes the row the authoritative record of what was owed,
        and leaves the channel one job: to put those exact bytes on the wire.

        The wait is computed from ``due_at - raised_at`` rather than from the
        window constant, because a tick landing late in the window should say the
        fifteen minutes that are actually left, not the thirty the window is
        wide. Floored to whole minutes for the reason the CLI floors it: rounding
        up promises time that is not there.
        """
        if self._recipient is None:
            return None

        minutes = int((notice.due_at - notice.raised_at).total_seconds() // 60)
        unit = "minute" if minutes == 1 else "minutes"
        return OutboundMessage(
            plan_id=notice.plan_id,
            due_at=notice.due_at,
            recipient=self._recipient,
            subject=f"Payout of {notice.amount} in {minutes} {unit}",
            body=(
                f"The plan {notice.plan_name!r} pays {notice.amount} at "
                f"{notice.due_at.isoformat(timespec='minutes')}, "
                f"in {minutes} {unit}.\n\n"
                "This is a courtesy notice. The payout will go ahead at the "
                "time above whether or not you act on it.\n"
            ),
            # The pass's own notion of now, so replaying a tick composes the
            # same message rather than a slightly different one.
            created_at=notice.raised_at,
        )
