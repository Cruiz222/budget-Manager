"""The receipt drain, and the rule it is defined by *not* having.

The mirror of ``test_deliver_pending_messages.py``, and the class that file ends
with - "a stale warning is never sent" - has no counterpart here on purpose. There
is nothing to be stale. A receipt says something that happened, and saying it late
is still true, so this drain has no expiry branch at all and the tests below that
matter most are the ones asserting that a *late* drain still sends.
"""

import sqlite3
from datetime import datetime
from uuid import uuid4

import pytest

from app.application.notifications.deliver_notifications import DeliverNotifications
from app.application.notifications.deliver_pending_messages import DeliveryReport
from app.domain.notifications.notification import Notification
from app.domain.notifications.notificationKind import NotificationKind
from app.infrastructure.persistence.sqlite_unit_of_work import SqliteUnitOfWorkFactory

NOON = datetime(2026, 3, 2, 12, 0)
TEN_PAST_NOON = datetime(2026, 3, 2, 12, 10)
NEXT_WEEK = datetime(2026, 3, 9, 12, 0)
FIVE_PAST_NOON = datetime(2026, 3, 2, 12, 5)


def build_deliverer(tmp_path, channel, name="receipts.db"):
    factory = SqliteUnitOfWorkFactory(str(tmp_path / name))
    return DeliverNotifications(factory, channel=channel), factory, str(
        tmp_path / name
    )


def seed(factory, notifications=()):
    """Queue the given receipts in one unit.

    No wallet or plan to write first, unlike the outbox's ``seed`` - this table
    has no foreign key, which is the subject of its own test in the repository
    suite.
    """
    uow = factory.start()
    try:
        for notification in notifications:
            uow.notifications.enqueue(notification)
        uow.commit()
    except BaseException:
        uow.rollback()
        raise


def build_notification(subject_id=None, **overrides):
    subject_id = subject_id if subject_id is not None else uuid4()
    kwargs = dict(
        event_key=f"payout_succeeded:plan:{subject_id}:2026-03-02T12:00:00",
        kind=NotificationKind.PAYOUT_SUCCEEDED,
        subject_id=subject_id,
        recipient="chinedu@example.com",
        subject="Plan 'salary': 20000.00 NGN moved",
        body="The plan 'salary' ran at 2026-03-02T12:00.",
        created_at=NOON,
    )
    kwargs.update(overrides)
    return Notification(**kwargs)


def read_rows(db_path):
    """Every receipt row, straight from the table.

    The diagnostic query worth reaching for when a receipt does not arrive:

        SELECT kind, subject_id, status, attempts, last_error FROM notifications
    """
    connection = sqlite3.connect(db_path)
    try:
        connection.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in connection.execute(
                "SELECT event_key, kind, subject_id, status, attempts, "
                "last_error, settled_at FROM notifications ORDER BY created_at"
            )
        ]
    finally:
        connection.close()


def only_row(db_path):
    rows = read_rows(db_path)
    assert len(rows) == 1
    return rows[0]


