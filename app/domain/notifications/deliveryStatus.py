from enum import Enum


class DeliveryStatus(Enum):
    """Where a queued message has got to.

    Named for the *delivery*, not for the thing being delivered, because two
    kinds of message now share these three states:

      - ``OutboundMessage``, a warning about an occurrence that is still coming
      - ``Notification``, a receipt for an event that has already happened

    What they have in common is exactly what this enum describes: a message was
    composed, and it has either gone out, been given up on, or is still owed.
    What differs between them - whether being late makes a message worthless -
    is a property of the message, not of its delivery, and lives on the message.

    This was called ``OutboundMessageStatus``, which was accurate while there was
    one kind of message and became a lie the moment a second arrived. The rename
    is a pure rename: the member *names* below are what is persisted (see
    ``enum_to_text``), and none of them changed, so no database on disk cares.

    Three members, and the two that are *missing* are the interesting part.

    There is no ``FAILED``. A delivery attempt that dies does not settle the
    message - it leaves it ``PENDING`` with the error on ``last_error`` and one
    more ``attempts``, and the next tick tries again. Making a failure terminal
    would mean a mail server that was down for five minutes loses the message
    forever, which is precisely the outcome the queue exists to prevent. This
    mirrors ``PlanRun``'s run/reason split: the status says what the thing *is*,
    and the error says what went wrong along the way.

    There is no ``SENDING``. A message is claimed and settled inside the same
    short unit of work, so an in-flight state would only ever be observable to a
    crashed process - and a crashed process cannot read it.
    """

    PENDING = "pending"
    """Queued, and not yet delivered. The only state that is ever retried."""

    SENT = "sent"
    """Delivered to the channel. Terminal."""

    EXPIRED = "expired"
    """Given up on without being sent. Terminal, and never sent."""

    @property
    def is_settled(self) -> bool:
        """Whether this message is finished with, one way or the other.

        ``SENT`` and ``EXPIRED`` are both final; only ``PENDING`` is still owed.
        Written as a property rather than as two comparisons at each call site,
        so adding a fourth terminal state later is one edit rather than a hunt.
        """
        return self is not DeliveryStatus.PENDING
