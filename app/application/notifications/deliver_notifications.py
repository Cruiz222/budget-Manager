"""Drain the receipts: send what is owed, and drop nothing on the way.

The sibling of ``DeliverPendingMessages``, and the reason they are two classes
rather than one parameterised pass is the whole content of this phase. A warning
has a *deadline* - the occurrence it warns about - and that single fact shapes its
drain: messages are expired before delivery is even attempted, and the window
doubles as a natural bound on retries. A receipt has no deadline at all. Sharing
one pass over both queues would mean the warning's cutoff rules leaking into the
receipt queue, and the leak would be silent: the receipt would simply never be
sent, having been "expired" the same tick it was written, because a receipt is
*created at* its moment.

So the two rules that are genuinely common are written twice, and the one rule
that differs is written once, in the right place. What is shared is the
vocabulary - ``DeliveryReport`` - not the algorithm. That is a deliberate cut,
and it is listed under *Still open* in the README.
"""

from datetime import datetime

from app.application.notifications.deliver_pending_messages import DeliveryReport
from app.application.unit_of_work import UnitOfWorkFactory
from app.domain.notifications.notification import Notification
from app.domain.notifications.notificationChannel import NotificationChannel


class DeliverNotifications:
    """Send every queued receipt, and never drop one.

    It keeps the two rules ``DeliverPendingMessages`` states, unchanged and for
    the same reasons:

    1. **A failure is recorded, never raised.** A mail server being down is not an
       incident - it is a message that goes out next time. A failed attempt leaves
       the notification ``PENDING`` with the error on ``last_error``, and this
       method returns normally. What that buys is the property the whole feature
       rests on: **a broken SMTP server can never make a deposit fail, or a tick
       fail.** The money has already moved and the receipt is still owed.

    2. **No database transaction is held across the send.** The send happens
       before the unit opens and the write happens after it closes, so a hang on
       the socket is never a hang on a transaction. The cost is stated rather than
       hidden, exactly as it is there: if the send succeeds and the write then
       fails, the next drain sends the message a second time. This is an
       **at-least-once channel**, and there is no alternative - the message leaves
       the process and lands somewhere no transaction of ours can reach. A
       duplicate receipt is obviously the cheaper mistake.

    **The expiry branch is absent by design, not forgotten.** There is no
    ``if notification.due_at <= as_of`` here, and there is no ``due_at`` to
    compare - a notification is about the past. The rule it replaces existed
    because a warning becomes *false* when its moment passes; nothing makes "your
    payout went out" false. ``as_of`` therefore decides nothing in this class. It
    is still taken, because it is the moment recorded on a successful send, and
    because both drains then have the same shape: one pass, one idea of now,
    replayable.

    **The consequence, stated plainly.** A notification that cannot be delivered
    is retried on every drain, forever - there is no natural cap where the warning
    has one. That is the right default: the message is still true, still owed, and
    dropping it after N attempts would lose the one receipt that matters. But it
    does mean a wedged mail server makes each drain slower in proportion to the
    backlog, since each attempt can wait out the socket timeout. A retry cap is
    the obvious next move and is recorded in the README rather than invented here.
    """

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        channel: NotificationChannel | None = None,
    ):
        self._unit_of_work_factory = unit_of_work_factory
        # ``None`` means this installation cannot send: no email is configured.
        # That is a normal state, not an error. Unlike the warning queue there is
        # nothing to age out in it - receipts queue up and are delivered the
        # moment a configuration exists again, however long that takes.
        self._channel = channel

    def execute(self, as_of: datetime) -> DeliveryReport[Notification]:
        """Send everything currently queued, and report what happened.

        Note what is *not* here: nothing is settled without being sent. A
        notification leaves this loop only as SENT or still PENDING.
        """
        sent = []
        failed = []
        deferred = []

        for notification in self._outstanding():
            if self._channel is None:
                deferred.append(notification)
            elif self._attempt(notification, as_of):
                sent.append(notification)
            else:
                failed.append(notification)

        return DeliveryReport(
            sent=tuple(sent),
            failed=tuple(failed),
            deferred=tuple(deferred),
        )

    def _outstanding(self) -> list[Notification]:
        """Every notification still owed, read in a unit of its own.

        A read-only unit, rolled back in a ``finally`` - the same shape as the
        warning drain's. The values that come back are plain in-memory objects, so
        they stay usable after the rollback: it releases the connection, not them.
        """
        uow = self._unit_of_work_factory.start()
        try:
            return list(uow.notifications.pending())
        finally:
            uow.rollback()

    def _attempt(self, notification: Notification, as_of: datetime) -> bool:
        """Try to send one notification. Returns whether it went.

        Structurally identical to the warning drain's ``_attempt``, and identical
        on purpose - the narrow ``except Exception`` around the send alone is
        load-bearing in both. If the ``save`` below were inside that clause, a
        database error would be recorded as a *delivery* failure, and the receipt
        would be retried forever against a mail server that was working perfectly.
        """
        failure = None
        try:
            self._channel.send(notification)
        except Exception as exc:
            failure = f"{type(exc).__name__}: {exc}"

        uow = self._unit_of_work_factory.start()
        try:
            if failure is None:
                notification.mark_sent(as_of)
            else:
                notification.record_failure(failure)
            uow.notifications.save(notification)
            uow.commit()
        except BaseException:
            uow.rollback()
            raise
        return failure is None
