from abc import ABC, abstractmethod

from app.domain.notifications.outboundMessage import OutboundMessage


class OutboundMessageRepository(ABC):
    """Defines what an outbox store must do (the domain does not care how)."""

    @abstractmethod
    def enqueue(self, message: OutboundMessage) -> bool:
        """Queue a message, and say **whether this caller was the one that did**.

        The same claim idiom as ``PlanNoticeRepository.claim``, and here it is
        belt-and-braces rather than the primary defence. The notice claim already
        guarantees the warning is raised only once per occurrence, so only one
        tick ever reaches this call for a given ``(plan_id, due_at)`` - but a key
        that can only be inserted once means a double-send is *unrepresentable*
        rather than merely unlikely, and that is a better thing for a
        send-an-email-to-someone table to be.
        """
        pass

    @abstractmethod
    def pending(self) -> list[OutboundMessage]:
        """Every message still owed, oldest occurrence first.

        Settled messages - sent or expired - are not owed and do not come back.
        Ordered by ``due_at`` rather than by ``created_at`` so the most urgent
        warning goes first: a message about a payout in ten minutes is worth
        more than one about a payout in twenty-five, whichever was composed
        first.
        """
        pass

    @abstractmethod
    def save(self, message: OutboundMessage) -> OutboundMessage:
        """Write a message's current state back.

        Unlike the notice store, this **does** overwrite: a message genuinely
        has a truer second version once it has been sent, or once it has failed
        again. Forgetting that would mean re-sending a message on every tick
        forever.
        """
        pass