class TestSendingWhatIsOwed:
    def test_a_queued_receipt_is_sent_and_settled(self, tmp_path, build_channel):
        channel = build_channel()
        deliverer, factory, db = build_deliverer(tmp_path, channel)
        notification = build_notification()
        seed(factory, [notification])

        report = deliverer.execute(TEN_PAST_NOON)

        assert [one.event_key for one in report.sent] == [notification.event_key]
        assert [one.event_key for one in channel.sent] == [notification.event_key]
        row = only_row(db)
        assert row["status"] == "SENT"
        assert row["settled_at"] == TEN_PAST_NOON.isoformat()

    def test_the_words_sent_are_the_words_that_were_queued(
        self, tmp_path, build_channel
    ):
        """Composition happens at enqueue, so the channel is handed the stored text.

        Nothing is re-rendered on the way out: a wording change in ``compose``
        must not rewrite a receipt for a payment that has already been reported.
        """
        channel = build_channel()
        deliverer, factory, _ = build_deliverer(tmp_path, channel)
        seed(factory, [build_notification(subject="frozen", body="frozen body")])

        deliverer.execute(TEN_PAST_NOON)

        assert channel.sent[0].subject == "frozen"
        assert channel.sent[0].body == "frozen body"
        assert channel.sent[0].recipient == "chinedu@example.com"

    def test_an_empty_queue_is_quiet(self, tmp_path, build_channel):
        channel = build_channel()
        deliverer, factory, _ = build_deliverer(tmp_path, channel)
        seed(factory)

        report = deliverer.execute(TEN_PAST_NOON)

        assert report.is_quiet is True
        assert channel.attempts == []

    def test_a_settled_receipt_is_not_sent_again(self, tmp_path, build_channel):
        """The property that stops a drain repeating itself on every tick."""
        channel = build_channel()
        deliverer, factory, _ = build_deliverer(tmp_path, channel)
        seed(factory, [build_notification()])

        deliverer.execute(TEN_PAST_NOON)
        second = deliverer.execute(FIVE_PAST_NOON)

        assert len(channel.sent) == 1
        assert second.sent == ()

    def test_a_receipt_is_sent_in_the_order_the_events_happened(
        self, tmp_path, build_channel
    ):
        """``created_at``, unlike the outbox's ``due_at``.

        A warning has an upcoming moment to be urgent about; a receipt is about
        something that already happened, so the only fair order is the order the
        events occurred in.
        """
        channel = build_channel()
        deliverer, factory, _ = build_deliverer(tmp_path, channel)
        seed(factory, [build_notification(created_at=TEN_PAST_NOON)])
        seed(factory, [build_notification(created_at=NOON)])

        deliverer.execute(TEN_PAST_NOON)

        assert [one.created_at for one in channel.sent] == [NOON, TEN_PAST_NOON]


class TestAReceiptIsNeverExpired:
    """The whole difference between this class and the warning drain.

    There is no ``if due_at <= as_of`` here, and no ``due_at`` to compare. The
    rule it replaces exists because a warning becomes *false* when its moment
    passes; nothing makes "your payout went out" false. So ``as_of`` decides
    nothing about whether a receipt is worth sending - it is only the moment
    recorded on a successful send.
    """

    def test_a_drain_long_after_the_event_still_sends(self, tmp_path, build_channel):
        """The case the warning drain gets wrong on purpose and this one must not.

        A warning drained a week late is expired rather than sent, because it
        would now be a lie. The same message here is a week-old receipt - late,
        and entirely true, and still the only record the user has that their money
        moved.
        """
        channel = build_channel()
        deliverer, factory, db = build_deliverer(tmp_path, channel)
        seed(factory, [build_notification()])

        report = deliverer.execute(NEXT_WEEK)

        assert len(report.sent) == 1
        assert report.expired == ()
        assert only_row(db)["status"] == "SENT"

    def test_the_report_has_no_expired_row_for_a_receipt(self, tmp_path, build_channel):
        """``expired`` stays a field, and stays empty.

        The field is part of ``DeliveryReport`` because a report is the same four
        things whichever queue it describes. Nothing writes to this one, which is
        the point: a drain that could expire a receipt would be a drain that can
        lose one.
        """
        channel = build_channel()
        deliverer, factory, _ = build_deliverer(tmp_path, channel)
        seed(factory, [build_notification()])

        report = deliverer.execute(NEXT_WEEK)

        assert report.expired == ()
        assert report.sent != ()

    def test_a_receipt_is_not_expired_even_with_no_channel_configured(
        self, tmp_path
    ):
        """Nothing ages out of the receipt queue while email is unconfigured.

        An install that loses its mail account accumulates receipts rather than
        losing them, and they all go out the day a configuration exists again -
        however long that takes.
        """
        deliverer, factory, db = build_deliverer(tmp_path, channel=None)
        seed(factory, [build_notification()])

        report = deliverer.execute(NEXT_WEEK)

        assert report.expired == ()
        assert len(report.deferred) == 1
        assert only_row(db)["status"] == "PENDING"


