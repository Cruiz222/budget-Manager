"""The receipt queue: send what is owed, and drop nothing on the way.

A receipt has **no deadline at all**, and that one difference is why the two
queues exist rather than one queue with a type column. Nothing about "your payout
went out" becomes untrue with age, so no receipt is ever expired - not on the
tick it was written, and not ever (decision 30).

That absence is what the shared drain had to be able to express without a flag,
and it is expressed by the message: ``Notification.is_stale`` returns ``False``,
always. So this drain never expires anything, not because a branch was left out
here, but because the message it holds cannot answer otherwise (decision 46).

**The consequence, stated plainly.** A receipt that cannot be delivered is
retried on every pass, forever - there is no natural cap where a warning has one.
That is the right default: the message is still true, still owed, and dropping it
after N attempts would lose the one receipt that matters. But it does mean a
wedged mail server makes each pass slower in proportion to the backlog, since
each attempt can wait out the socket timeout. A retry cap is the obvious next
move and is recorded in the README rather than invented here.
"""

from app.application.notifications.deliver_queue import DeliverQueue, DeliveryReport
from app.domain.notifications.notification import Notification

__all__ = ["DeliverNotifications", "DeliveryReport"]


class DeliverNotifications(DeliverQueue[Notification]):
    """Send every queued receipt, and never drop one.

    The queue is read oldest event first, because a receipt reports something
    that already happened and the fair order is the order things happened in.

    Note what is *not* here: nothing is settled without being sent. A
    notification leaves the drain only as SENT or still PENDING.
    """

    def _queue(self, uow):
        return uow.notifications
