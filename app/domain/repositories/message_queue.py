from typing import Protocol, TypeVar

T = TypeVar("T")


class MessageQueue(Protocol[T]):
    """What a delivery pass needs from a store of owed messages.

    Two operations, and a drain asks its store exactly these: read every message
    still owed, and write one back with its delivery outcome recorded. That is
    the whole of a queue's job once a drain is running, and it is what
    ``OutboundMessageRepository`` and ``NotificationRepository`` already do.

    ``enqueue`` is deliberately **not** here. Queueing is the business of the use
    case that composes a message - ``NotifyUpcomingRuns``, or a run writing its
    own receipt - and a drain never queues anything. A port that described the
    whole repository would be two ports in one coat, and the drain would appear
    to have an opinion about how messages come into being.

    **A ``Protocol`` rather than the ``ABC`` every other port in this package
    uses**, and that departure is decision 47. An ABC says *inherit from me*,
    which would mean editing ``OutboundMessageRepository``,
    ``NotificationRepository`` and both SQLite implementations to declare a
    relationship they already satisfy. A Protocol says *look like this*, and the
    two stores already do - so nothing in infrastructure moves at all, and the
    conformance is checked by the type checker rather than at import.

    The cost is that the relationship is not visible in the class definition -
    a reader of ``NotificationRepository`` learns it is a queue from here and
    from the drain, not from an inheritance list. If a third queue ever appears
    and inheriting starts to carry its weight, promoting this to an ABC is a
    one-line change per store.
    """

    def pending(self) -> list[T]:
        """Every message still owed, in whatever order this store considers fair.

        The two stores order differently, and each is right for its own queue: a
        warning sorts by ``due_at`` so the most urgent goes first, a receipt by
        ``created_at`` because it reports something that already happened and the
        fair order is the order things happened in. The drain does not care,
        which is why the contract says only "still owed".
        """
        ...

    def save(self, message: T) -> T:
        """Write a message's current state back.

        Overwriting is expected and load-bearing: a message genuinely has a
        truer second version once it has been sent, or once it has failed again.
        A store that refused a second write would re-send the message on every
        pass forever.
        """
        ...