class TestFailureIsRecordedNotRaised:
    """A mail server being down is a message that goes out next tick, not an incident.

    The property this buys is the one the whole feature rests on: **a broken SMTP
    server can never make a deposit fail, or a tick fail.** The money has already
    moved and the receipt is still owed.
    """

    def test_a_channel_that_raises_does_not_raise_from_execute(
        self, tmp_path, build_channel
    ):
        channel = build_channel(failures=[OSError("connection refused")])
        deliverer, factory, db = build_deliverer(tmp_path, channel)
        seed(factory, [build_notification()])

        report = deliverer.execute(TEN_PAST_NOON)  # must not raise

        assert len(report.failed) == 1
        row = only_row(db)
        assert row["status"] == "PENDING"
        assert row["attempts"] == 1
        assert "connection refused" in row["last_error"]
        assert row["settled_at"] is None

    def test_the_error_names_the_exception_type(self, tmp_path, build_channel):
        """So the log line says what actually went wrong, not just that something did."""
        channel = build_channel(failures=[OSError("connection refused")])
        deliverer, factory, db = build_deliverer(tmp_path, channel)
        seed(factory, [build_notification()])

        deliverer.execute(TEN_PAST_NOON)

        assert only_row(db)["last_error"] == "OSError: connection refused"

    def test_a_later_drain_retries_and_succeeds(self, tmp_path, build_channel):
        """The retry the design exists for.

        Unlike the warning queue there is no natural cap on these attempts, and
        that is right: the message is still true and still owed, and dropping it
        after N tries would lose the one record that matters.
        """
        channel = build_channel(failures=[OSError("connection refused")])
        deliverer, factory, db = build_deliverer(tmp_path, channel)
        seed(factory, [build_notification()])

        first = deliverer.execute(TEN_PAST_NOON)
        second = deliverer.execute(NEXT_WEEK)

        assert len(first.failed) == 1
        assert len(second.sent) == 1
        row = only_row(db)
        assert row["status"] == "SENT"
        assert row["attempts"] == 1  # the count survives the success

    def test_the_stored_error_survives_a_later_success(self, tmp_path, build_channel):
        """A record of the trouble, kept after it stopped."""
        channel = build_channel(failures=[OSError("connection refused")])
        deliverer, factory, db = build_deliverer(tmp_path, channel)
        seed(factory, [build_notification()])

        deliverer.execute(TEN_PAST_NOON)
        deliverer.execute(NEXT_WEEK)

        assert only_row(db)["last_error"] == "OSError: connection refused"

    def test_one_bad_receipt_does_not_stop_the_next(self, tmp_path, build_channel):
        """The transaction boundary follows the aggregate, not the batch.

        The warning drain's equivalent, and it matters more here: the message that
        would be abandoned is the receipt for money that has already moved.
        """
        channel = build_channel(failures=[OSError("first one refused")])
        deliverer, factory, db = build_deliverer(tmp_path, channel)
        seed(factory, [build_notification(created_at=NOON)])
        seed(factory, [build_notification(created_at=FIVE_PAST_NOON)])

        report = deliverer.execute(TEN_PAST_NOON)

        assert len(report.failed) == 1
        assert len(report.sent) == 1
        statuses = [row["status"] for row in read_rows(db)]
        assert statuses == ["PENDING", "SENT"]

    def test_a_database_error_is_not_recorded_as_a_delivery_failure(
        self, tmp_path, build_channel
    ):
        """The narrow ``except`` around the send alone, which is load-bearing.

        A failure of the *write* must not be caught by the clause that handles a
        failure of the *send*. If it were, a database error would be written to
        ``last_error`` as though the mail server had refused - and the receipt
        would then be retried forever against a mail server that was working
        perfectly. So the error propagates, which is what this asserts.
        """
        channel = build_channel()
        deliverer, factory, _ = build_deliverer(tmp_path, channel)
        seed(factory, [build_notification()])
        deliverer._unit_of_work_factory = _UncommittableFactory(factory)

        with pytest.raises(sqlite3.OperationalError):
            deliverer.execute(TEN_PAST_NOON)

        assert channel.sent != []  # the send itself did happen


