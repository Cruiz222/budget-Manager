from abc import ABC, abstractmethod

from app.domain.notifications.notification import Notification


class NotificationRepository(ABC):
    """Defines what a notification store must do (the domain does not care how).

    The same three operations as ``OutboundMessageRepository``, and the same
    shapes - because the two stores answer the same three questions. What differs
    is only the key they answer them about: a warning is keyed by its occurrence,
    a notification by its event.
    """

    @abstractmethod
    def enqueue(self, notification: Notification) -> bool:
        """Queue a notification, and say **whether this caller was the one that did**.

        The same claim idiom as ``PlanNoticeRepository.claim`` and
        ``OutboundMessageRepository.enqueue``: the insert *is* the decision, and
        the row count is the answer to "was this mine to queue?".

        It is load-bearing here in a way it is not for a warning. A warning is
        protected by the notice claim upstream, so a second enqueue for the same
        occurrence is a bug that merely does nothing. A notification often has no
        such protection: the idempotent path through ``WalletOperation`` returns
        an *existing* ledger row rather than refusing, so repeating a deposit with
        the same reference reaches this method a second time quite legitimately.
        This is what stops that being a second email.
        """
        pass

    @abstractmethod
    def pending(self) -> list[Notification]:
        """Every notification still owed, oldest first.

        Settled messages - sent or expired - are not owed and do not come back.
        Ordered by ``created_at`` rather than by a subject id, because a
        notification is about something that *already happened*: there is no
        upcoming moment to sort by, and the fair order is the order things
        happened in.
        """
        pass

    @abstractmethod
    def save(self, notification: Notification) -> Notification:
        """Write a notification's current state back.

        This **does** overwrite, unlike the notice store and like the outbox: a
        notification genuinely has a truer second version once it has been sent,
        or once it has failed again. Forgetting that would mean re-sending it on
        every drain forever.
        """
        pass
