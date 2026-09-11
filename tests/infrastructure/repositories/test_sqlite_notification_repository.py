"""The receipt store: a derived key that claims, and a save that progresses.

Structured like ``test_sqlite_outbound_message_repository.py`` and deliberately
so - the two repositories are the same three methods over the same three rules,
and reading them side by side is how a drift between them would show up. The one
structural difference is that this table has no foreign key, and there is a test
below that says why.
"""

from datetime import datetime
from uuid import uuid4

import pytest

from app.domain.notifications.deliveryStatus import DeliveryStatus
from app.domain.notifications.notification import Notification
from app.domain.notifications.notificationKind import NotificationKind
from app.infrastructure.persistence.serialization import text_to_enum
from app.infrastructure.persistence.sqlite_unit_of_work import open_sqlite_connection
from app.infrastructure.repositories.sqlite_notification_repository import (
    SqliteNotificationRepository,
)

NOON = datetime(2026, 3, 2, 12, 0)
TEN_PAST_NOON = datetime(2026, 3, 2, 12, 10)
FIVE_PAST_NOON = datetime(2026, 3, 2, 12, 5)


def build_repository():
    """A receipt store on a fresh in-memory database.

    No seeding, unlike the outbox: this table has no foreign key and no parents
    to write first - see ``test_a_notification_needs_no_parents``.
    """
    return SqliteNotificationRepository(open_sqlite_connection(":memory:"))


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


def test_round_trips_a_notification():
    repository = build_repository()
    plan_id = uuid4()

    repository.enqueue(build_notification(plan_id))

    stored = repository.pending()[0]
    assert stored.event_key == f"payout_succeeded:plan:{plan_id}:2026-03-02T12:00:00"
    assert stored.kind is NotificationKind.PAYOUT_SUCCEEDED
    assert stored.subject_id == plan_id
    assert stored.recipient == "chinedu@example.com"
    assert stored.subject == "Plan 'salary': 20000.00 NGN moved"
    assert stored.body == "The plan 'salary' ran at 2026-03-02T12:00."
    assert stored.created_at == NOON
    assert stored.status is DeliveryStatus.PENDING
    assert stored.attempts == 0
    assert stored.last_error is None
    assert stored.settled_at is None


def test_an_empty_queue_owes_nothing():
    assert build_repository().pending() == []


def test_a_notification_needs_no_parents():
    """No foreign key, unlike the outbox, and that is the design rather than a gap.

    ``outbound_messages.plan_id`` references ``savings_plans`` because a warning
    is *about* a plan and a warning about nothing cannot exist. A receipt's
    subject is a plan id or a wallet id, depending on the kind, so there is no
    single table to point at - and more to the point there is nothing to protect:
    the row records something that already happened and is committed in the same
    transaction as the ledger row it describes. By the time it exists, its subject
    certainly does.
    """
    repository = build_repository()

    assert repository.enqueue(build_notification()) is True
    assert len(repository.pending()) == 1


class TestEnqueueing:
    """The write answers the question, so that nothing has to be read first."""

    def test_the_first_enqueue_wins(self):
        repository = build_repository()

        assert repository.enqueue(build_notification()) is True

    def test_a_second_enqueue_of_the_same_event_loses(self):
        repository = build_repository()
        notification = build_notification()

        repository.enqueue(notification)
        second = repository.enqueue(build_notification(notification.subject_id))

        assert second is False

    def test_a_losing_enqueue_writes_no_second_row(self):
        repository = build_repository()
        notification = build_notification()

        for _ in range(3):
            repository.enqueue(build_notification(notification.subject_id))

        assert len(repository.pending()) == 1

    def test_a_losing_enqueue_does_not_rewrite_the_words(self):
        """``DO NOTHING``, not ``DO UPDATE`` - the whole point of a claim.

        The words were composed once, at the moment the event happened, and are
        frozen there. A second enqueue is the same event reaching here twice -
        which the idempotent path through ``WalletOperation`` does quite
        legitimately - and it must change nothing, certainly not the text of a
        receipt for a payment that has already been reported.
        """
        repository = build_repository()
        notification = build_notification()

        repository.enqueue(build_notification(notification.subject_id, subject="first"))
        repository.enqueue(build_notification(notification.subject_id, subject="second"))

        assert repository.pending()[0].subject == "first"

    def test_two_events_about_one_subject_are_two_rows(self):
        """A plan that pays in March and pays in April is two things that happened."""
        repository = build_repository()
        plan_id = uuid4()
        subject_id = plan_id

        repository.enqueue(
            build_notification(
                subject_id,
                event_key=f"payout_succeeded:plan:{plan_id}:2026-03-02T12:00:00",
            )
        )
        repository.enqueue(
            build_notification(
                subject_id,
                event_key=f"payout_succeeded:plan:{plan_id}:2026-04-02T12:00:00",
            )
        )

        assert len(repository.pending()) == 2


