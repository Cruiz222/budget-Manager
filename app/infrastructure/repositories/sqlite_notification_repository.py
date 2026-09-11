import sqlite3

from app.domain.notifications.deliveryStatus import DeliveryStatus
from app.domain.notifications.notification import Notification
from app.domain.notifications.notificationKind import NotificationKind
from app.domain.repositories.notification_repository import NotificationRepository
from app.infrastructure.persistence.serialization import (
    datetime_to_text,
    enum_to_text,
    text_to_datetime,
    text_to_enum,
    text_to_uuid,
    uuid_to_text,
)


class SqliteNotificationRepository(NotificationRepository):
    """Notification store over a single SQLite connection.

    Keyed on ``event_key`` - a value derived from the event the notification is
    about, not a generated id - so the database, not a check in Python, is what
    stops an event being announced twice.

    The three methods are the three of ``SqliteOutboundMessageRepository`` with a
    different key, and the *conflict clauses* are copied from it exactly, for the
    same reasons: ``enqueue`` claims, ``save`` progresses.
    """

    #: The columns, in one place, so the three statements below cannot drift.
    _COLUMNS = (
        "event_key, kind, subject_id, recipient, subject, body, "
        "status, attempts, last_error, created_at, settled_at"
    )

    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection
        self._connection.row_factory = sqlite3.Row

    def enqueue(self, notification: Notification) -> bool:
        """Insert the notification, and report whether this call is the one that did.

        ``DO NOTHING`` rather than ``DO UPDATE``, unlike ``save`` below, and the
        difference is the point: this is a *claim*, not a write. The words were
        composed once, at the moment the event happened, and are frozen there. A
        second enqueue of the same event is not a correction - it is the same
        event reaching here twice, which the idempotent path through
        ``WalletOperation`` does quite legitimately - and it must change nothing.

        The row count is the return value for the same reason it is in the notice
        and outbox stores: SQLite reports zero rows changed when the conflict
        fires, so the write itself answers "was this mine to queue?".
        """
        cursor = self._connection.execute(
            """
            INSERT INTO notifications (
                event_key, kind, subject_id, recipient, subject, body,
                status, attempts, last_error, created_at, settled_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_key) DO NOTHING
            """,
            self._values(notification),
        )
        return cursor.rowcount == 1

    def pending(self) -> list[Notification]:
        """Every notification still owed, oldest first.

        ``ORDER BY created_at``, unlike the outbox's ``ORDER BY due_at``, and the
        difference is not stylistic. A warning has an upcoming moment to be
        urgent about; a notification is about something that already happened, so
        the only fair order is the order the events occurred in.
        """
        rows = self._connection.execute(
            f"""
            SELECT {self._COLUMNS}
            FROM notifications
            WHERE status = ?
            ORDER BY created_at
            """,
            (enum_to_text(DeliveryStatus.PENDING),),
        ).fetchall()
        return [self._row_to_notification(row) for row in rows]

    def save(self, notification: Notification) -> Notification:
        """Write the notification's current state, overwriting whatever was there.

        The opposite conflict clause to ``enqueue``, and both are correct. A
        notification that has just been sent, or has just failed its third
        attempt, really does have a truer second version - and if this did not
        overwrite, every drain would find it still PENDING and send it again,
        forever.

        Only lifecycle columns are updated. The facts - what happened, to whom,
        and the words - are never rewritten, which is the row's promise that a
        queued message says what it said when it was queued.
        """
        self._connection.execute(
            """
            INSERT INTO notifications (
                event_key, kind, subject_id, recipient, subject, body,
                status, attempts, last_error, created_at, settled_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_key) DO UPDATE SET
                status     = excluded.status,
                attempts   = excluded.attempts,
                last_error = excluded.last_error,
                settled_at = excluded.settled_at
            """,
            self._values(notification),
        )
        return notification

    @staticmethod
    def _values(notification: Notification) -> tuple:
        """The column values, in the order the two statements above expect.

        Shared so that ``enqueue`` and ``save`` cannot disagree about which
        column holds which field - a mistake that would be invisible until a
        round trip, and would then look like a serialization bug.
        """
        return (
            notification.event_key,
            enum_to_text(notification.kind),
            uuid_to_text(notification.subject_id),
            notification.recipient,
            notification.subject,
            notification.body,
            enum_to_text(notification.status),
            notification.attempts,
            notification.last_error,
            datetime_to_text(notification.created_at),
            datetime_to_text(notification.settled_at),
        )

    def _row_to_notification(self, row) -> Notification:
        # text_to_datetime rather than a bare fromisoformat, so the reading rule
        # lives with every other one. ``settled_at`` is NULL while the message is
        # owed, and text_to_datetime carries that through as None.
        return Notification(
            event_key=row["event_key"],
            kind=text_to_enum(NotificationKind, row["kind"]),
            subject_id=text_to_uuid(row["subject_id"]),
            recipient=row["recipient"],
            subject=row["subject"],
            body=row["body"],
            status=text_to_enum(DeliveryStatus, row["status"]),
            attempts=row["attempts"],
            last_error=row["last_error"],
            created_at=text_to_datetime(row["created_at"]),
            settled_at=text_to_datetime(row["settled_at"]),
        )
