import uuid
from dataclasses import dataclass
from datetime import datetime

from .exception import (
    InvalidOutboundMessageAttemptsError,
    InvalidOutboundMessageBodyError,
    InvalidOutboundMessageCreatedAtError,
    InvalidOutboundMessageDueAtError,
    InvalidOutboundMessageLastErrorError,
    InvalidOutboundMessagePlanIDError,
    InvalidOutboundMessageRecipientError,
    InvalidOutboundMessageSettledAtError,
    InvalidOutboundMessageStatusError,
    InvalidOutboundMessageSubjectError,
    MessageAlreadySettledError,
)
from .outboundMessageStatus import OutboundMessageStatus


@dataclass(init=False)
class OutboundMessage:
    """A message the system owes the outside world, queued before it is sent.

    This is the *outbox* half of the transactional outbox pattern. The warning
    about an upcoming run is claimed once and never raised again (see
    ``PlanNotice``), so if the claim could commit while the message was never
    written, that warning would be lost **permanently** - no later tick would
    know to say it. Writing this row in the *same* transaction as the claim is
    what makes "we decided to warn" imply "the warning will be delivered". The
    only reason this class exists is that guarantee.

    Which is why the row is written before anything is sent, and why sending
    happens in a later pass over ``PENDING`` rows. A message that is sent the
    moment it is composed would have to be sent inside the claim's transaction -
    holding a database write open across a network call to a mail server, and
    rolling the whole warning back if the mail server was down. Deferring the
    send costs one extra tick of latency and buys a warning that survives a dead
    SMTP server.

    **Facts vs lifecycle**, the same split ``Transaction`` makes. The plan it is
    about, the occurrence, the recipient and the words are facts - set once and
    never edited, because the row is a record of what we owed and to whom. The
    status, attempt count and error are lifecycle, and change only through the
    methods below.

    The *recipient* is captured here rather than resolved when the message is
    sent. If the address were read from configuration at send time, changing
    ``BUDGET_NOTIFY_TO`` would silently redirect every message already queued -
    including ones composed under the old address, which is a different promise
    than the one the queue is holding.

    Its *natural key* is ``(plan_id, due_at)`` - one warning per plan per
    occurrence, the same key ``PlanNotice`` and ``PlanRun`` use. The store
    enforces it with a primary key, so a double enqueue is unrepresentable
    rather than merely unlikely.
    """

    plan_id: uuid.UUID
    due_at: datetime
    recipient: str
    subject: str
    body: str
    created_at: datetime
    status: OutboundMessageStatus
    attempts: int
    last_error: str | None
    settled_at: datetime | None

    def __init__(
        self,
        plan_id: uuid.UUID,
        due_at: datetime,
        recipient: str,
        subject: str,
        body: str,
        created_at: datetime,
        status: OutboundMessageStatus = OutboundMessageStatus.PENDING,
        attempts: int = 0,
        last_error: str | None = None,
        settled_at: datetime | None = None,
    ):
        self.plan_id = plan_id
        self.due_at = due_at
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
    def key(self) -> tuple[uuid.UUID, datetime]:
        """The natural key: one message per plan per occurrence."""
        return (self.plan_id, self.due_at)

    @property
    def is_settled(self) -> bool:
        """Whether this message is done - sent or expired, and no longer owed."""
        return self.status.is_settled

    def mark_sent(self, at: datetime) -> None:
        """Record that the message was delivered."""
        self._refuse_if_settled()
        self.status = OutboundMessageStatus.SENT
        self.settled_at = at

    def mark_expired(self, at: datetime) -> None:
        """Record that the message was dropped without being sent.

        Expiry is a *decision*, not a failure: the occurrence came and went, and
        a warning about it is no longer news. Nothing went wrong, which is why
        this leaves ``last_error`` alone and settles the row for good.
        """
        self._refuse_if_settled()
        self.status = OutboundMessageStatus.EXPIRED
        self.settled_at = at

    def record_failure(self, error: str) -> None:
        """Record that a delivery attempt failed, leaving the message queued.

        Deliberately *not* terminal, and the return to ``PENDING`` is the whole
        point - see ``OutboundMessageStatus``. The message is still owed, so the
        next tick picks it up again, and ``attempts`` is the only trace of how
        many times it has been tried.
        """
        self._refuse_if_settled()
        self.attempts += 1
        self.last_error = error

    def _refuse_if_settled(self) -> None:
        """A settled message is history, and history does not get edited.

        The same shape as ``Transaction``'s "only pending transactions can be
        marked successful": a second ``mark_sent`` on a row already sent means
        the caller has lost track of which messages it is holding, and quietly
        letting it through would overwrite the moment the message really went.
        """
        if self.is_settled:
            raise MessageAlreadySettledError(
                f"this message is already {self.status.value}"
            )

    def __post_init__(self):
        if not isinstance(self.plan_id, uuid.UUID):
            raise InvalidOutboundMessagePlanIDError("invalid plan id")

        # The occurrence this warning is about, so it matches what the notice
        # was claimed for. The subclass trap applies here as it does in
        # ``PlanRun`` and ``PlanNotice``: a ``datetime`` passes
        # ``isinstance(x, date)``, so the check names the narrower type.
        if not isinstance(self.due_at, datetime):
            raise InvalidOutboundMessageDueAtError(
                f"due_at must be a datetime, got {type(self.due_at).__name__}"
            )

        # Non-empty, not merely a string. An empty recipient is a message that
        # can never be delivered, and it would fail silently at send time - which
        # is the worst place to discover it, because by then the notice has been
        # claimed and there is nothing left to re-raise.
        for value, error in (
            (self.recipient, InvalidOutboundMessageRecipientError),
            (self.subject, InvalidOutboundMessageSubjectError),
            (self.body, InvalidOutboundMessageBodyError),
        ):
            if not isinstance(value, str) or not value.strip():
                raise error("must be a non-empty string")

        if not isinstance(self.created_at, datetime):
            raise InvalidOutboundMessageCreatedAtError(
                f"created_at must be a datetime, got {type(self.created_at).__name__}"
            )

        if not isinstance(self.status, OutboundMessageStatus):
            raise InvalidOutboundMessageStatusError(
                f"status must be an OutboundMessageStatus, "
                f"got {type(self.status).__name__}"
            )

        # ``bool`` subclasses ``int``, so this check names ``bool`` explicitly -
        # the other half of decision 10. Without it, ``attempts=True`` would be a
        # legitimate one-attempt count.
        if isinstance(self.attempts, bool) or not isinstance(self.attempts, int):
            raise InvalidOutboundMessageAttemptsError(
                f"attempts must be an int, got {type(self.attempts).__name__}"
            )
        if self.attempts < 0:
            raise InvalidOutboundMessageAttemptsError("attempts cannot be negative")

        if self.last_error is not None and not isinstance(self.last_error, str):
            raise InvalidOutboundMessageLastErrorError(
                f"last_error must be a string or None, "
                f"got {type(self.last_error).__name__}"
            )

        if self.settled_at is not None and not isinstance(self.settled_at, datetime):
            raise InvalidOutboundMessageSettledAtError(
                f"settled_at must be a datetime or None, "
                f"got {type(self.settled_at).__name__}"
            )

        # The same shape of rule as a blocked run needing a reason: the state and
        # its explanation must agree. A sent message with no moment recorded
        # cannot say when it went; a pending one carrying a settled moment is
        # claiming to be finished and unfinished at once.
        if self.is_settled and self.settled_at is None:
            raise InvalidOutboundMessageSettledAtError(
                f"a {self.status.value} message must record when it settled"
            )

        if not self.is_settled and self.settled_at is not None:
            raise InvalidOutboundMessageSettledAtError(
                f"a {self.status.value} message must not carry a settled moment"
            )

    def __str__(self) -> str:
        # To the minute, matching Schedule, PlanRun, PlanNotice and the CLI's
        # moment display. Seconds are not shown because nothing sets them here:
        # ``due_at`` comes from the plan's anchor, which a human typed.
        moment = self.due_at.isoformat(timespec="minutes")
        return f"message to {self.recipient} about {self.plan_id} due {moment}: {self.status.value}"
