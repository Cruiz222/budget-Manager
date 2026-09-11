import sqlite3

from app.domain.notifications.outboundMessage import OutboundMessage
from app.domain.notifications.deliveryStatus import DeliveryStatus
from app.domain.repositories.outbound_message_repository import (
    OutboundMessageRepository,
)
from app.infrastructure.persistence.serialization import (
    datetime_to_text,
    enum_to_text,
    text_to_datetime,
    text_to_enum,
    text_to_uuid,
    uuid_to_text,
)


class SqliteOutboundMessageRepository(OutboundMessageRepository):
    """Outbox store over a single SQLite connection.

    Keyed on ``(plan_id, due_at)`` - the natural key, and the primary key - so a
    plan has at most one message per occurrence and the database, not a check in
    Python, is what enforces it.
    """

    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection
        self._connection.row_factory = sqlite3.Row

    def enqueue(self, message: OutboundMessage) -> bool:
        """Insert the message, and report whether this call is the one that did.

        ``DO NOTHING`` rather than ``DO UPDATE``, unlike ``save`` below, and the
        difference is the point: this is a *claim*, not a write. The message was
        composed once, at the moment the notice was raised, and its words are
        frozen there. A second enqueue of the same occurrence is a bug that
        should change nothing - certainly not the text of a warning that is
        already queued or already gone.

        The row count is the return value for the same reason it is in the
        notice store: SQLite reports zero rows changed when the conflict fires,
        so the write itself answers "was this mine to queue?".
        """
        cursor = self._connection.execute(
            """
            INSERT INTO outbound_messages (
                plan_id, due_at, recipient, subject, body,
                status, attempts, last_error, created_at, settled_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(plan_id, due_at) DO NOTHING
            """,
            (
                uuid_to_text(message.plan_id),
                message.due_at.isoformat(),
                message.recipient,
                message.subject,
                message.body,
                enum_to_text(message.status),
                message.attempts,
                message.last_error,
                datetime_to_text(message.created_at),
                datetime_to_text(message.settled_at),
            ),
        )
        return cursor.rowcount == 1

    def pending(self) -> list[OutboundMessage]:
        rows = self._connection.execute(
            """
            SELECT plan_id, due_at, recipient, subject, body,
                   status, attempts, last_error, created_at, settled_at
            FROM outbound_messages
            WHERE status = ?
            ORDER BY due_at
            """,
            (enum_to_text(DeliveryStatus.PENDING),),
        ).fetchall()
        return [self._row_to_message(row) for row in rows]

    def save(self, message: OutboundMessage) -> OutboundMessage:
        """Write the message's current state, overwriting whatever was there.

        The opposite conflict clause to ``enqueue``, and both are correct. A
        message that has just been sent, or has just failed its third attempt,
        really does have a truer second version - and if this did not overwrite,
        every tick would find it still PENDING and send it again, forever. The
        claim protects the *composition* of the message; this protects its
        *progress*.
        """
        self._connection.execute(
            """
            INSERT INTO outbound_messages (
                plan_id, due_at, recipient, subject, body,
                status, attempts, last_error, created_at, settled_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(plan_id, due_at) DO UPDATE SET
                status     = excluded.status,
                attempts   = excluded.attempts,
                last_error = excluded.last_error,
                settled_at = excluded.settled_at
            """,
            (
                uuid_to_text(message.plan_id),
                message.due_at.isoformat(),
                message.recipient,
                message.subject,
                message.body,
                enum_to_text(message.status),
                message.attempts,
                message.last_error,
                datetime_to_text(message.created_at),
                datetime_to_text(message.settled_at),
            ),
        )
        return message

    def _row_to_message(self, row) -> OutboundMessage:
        # text_to_datetime rather than a bare fromisoformat, so the reading rule
        # lives with every other one.
        return OutboundMessage(
            plan_id=text_to_uuid(row["plan_id"]),
            due_at=text_to_datetime(row["due_at"]),
            recipient=row["recipient"],
            subject=row["subject"],
            body=row["body"],
            status=text_to_enum(DeliveryStatus, row["status"]),
            attempts=row["attempts"],
            last_error=row["last_error"],
            created_at=text_to_datetime(row["created_at"]),
            settled_at=text_to_datetime(row["settled_at"]),
        )
