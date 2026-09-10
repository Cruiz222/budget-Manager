"""Drain the outbox: send what is owed, expire what is stale.

The second half of the transactional outbox. ``NotifyUpcomingRuns`` queues a
message in the same transaction as the notice it belongs to, so the decision to
warn is durable before anything is sent; this use case runs afterwards and does
the part that touches the network.

Splitting the two is what keeps a mail server out of a money-moving transaction.
The warning is composed and committed in one short unit, and delivered in
another pass that can fail as loudly as it likes without taking a payout with it.
"""

from dataclasses import dataclass
from datetime import datetime

from app.application.unit_of_work import UnitOfWorkFactory
from app.domain.notifications.notificationChannel import NotificationChannel
from app.domain.notifications.outboundMessage import OutboundMessage


@dataclass(frozen=True)
class DeliveryReport:
    """What one delivery pass did, described for whoever has to report it.

    Lists rather than counts, because the caller's job is to say *which* messages
    went out and which are stuck - a bare "2 sent" is not actionable, while a
    plan id and an error message are. A view, not a record: nothing here is
    stored, and nothing reads it to make a decision.
    """

    sent: tuple[OutboundMessage, ...] = ()
    expired: tuple[OutboundMessage, ...] = ()
    failed: tuple[OutboundMessage, ...] = ()
    deferred: tuple[OutboundMessage, ...] = ()

    @property
    def is_quiet(self) -> bool:
        """Whether there is nothing worth printing.

        The ordinary case by a wide margin: most ticks find an empty queue, and
        a tick that finds nothing to do should say nothing rather than report
        that it did nothing.
        """
        return not (self.sent or self.expired or self.failed or self.deferred)


class DeliverPendingMessages:
    """Send every queued message that is still worth sending.

    Two rules shape this, and both are about not making things worse:

    1. **A failure is recorded, never raised.** A mail server being down is not
       an incident - it is a message that goes out on the next tick. So a failed
       attempt leaves the message ``PENDING`` with the error on ``last_error``,
       and this method returns normally. What that buys is the property the whole
       feature rests on: **a broken SMTP server can never make ``plan tick``
       fail.** The money still moved, and the warning is still owed.

    2. **A stale warning is never sent.** A message whose occurrence has passed
       is *expired* rather than delivered. Telling someone a payout is thirty
       minutes away after it already happened is worse than silence - it is
       actively wrong, and it invites them to act on something that is over. This
       also removes any need for an arbitrary retry cap: the window bounds the
       attempts naturally, because once the occurrence passes, the message stops
       being retried at all.

    Expiry is checked before delivery and regardless of whether a channel is
    configured, because staleness is a fact about the clock rather than about
    delivery. A queue that only expired messages when a mail server was reachable
    would hold stale warnings forever on an install with no email at all.
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

    def execute(self, as_of: datetime) -> DeliveryReport:
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
            if message.due_at <= as_of:
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

    def _outstanding(self) -> list[OutboundMessage]:
        """Every message still owed, read in a unit of its own.

        A read-only unit, rolled back in a ``finally`` exactly as the notifier's
        ``_upcoming`` does. The messages that come back are plain in-memory
        objects, so they stay usable after the rollback - it releases the
        connection, not the values.
        """
        uow = self._unit_of_work_factory.start()
        try:
            return list(uow.outbound_messages.pending())
        finally:
            uow.rollback()

    def _expire(self, message: OutboundMessage, as_of: datetime) -> None:
        """Settle a message that is no longer worth sending."""
        uow = self._unit_of_work_factory.start()
        try:
            message.mark_expired(as_of)
            uow.outbound_messages.save(message)
            uow.commit()
        except BaseException:
            uow.rollback()
            raise

    def _attempt(self, message: OutboundMessage, as_of: datetime) -> bool:
        """Try to send one message. Returns whether it went.

        Note the shape: the send happens *before* the unit opens, and the write
        happens after it closes. That ordering is deliberate - no database
        transaction is held open across a network call to a mail server, which
        would otherwise be a transaction that can hang for as long as the socket
        timeout allows.

        The cost of that ordering is worth stating rather than hiding: if the
        send succeeds and the write then fails, the next tick will send the
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
            uow.outbound_messages.save(message)
            uow.commit()
        except BaseException:
            uow.rollback()
            raise
        return failure is None
