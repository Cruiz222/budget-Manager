import uuid
from dataclasses import dataclass
from datetime import datetime

from .deliveryStatus import DeliveryStatus
from .exception import (
    InvalidNotificationAttemptsError,
    InvalidNotificationBodyError,
    InvalidNotificationCreatedAtError,
    InvalidNotificationEventKeyError,
    InvalidNotificationKindError,
    InvalidNotificationLastErrorError,
    InvalidNotificationRecipientError,
    InvalidNotificationSettledAtError,
    InvalidNotificationStatusError,
    InvalidNotificationSubjectError,
    InvalidNotificationSubjectIDError,
    MessageAlreadySettledError,
)
from .notificationKind import NotificationKind


@dataclass(init=False)
class Notification:
    """A message the system owes the outside world about something that happened.

    The younger sibling of ``OutboundMessage``, and the contrast between them is
    the whole reason this class exists rather than a column being added to that
    one. An ``OutboundMessage`` is a *warning*: it says an occurrence is coming,
    and it is worthless once the occurrence arrives. A ``Notification`` is a
    *receipt*: it says an event happened, and it is still true a week later.

    That single difference - **when the message stops being worth sending** -
    pulls three other differences behind it:

      - the key. A warning is identified by ``(plan_id, due_at)``, which is
        exactly the occurrence it is about. A receipt is identified by the
        *event*, which may be a plan run or a ledger row, so its key is derived
        and namespaced (see ``eventKey``).
      - the deadline. ``DeliverPendingMessages`` expires a warning at its
        occurrence. Nothing expires a notification, because there is no moment
        after which "your payout went out" becomes false.
      - the cardinality. There is one warning per occurrence. There is one
        notification per event, and an event is not always plan-shaped - a
        deposit has no plan id anywhere in it, which is the concrete reason
        ``OutboundMessage`` could not be reused: it *requires* one.

    **Facts vs lifecycle**, the split ``Transaction``, ``PlanRun`` and
    ``OutboundMessage`` all make. What happened, to whom, and the words are facts
    - set once, never edited, because the row is a record of what we owed and to
    whom. The status, attempt count and error are lifecycle, and change only
    through the methods below.

    The *recipient* is captured here rather than resolved at send time, for the
    same reason it is on a warning: changing ``BUDGET_NOTIFY_TO`` must not
    silently redirect a message that was already queued under the old address.

    Not frozen, unlike ``PlanRun`` - this record has a life after it is written,
    and it is edited by the methods below and nothing else.
    """

    event_key: str
    kind: NotificationKind
    subject_id: uuid.UUID
    recipient: str
    subject: str
    body: str
    created_at: datetime
    status: DeliveryStatus
    attempts: int
    last_error: str | None
    settled_at: datetime | None

    def __init__(
        self,
        event_key: str,
        kind: NotificationKind,
        subject_id: uuid.UUID,
        recipient: str,
        subject: str,
        body: str,
        created_at: datetime,
        status: DeliveryStatus = DeliveryStatus.PENDING,
        attempts: int = 0,
        last_error: str | None = None,
        settled_at: datetime | None = None,
    ):
        self.event_key = event_key
        self.kind = kind
        self.subject_id = subject_id
        self.recipient = recipient
        self.subject = subject
        self.body = body
        self.created_at = created_at
        self.status = status
        self.attempts = attempts
        self.last_error = last_error
        self.settled_at = settled_at
        self.__post_init__()

    @property
    def is_settled(self) -> bool:
        """Whether this message is done - sent or expired, and no longer owed."""
        return self.status.is_settled

    def is_stale(self, as_of: datetime) -> bool:
        """Never. A receipt is about the past, and the past does not go stale.

        Always ``False`` - see decision 30. Nothing about "your payout went out"
        becomes untrue with age, so there is no moment after which this message
        is better dropped than sent. It is retried until it lands, however long
        that takes, and the backlog that produces is accepted deliberately.

        It is here, rather than the drain carrying a flag for it, so that the
        drain never has to know which queue it is draining - see decision 46. A
        boolean on the drain would be decision 30's failure exactly: a switch
        that can be set the wrong way, failing silently, with the receipt
        expired on the tick it was written.

        ``as_of`` is unused, and that is the point rather than an oversight. The
        parameter is taken so that both messages answer the same question in the
        same shape; the answer simply does not depend on it.
        """
        return False

    def mark_sent(self, at: datetime) -> None:
        """Record that the message was delivered."""
        self._refuse_if_settled()
        self.status = DeliveryStatus.SENT
        self.settled_at = at

    def mark_expired(self, at: datetime) -> None:
        """Record that the message was dropped without being sent.

        Kept on this aggregate even though **nothing currently expires a
        notification**, and that is worth stating rather than leaving a reader to
        hunt for the caller. It is here because the lifecycle is complete without
        it and a half-lifecycle is a trap: ``DeliveryStatus`` has the member, the
        store round-trips it, and the first kind that *does* have a shelf life
        will need this method to exist. What it must not do is quietly acquire an
        invented cutoff in the meantime - see the class docstring.

        Expiry is a *decision*, not a failure: nothing went wrong, which is why
        this leaves ``last_error`` alone and settles the row for good.
        """
        self._refuse_if_settled()
        self.status = DeliveryStatus.EXPIRED
        self.settled_at = at

    def record_failure(self, error: str) -> None:
        """Record that a delivery attempt failed, leaving the message queued.

        Deliberately *not* terminal, and the return to ``PENDING`` is the whole
        point - see ``DeliveryStatus``. The message is still owed, so the next
        drain picks it up again, and ``attempts`` is the only trace of how many
        times it has been tried.
        """
        self._refuse_if_settled()
        self.attempts += 1
        self.last_error = error

    def _refuse_if_settled(self) -> None:
        """A settled message is history, and history does not get edited.

        The same shape as ``Transaction``'s "only pending transactions can be
        marked successful", and the same error ``OutboundMessage`` raises: a
        second ``mark_sent`` on a row already sent means the caller has lost
        track of which messages it is holding, and quietly letting it through
        would overwrite the moment the message really went.
        """
        if self.is_settled:
            raise MessageAlreadySettledError(
                f"this message is already {self.status.value}"
            )

    def __post_init__(self):
        # Non-empty, not merely a string - and the same check for the key, which
        # is the row's identity. An empty key would collide with every other
        # empty key, so the second notification would silently never be queued.
        if not isinstance(self.event_key, str) or not self.event_key.strip():
            raise InvalidNotificationEventKeyError("must be a non-empty string")

        if not isinstance(self.kind, NotificationKind):
            raise InvalidNotificationKindError(
                f"kind must be a NotificationKind, got {type(self.kind).__name__}"
            )

        # The plan id or the wallet id this is about. A UUID either way, which is
        # why it is one field and not two nullable ones.
        if not isinstance(self.subject_id, uuid.UUID):
            raise InvalidNotificationSubjectIDError("invalid subject id")

        # ``bool`` subclasses ``int``, so this check names ``bool`` explicitly -
        # the other half of decision 10. Without it, ``attempts=True`` would be a
        # legitimate one-attempt count.
        if isinstance(self.attempts, bool) or not isinstance(self.attempts, int):
            raise InvalidNotificationAttemptsError(
                f"attempts must be an int, got {type(self.attempts).__name__}"
            )
        if self.attempts < 0:
            raise InvalidNotificationAttemptsError("attempts cannot be negative")

        for value, error in (
            (self.recipient, InvalidNotificationRecipientError),
            (self.subject, InvalidNotificationSubjectError),
            (self.body, InvalidNotificationBodyError),
        ):
            if not isinstance(value, str) or not value.strip():
                raise error("must be a non-empty string")

        # The subclass trap, named explicitly as it is in ``PlanRun``,
        # ``PlanNotice`` and ``OutboundMessage``: a ``datetime`` passes
        # ``isinstance(x, date)``, so the check names the narrower type and says
        # so in the message. The fourth place it bites, and it keeps biting.
        if not isinstance(self.created_at, datetime):
            raise InvalidNotificationCreatedAtError(
                f"created_at must be a datetime, got {type(self.created_at).__name__}"
            )

        if not isinstance(self.status, DeliveryStatus):
            raise InvalidNotificationStatusError(
                f"status must be a DeliveryStatus, got {type(self.status).__name__}"
            )

        if self.last_error is not None and not isinstance(self.last_error, str):
            raise InvalidNotificationLastErrorError(
                f"last_error must be a string or None, "
                f"got {type(self.last_error).__name__}"
            )

        if self.settled_at is not None and not isinstance(self.settled_at, datetime):
            raise InvalidNotificationSettledAtError(
                f"settled_at must be a datetime or None, "
                f"got {type(self.settled_at).__name__}"
            )

        # The state and its explanation must agree, the same rule a blocked run
        # follows. A sent message with no moment recorded cannot say when it went;
        # a pending one carrying a settled moment is claiming to be finished and
        # unfinished at once.
        if self.is_settled and self.settled_at is None:
            raise InvalidNotificationSettledAtError(
                f"a {self.status.value} message must record when it settled"
            )

        if not self.is_settled and self.settled_at is not None:
            raise InvalidNotificationSettledAtError(
                f"a {self.status.value} message must not carry a settled moment"
            )

    def __str__(self) -> str:
        # To the minute, matching Schedule, PlanRun, PlanNotice, OutboundMessage
        # and the CLI's moment display. Seconds are not shown because nothing sets
        # them here: ``created_at`` comes from the event that caused the message.
        moment = self.created_at.isoformat(timespec="minutes")
        return (
            f"{self.kind.value} for {self.subject_id} at {moment} "
            f"to {self.recipient}: {self.status.value}"
        )
