from abc import ABC, abstractmethod

from app.domain.notifications.outboundMessage import OutboundMessage


class NotificationChannel(ABC):
    """Defines what a delivery channel must do - the *outbound port*.

    Everything else in ``app.domain.repositories`` describes storage, and this
    looks like it belongs there until you notice the direction. A repository is
    something the application *asks* for data; this is something the application
    *tells* to act. Both are seams between the domain and the outside world, and
    both are abstract for the same reason: so the inside never names a
    technology. The SMTP adapter behind this is one implementation, and a test
    that asserts delivery behaviour uses another - which is why no test in this
    suite ever opens a socket.

    **Raising is the interface, not an error path.** ``send`` is expected to raise
    when it cannot deliver, and the caller treats that as ordinary: the failure is
    recorded and the message is retried next tick. A channel that swallowed its
    failures and returned quietly would leave the queue unable to tell "sent"
    from "not sent" - and the row would be marked SENT by a message that never
    left.

    Deliberately *not* named ``NotificationRepository``, and deliberately not
    given a ``pending`` method. What is owed is the queue's business; what a
    channel does is hand one composed message to one outside world.
    """

    @abstractmethod
    def send(self, message: OutboundMessage) -> None:
        """Deliver one message, or raise.

        Returning normally means the channel accepted responsibility for the
        message and it can be marked SENT. Raising means it did not, and never
        means the message is lost - it stays PENDING with the reason recorded.
        """
        pass