class TestSaving:
    """``save`` overwrites, unlike ``enqueue`` - and that contrast is the design.

    If ``save`` did not overwrite, every drain would find the message still
    PENDING and send it again, forever. The notice store makes the opposite
    choice for the opposite reason: a notice has no truer second version.
    """

    def test_saving_a_sent_notification_settles_the_row(self):
        repository = build_repository()
        repository.enqueue(build_notification())

        notification = repository.pending()[0]
        notification.mark_sent(TEN_PAST_NOON)
        repository.save(notification)

        assert repository.pending() == []

    def test_the_settled_row_can_still_be_read_for_its_moment(self):
        """Settled is not deleted. The row is the record of what was sent."""
        repository = build_repository()
        repository.enqueue(build_notification())
        notification = repository.pending()[0]
        notification.mark_sent(TEN_PAST_NOON)
        repository.save(notification)

        row = repository._connection.execute(
            "SELECT status, settled_at FROM notifications WHERE event_key = ?",
            (notification.event_key,),
        ).fetchone()

        assert row["status"] == "SENT"
        assert row["settled_at"] == TEN_PAST_NOON.isoformat()

    def test_a_failed_attempt_is_written_and_the_message_stays_owed(self):
        repository = build_repository()
        repository.enqueue(build_notification())

        notification = repository.pending()[0]
        notification.record_failure("OSError: connection refused")
        repository.save(notification)

        stored = repository.pending()[0]
        assert stored.attempts == 1
        assert stored.last_error == "OSError: connection refused"

    def test_saving_does_not_duplicate_the_row(self):
        repository = build_repository()
        repository.enqueue(build_notification())

        notification = repository.pending()[0]
        for _ in range(3):
            notification.record_failure("still refused")
            repository.save(notification)

        assert len(repository.pending()) == 1
        assert repository.pending()[0].attempts == 3

    def test_saving_does_not_rewrite_the_facts(self):
        """Only the lifecycle columns move.

        A receipt is a record of what we owed and to whom. Letting a save rewrite
        the words would mean a message could be altered after it was delivered -
        or worse, before it was, by a later code path that had no business
        touching a composition it did not write.
        """
        repository = build_repository()
        repository.enqueue(build_notification())
        original = repository.pending()[0]

        edited = build_notification(original.subject_id, subject="reworded")
        edited.mark_sent(TEN_PAST_NOON)
        repository.save(edited)

        row = repository._connection.execute(
            "SELECT subject, recipient, created_at FROM notifications "
            "WHERE event_key = ?",
            (original.event_key,),
        ).fetchone()

        assert row["subject"] == original.subject
        assert row["recipient"] == original.recipient
        assert row["created_at"] == original.created_at.isoformat()

    def test_an_expired_row_is_settled_too(self):
        """Nothing expires a notification yet - see the aggregate - but the store
        has to round-trip the state, or the first kind with a shelf life would
        fail here rather than at its own rule."""
        repository = build_repository()
        repository.enqueue(build_notification())

        notification = repository.pending()[0]
        notification.mark_expired(TEN_PAST_NOON)
        repository.save(notification)

        assert repository.pending() == []


class TestWhatIsOwed:
    def test_pending_excludes_every_settled_status(self):
        repository = build_repository()
        sent = build_notification()
        expired = build_notification()
        owed = build_notification()
        repository.enqueue(sent)
        repository.enqueue(expired)
        repository.enqueue(owed)
        sent.mark_sent(NOON)
        expired.mark_expired(NOON)
        repository.save(sent)
        repository.save(expired)

        assert [one.event_key for one in repository.pending()] == [owed.event_key]

    def test_pending_is_ordered_by_when_the_event_happened(self):
        """Unlike the outbox, which sorts by the occurrence it is announcing.

        A warning has an upcoming moment to be urgent about, so the soonest goes
        first. A receipt is about something that already happened - there is no
        upcoming moment - so the fair order is the order the events occurred in,
        and ``created_at`` carries that.
        """
        repository = build_repository()
        for created_at in (TEN_PAST_NOON, NOON, FIVE_PAST_NOON):
            repository.enqueue(
                build_notification(created_at=created_at)
            )

        assert [one.created_at for one in repository.pending()] == [
            NOON,
            FIVE_PAST_NOON,
            TEN_PAST_NOON,
        ]

    def test_a_mixed_plan_and_wallet_queue_is_one_list(self):
        """One table for every kind, so the drain never has to know which is which.

        The order here is by ``created_at`` across both scopes, which is the point:
        a plan's payout and a manual deposit are two things that happened, and the
        reader wants them in the order they happened.
        """
        repository = build_repository()
        wallet_id = uuid4()
        plan_id = uuid4()
        repository.enqueue(
            build_notification(
                wallet_id,
                event_key=f"wallet_deposit:wallet:{wallet_id}:ref-1",
                kind=NotificationKind.WALLET_DEPOSIT,
                created_at=FIVE_PAST_NOON,
            )
        )
        repository.enqueue(
            build_notification(plan_id, created_at=TEN_PAST_NOON)
        )

        assert [
            (one.kind, one.created_at) for one in repository.pending()
        ] == [
            (NotificationKind.WALLET_DEPOSIT, FIVE_PAST_NOON),
            (NotificationKind.PAYOUT_SUCCEEDED, TEN_PAST_NOON),
        ]


def test_an_unknown_stored_status_is_a_loud_failure():
    """``text_to_enum`` raises rather than guessing.

    Worth pinning because the rename in this phase is exactly the kind of change
    that would break it: ``enum_to_text`` stores a member's *name*, so renaming a
    member is a data migration even when the class name changes. See decision 6.

    The repository's own reads cannot hit this: ``pending()`` selects only
    ``status = 'PENDING'`` rows, so a bad status is filtered out before it is
    ever hydrated. The property therefore lives at the serialization boundary,
    and this is where it is asserted - the drain's caller is the only one who
    can bring an unrepresentable status within reach of hydration.
    """
    with pytest.raises(KeyError):
        text_to_enum(DeliveryStatus, "CANCELLED")