class _UncommittableFactory:
    """A factory whose units read fine and cannot be committed.

    Used for one thing: to make the *write* fail after a successful send, which is
    otherwise unreachable from a test - the send is the only step that can fail on
    its own, and a stub that fails both would test neither.
    """

    def __init__(self, inner):
        self._inner = inner
        self._writes = 0

    def start(self):
        uow = self._inner.start()
        # The first unit is the read; every one after it is a write.
        self._writes += 1
        if self._writes > 1:
            uow.commit = _uncommittable
        return uow


def _uncommittable():
    raise sqlite3.OperationalError("database is locked")


class TestWithNoChannelConfigured:
    """An install with no mail account is normal, not broken.

    ``None`` is not a channel that fails - it is the *absence* of one, and the
    use case can see the difference. That is why "there is nowhere to send this"
    is a state the caller can report rather than a connection error it has to
    interpret.
    """

    def test_nothing_is_sent_and_nothing_is_lost(self, tmp_path):
        deliverer, factory, db = build_deliverer(tmp_path, channel=None)
        seed(factory, [build_notification()])

        report = deliverer.execute(TEN_PAST_NOON)

        assert report.sent == ()
        assert report.failed == ()  # not a failure: nothing was attempted
        assert len(report.deferred) == 1
        row = only_row(db)
        assert row["status"] == "PENDING"
        assert row["attempts"] == 0

    def test_it_is_not_reported_as_an_error(self, tmp_path):
        """Deferred is not failed, and the distinction is what keeps the log honest."""
        deliverer, factory, _ = build_deliverer(tmp_path, channel=None)
        seed(factory, [build_notification()])

        report = deliverer.execute(TEN_PAST_NOON)

        assert report.failed == ()
        assert report.is_quiet is False  # there *is* something to say

    def test_a_receipt_queued_before_the_channel_vanished_is_still_owed(
        self, tmp_path, build_channel
    ):
        """Configured once, then unconfigured: the receipt waits, it does not vanish."""
        channel = build_channel(failures=[OSError("mail server down")])
        deliverer, factory, db = build_deliverer(tmp_path, channel)
        seed(factory, [build_notification()])
        deliverer.execute(TEN_PAST_NOON)

        # The same database, now with no channel at all.
        unconfigured = DeliverNotifications(factory, channel=None)
        report = unconfigured.execute(NEXT_WEEK)

        assert len(report.deferred) == 1
        assert only_row(db)["status"] == "PENDING"


class TestTheReport:
    def test_a_quiet_report_is_the_empty_one(self):
        assert DeliveryReport().is_quiet is True

    def test_a_report_with_an_outcome_is_not_quiet(self):
        notification = build_notification()

        assert DeliveryReport(sent=(notification,)).is_quiet is False
        assert DeliveryReport(failed=(notification,)).is_quiet is False
        assert DeliveryReport(deferred=(notification,)).is_quiet is False

    def test_it_is_the_same_class_the_warning_drain_returns(self):
        """``DeliveryReport`` is generic, not duplicated.

        One class for "what a delivery pass did", parameterised by what it
        delivered - so the reporting rule (say which, not how many; stay silent
        when there is nothing) is written once. Two identical dataclasses kept in
        step by hand would be the alternative, for no benefit, and the pair would
        drift in exactly the way that is hardest to notice.
        """
        from app.application.notifications.deliver_pending_messages import (
            DeliveryReport as WarningReport,
        )

        assert WarningReport is DeliveryReport
