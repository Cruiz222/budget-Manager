"""Drain a delivery queue: send what is owed, expire what is stale.

The second half of the transactional outbox. Something upstream queues a message
in the same transaction as the fact it describes - ``NotifyUpcomingRuns`` for a
warning, a run or a wallet command for a receipt - so the decision to speak is
durable before anything is sent. This use case runs afterwards and does the part
that touches the network.

Splitting the two is what keeps a mail server out of a money-moving transaction.
The message is composed and committed in one short unit, and delivered in another
pass that can fail as loudly as it likes without taking a payout with it.

**One class serves both queues**, and the reason it can is that the two differ in
exactly two places - which store is read, and whether a message can go stale -
and neither of those is part of the *algorithm*. Which store is the subclass's
one statement (``_queue``); whether a message is stale is the message's own
answer (``is_stale``). What is left is a single pass, written once: send what is
owed, record a failure rather than raising it, and hold no transaction across the
send.

Until decision 45 the two passes were separate classes with the algorithm written
twice. That was a deliberate sequencing choice, not an oversight - the risky half
of the phase was a receipt landing inside a payout transaction, and moving the
well-tested warning path at the same time would have meant debugging two things
at once. Both shapes are now known rather than predicted, so the seam is drawn.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Generic, TypeVar

from app.application.unit_of_work import UnitOfWork, UnitOfWorkFactory
from app.domain.notifications.notificationChannel import NotificationChannel
from app.domain.repositories.message_queue import MessageQueue

#: The message type a report describes - an ``OutboundMessage`` for the warning
#: queue, a ``Notification`` for the receipt one.
T = TypeVar("T")


@dataclass(frozen=True)
class DeliveryReport(Generic[T]):
    """What one delivery pass did, described for whoever has to report it.

    Lists rather than counts, because the caller's job is to say *which* messages
    went out and which are stuck - a bare "2 sent" is not actionable, while a
    plan id and an error message are. A view, not a record: nothing here is
    stored, and nothing reads it to make a decision.

    **Generic over the message type**, which is what lets one dataclass serve
    both queues. There are two drains - the warning queue and the receipt queue -
    and they keep identical accounts of different aggregates. Without this
    parameter there would be a second, identical dataclass, kept in step by hand
    for no benefit: the *reporting* rule (say which, not how many; stay silent
    when there is nothing) is a property of delivery, not of what is being
    delivered.

    ``expired`` is always empty for a receipt drain, by design - nothing expires
    a notification (see ``Notification.is_stale``). The field stays because a
    report is the same four things whichever queue it describes, and a slot that
    means "nothing was dropped" is more honest than a report with no way to say
    it. It is now structurally present for both queues rather than omitted from
    one, which is the same argument the shared drain makes.
    """

    sent: tuple[T, ...] = ()
    expired: tuple[T, ...] = ()
    failed: tuple[T, ...] = ()
    deferred: tuple[T, ...] = ()

    @property
    def is_quiet(self) -> bool:
        """Whether there is nothing worth printing.

        The ordinary case by a wide margin: most passes find an empty queue, and
        a pass that finds nothing to do should say nothing rather than report
        that it did nothing.
        """
        return not (self.sent or self.expired or self.failed or self.deferred)


class DeliverQueue(Generic[T], ABC):
    """Send every queued message that is still worth sending.

    Two rules shape every pass, and both are about not making things worse:

    1. **A failure is recorded, never raised.** A mail server being down is not
       an incident - it is a message that goes out on the next pass. So a failed
       attempt leaves the message ``PENDING`` with the error on ``last_error``,
       and ``execute`` returns normally. What that buys is the property the whole
       feature rests on: **a broken SMTP server can never make a command or a
       tick fail.** The money still moved, and the message is still owed. It is
       also why there is no ``FAILED`` status - a failure that advanced a message
       to a terminal state would be a message thrown away (decision 24).

    2. **No database transaction is held across the send.** The send happens
       before the unit opens and the write happens after it closes, so a hang on
       the socket is never a hang on a transaction. The cost is stated rather
       than hidden: if the send succeeds and the write then fails, the next pass
       sends the message a second time. **This is an at-least-once channel**, and
       there is no alternative - the message leaves the process and lands
       somewhere no transaction of ours can reach. Between a duplicate warning
       and a lost one, a duplicate is obviously the cheaper mistake.

    **Staleness is asked of the message, not decided here.** That placement is
    decision 46, and it is what makes the merge safe rather than merely tidy. A
    message that cannot go stale answers ``False`` and so can never be expired by
    a drain, however the drain is built. The alternative - a flag on this class -
    is the failure decision 30 named: a switch that can be set the wrong way and
    fails *silently*, with the receipt expired on the tick it was written.

    Expiry is checked before delivery and regardless of whether a channel is
    configured, because staleness is a fact about the clock rather than about
    delivery. A queue that only expired stale messages when a mail server was
    reachable would hold them forever on an install with no email at all
    (decision 25).
    """

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        channel: NotificationChannel | None = None,
    ):
        self._unit_of_work_factory = unit_of_work_factory
        # ``None`` means this installation cannot send: no email is configured.
        # That is a normal state, not an error - messages are still expired on
        # time, and anything queued from a previous configuration simply stays
        # owed until one exists again.
        self._channel = channel

    @abstractmethod
    def _queue(self, uow: UnitOfWork) -> MessageQueue[T]:
        """The store this pass owes messages from.

        The one thing a subclass has to say, and the only difference between the
        two drains. Everything else - what a message is, whether it can go stale
        - the messages answer for themselves.
        """
        ...

    def execute(self, as_of: datetime) -> DeliveryReport[T]:
        """Settle or send everything currently queued, and report what happened.

        ``as_of`` is taken rather than read from the clock for the same reason it
        is everywhere else - testability, and so one pass has one idea of what
        "now" is. It is also the moment recorded as ``settled_at``, so replaying
        a pass settles messages with the same timestamp rather than a slightly
        different one.
        """
        sent = []
        expired = []
        failed = []
        deferred = []

        for message in self._outstanding():
            if message.is_stale(as_of):
                self._expire(message, as_of)
                expired.append(message)
            elif self._channel is None:
                deferred.append(message)
            elif self._attempt(message, as_of):
                sent.append(message)
            else:
                failed.append(message)

        return DeliveryReport(
            sent=tuple(sent),
            expired=tuple(expired),
            failed=tuple(failed),
            deferred=tuple(deferred),
        )

    def _outstanding(self) -> list[T]:
        """Every message still owed, read in a unit of its own.

        A read-only unit, rolled back in a ``finally`` exactly as the notifier's
        ``_upcoming`` does. The messages that come back are plain in-memory
        objects, so they stay usable after the rollback - it releases the
        connection, not the values.
        """
        uow = self._unit_of_work_factory.start()
        try:
            return list(self._queue(uow).pending())
        finally:
            uow.rollback()

    def _expire(self, message: T, as_of: datetime) -> None:
        """Settle a message that is no longer worth sending."""
        uow = self._unit_of_work_factory.start()
        try:
            message.mark_expired(as_of)
            self._queue(uow).save(message)
            uow.commit()
        except BaseException:
            uow.rollback()
            raise

    def _attempt(self, message: T, as_of: datetime) -> bool:
        """Try to send one message. Returns whether it went.

        Note the shape: the send happens *before* the unit opens, and the write
        happens after it closes. That ordering is deliberate - no database
        transaction is held open across a network call to a mail server, which
        would otherwise be a transaction that can hang for as long as the socket
        timeout allows.

        The cost of that ordering is worth stating rather than hiding: if the
        send succeeds and the write then fails, the next pass will send the
        message a second time. **This is an at-least-once channel**, and there is
        no alternative - the message leaves the process and lands somewhere no
        transaction of ours can reach. Given the choice between a duplicate
        warning and a lost one, a duplicate is obviously the cheaper mistake, and
        a warning that arrives twice is a far smaller sin than a payout the user
        was never told about.

        The failure is caught narrowly - ``Exception``, around the send alone -
        and that narrowness is load-bearing. If the ``save`` below were inside
        that clause, a database error would be recorded as a *delivery* failure,
        and the message would be retried forever against a mail server that was
        working perfectly.
        """
        failure = None
        try:
            self._channel.send(message)
        except Exception as exc:
            failure = f"{type(exc).__name__}: {exc}"

        uow = self._unit_of_work_factory.start()
        try:
            if failure is None:
                message.mark_sent(as_of)
            else:
                message.record_failure(failure)
            self._queue(uow).save(message)
            uow.commit()
        except BaseException:
            uow.rollback()
            raise
        return failure is None
