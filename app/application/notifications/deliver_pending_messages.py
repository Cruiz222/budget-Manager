"""The warning queue: send what is owed, expire what is stale.

A warning has a **deadline** - the occurrence it warns about - and that single
fact is the whole of what distinguishes this drain from the receipt one. It is
why messages are expired before delivery is even attempted, and why the window
doubles as a natural bound on retries: once the occurrence passes, the message
stops being retried at all, so no arbitrary retry cap is needed (decision 25).

The algorithm itself lives in ``DeliverQueue``, shared with the receipt drain.
This module names the store and says what makes a warning's queue different; it
does not restate the two delivery rules, which are written once where they are
implemented.
"""

from app.application.notifications.deliver_queue import DeliverQueue, DeliveryReport
from app.domain.notifications.outboundMessage import OutboundMessage

#: Re-exported so that ``DeliveryReport`` keeps a stable home for callers that
#: have always imported it from here. It is *defined* in ``deliver_queue``, beside
#: the drain that produces it - this is an alias, not a second copy.
__all__ = ["DeliverPendingMessages", "DeliveryReport"]


class DeliverPendingMessages(DeliverQueue[OutboundMessage]):
    """Send every queued warning that is still worth sending.

    What is distinctive is stated on ``OutboundMessage.is_stale``: a warning
    expires at its occurrence, strictly, so a warning about a payout that is
    happening right now is already too late to send. The queue is read oldest
    occurrence first, so the most urgent warning goes first.
    """

    def _queue(self, uow):
        return uow.outbound_messages
