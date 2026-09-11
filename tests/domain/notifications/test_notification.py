"""The receipt aggregate: what it must hold, and what it refuses to be built as.

The mirror of ``test_outbound_message.py``, and the two files are meant to be read
side by side. Almost every case below has a twin there - the difference is not in
the *checks*, which are deliberately the same checks, but in the fields they are
applied to. A warning needs a plan id and an occurrence; a receipt needs neither,
and needs an event key instead.
"""

from datetime import date, datetime
from uuid import UUID, uuid4

import pytest

from app.domain.notifications.deliveryStatus import DeliveryStatus
from app.domain.notifications.exception import (
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
from app.domain.notifications.notification import Notification
from app.domain.notifications.notificationKind import NotificationKind

NOON = datetime(2026, 3, 2, 12, 0)
TEN_PAST_NOON = datetime(2026, 3, 2, 12, 10)


def build_notification(**overrides):
    """A well-formed receipt, with every dial a test could turn.

    The defaults describe the headline case - a plan that just paid out - and the
    ``event_key`` is a real one rather than a placeholder, because the key is the
    row's identity and a fake would hide a change to its shape.
    """
    plan_id = overrides.pop("subject_id", None) or uuid4()
    kwargs = dict(
        event_key=f"payout_succeeded:plan:{plan_id}:2026-03-02T12:00:00",
        kind=NotificationKind.PAYOUT_SUCCEEDED,
        subject_id=plan_id,
        recipient="chinedu@example.com",
        subject="Plan 'salary': 20000.00 NGN moved",
        body="The plan 'salary' ran at 2026-03-02T12:00.",
        created_at=NOON,
    )
    kwargs.update(overrides)
    return Notification(**kwargs)


class TestWhatAReceiptHolds:
    def test_a_new_notification_is_pending_and_untried(self):
        notification = build_notification()

        assert notification.status is DeliveryStatus.PENDING
        assert notification.attempts == 0
        assert notification.last_error is None
        assert notification.settled_at is None
        assert notification.is_settled is False

    def test_it_holds_the_words_it_will_send(self):
        notification = build_notification(recipient="a@b.c", subject="s", body="b")

        assert notification.recipient == "a@b.c"
        assert notification.subject == "s"
        assert notification.body == "b"

    def test_it_holds_no_plan_and_no_occurrence(self):
        """The concrete reason ``OutboundMessage`` could not be reused.

        A deposit has no plan id anywhere in it. A warning *requires* one and
        validates it, so the two cannot be the same record - and the subject here
        is a wallet id as often as a plan id, which is why the field is a
        ``UUID`` and not two nullable ones.
        """
        wallet_id = uuid4()
        notification = build_notification(subject_id=wallet_id)

        assert notification.subject_id == wallet_id
        assert not hasattr(notification, "plan_id")
        assert not hasattr(notification, "due_at")

    def test_the_event_key_is_its_identity(self):
        """Not the subject, and not the moment it was composed.

        A receipt is identified by the *event* it describes, which is what makes a
        second enqueue of the same event a no-op rather than a second email.
        """
        key = "wallet_deposit:wallet:00000000-0000-0000-0000-000000000000:ref-1"
        notification = build_notification(event_key=key)

        assert notification.event_key == key

    def test_str_reads_as_the_event_being_reported(self):
        notification = build_notification(
            subject_id=UUID(int=0), kind=NotificationKind.WALLET_DEPOSIT
        )

        assert str(notification) == (
            f"wallet_deposit for {UUID(int=0)} at 2026-03-02T12:00 "
            f"to chinedu@example.com: pending"
        )


class TestWhatCannotBeBuilt:
    def test_an_empty_event_key_is_rejected(self):
        """Empty keys would all collide, so the second event would vanish.

        The key is the primary key of the row, which makes this worse than a
        cosmetic check: two notifications sharing the empty string means the
        second one is silently never queued, and nothing would report it.
        """
        with pytest.raises(InvalidNotificationEventKeyError):
            build_notification(event_key="   ")

    def test_a_non_string_event_key_is_rejected(self):
        with pytest.raises(InvalidNotificationEventKeyError):
            build_notification(event_key=None)

    def test_a_non_kind_is_rejected(self):
        with pytest.raises(InvalidNotificationKindError):
            build_notification(kind="payout_succeeded")

    def test_a_non_uuid_subject_is_rejected(self):
        with pytest.raises(InvalidNotificationSubjectIDError):
            build_notification(subject_id="not-a-uuid")

    def test_a_non_datetime_created_at_is_rejected(self):
        with pytest.raises(InvalidNotificationCreatedAtError):
            build_notification(created_at="2026-03-02T12:00:00")

    def test_a_bare_date_created_at_is_rejected(self):
        """The subclass trap, in the fourth class to have to name it.

        ``datetime`` passes ``isinstance(x, date)``, so a check written against
        the wider type would accept either. The narrower type is required by name
        here, and a plain date fails for that reason rather than by a special
        case - which matters because a receipt stamped with a bare date has lost
        the moment the money moved.
        """
        assert isinstance(datetime(2026, 3, 2, 12, 0), date)  # the trap, still true

        with pytest.raises(InvalidNotificationCreatedAtError):
            build_notification(created_at=date(2026, 3, 2))

    @pytest.mark.parametrize("field", ["recipient", "subject", "body"])
    def test_an_empty_text_field_is_rejected(self, field):
        """Empty is rejected, and so is whitespace-only.

        A message with no recipient fails at *send* time, which is the worst
        possible moment for a receipt: the row is already committed alongside the
        money that moved, so nothing is left to raise it again. Refusing it at
        construction moves the failure back to the transaction that could still
        roll back.
        """
        with pytest.raises(
            {
                "recipient": InvalidNotificationRecipientError,
                "subject": InvalidNotificationSubjectError,
                "body": InvalidNotificationBodyError,
            }[field]
        ):
            build_notification(**{field: "   "})

    def test_a_non_string_recipient_is_rejected(self):
        with pytest.raises(InvalidNotificationRecipientError):
            build_notification(recipient=None)

    def test_an_unknown_status_is_rejected(self):
        with pytest.raises(InvalidNotificationStatusError):
            build_notification(status="sent")

    def test_a_boolean_attempt_count_is_rejected(self):
        """``bool`` subclasses ``int``, so this has to be checked by name.

        Without the explicit clause, ``attempts=True`` would be a perfectly
        legitimate one-attempt count and nothing would ever complain.
        """
        assert isinstance(True, int)  # the trap, still true

        with pytest.raises(InvalidNotificationAttemptsError):
            build_notification(attempts=True)

    def test_a_negative_attempt_count_is_rejected(self):
        with pytest.raises(InvalidNotificationAttemptsError):
            build_notification(attempts=-1)

    def test_a_non_string_last_error_is_rejected(self):
        with pytest.raises(InvalidNotificationLastErrorError):
            build_notification(last_error=OSError("refused"))

    def test_a_non_datetime_settled_at_is_rejected(self):
        with pytest.raises(InvalidNotificationSettledAtError):
            build_notification(settled_at="2026-03-02T12:00:00")


class TestSettledAndUnsettledMustAgree:
    """The same shape of rule as a blocked run needing a reason.

    A status that claims the message is finished, and a moment that says when -
    or their absence - have to tell the same story. A half-written pair is a row
    that cannot answer the question it exists to answer.
    """

    def test_a_sent_notification_with_no_moment_is_rejected(self):
        with pytest.raises(InvalidNotificationSettledAtError):
            build_notification(status=DeliveryStatus.SENT, settled_at=None)

    def test_an_expired_notification_with_no_moment_is_rejected(self):
        with pytest.raises(InvalidNotificationSettledAtError):
            build_notification(status=DeliveryStatus.EXPIRED, settled_at=None)

    def test_a_pending_notification_carrying_a_settled_moment_is_rejected(self):
        with pytest.raises(InvalidNotificationSettledAtError):
            build_notification(status=DeliveryStatus.PENDING, settled_at=NOON)

    def test_a_sent_notification_with_its_moment_is_accepted(self):
        notification = build_notification(status=DeliveryStatus.SENT, settled_at=NOON)

        assert notification.is_settled is True


class TestItsLifecycle:
    def test_marking_it_sent_records_the_moment(self):
        notification = build_notification()

        notification.mark_sent(TEN_PAST_NOON)

        assert notification.status is DeliveryStatus.SENT
        assert notification.settled_at == TEN_PAST_NOON
        assert notification.is_settled is True

    def test_marking_it_expired_records_the_moment_and_no_error(self):
        """Expiry is a decision, not a failure, so ``last_error`` stays empty.

        Nothing currently expires a notification - see the aggregate - and this
        test is the reason the method is still here: it is the lifecycle that
        would break silently the day a kind with a shelf life arrives.
        """
        notification = build_notification()

        notification.mark_expired(TEN_PAST_NOON)

        assert notification.status is DeliveryStatus.EXPIRED
        assert notification.settled_at == TEN_PAST_NOON
        assert notification.last_error is None

    def test_a_failure_leaves_it_pending_and_counted(self):
        """The whole reason a receipt can be retried forever.

        A delivery attempt that dies does not settle the message - it stays owed,
        and the next drain tries again. Making failure terminal would mean a mail
        server that was down for five minutes loses the receipt for money that
        really moved, which is the one message that must not be lost.
        """
        notification = build_notification()

        notification.record_failure("OSError: connection refused")

        assert notification.status is DeliveryStatus.PENDING
        assert notification.attempts == 1
        assert notification.last_error == "OSError: connection refused"
        assert notification.settled_at is None

    def test_failing_twice_counts_twice_and_keeps_the_last_error(self):
        notification = build_notification()

        notification.record_failure("first")
        notification.record_failure("second")

        assert notification.attempts == 2
        assert notification.last_error == "second"

    def test_a_settled_notification_cannot_be_settled_again(self):
        """History does not get edited.

        A second ``mark_sent`` means the caller has lost track of which messages
        it is holding, and letting it through would overwrite the moment the
        message really went.
        """
        notification = build_notification()
        notification.mark_sent(TEN_PAST_NOON)

        with pytest.raises(MessageAlreadySettledError):
            notification.mark_sent(datetime(2026, 3, 2, 12, 20))

    def test_a_sent_notification_cannot_then_expire(self):
        """The pairing that would confuse the two queues if it were allowed.

        A receipt delivered and then marked expired would say the message both
        went and was dropped, which is exactly the conflation the warning and
        receipt tables exist to prevent.
        """
        notification = build_notification()
        notification.mark_sent(TEN_PAST_NOON)

        with pytest.raises(MessageAlreadySettledError):
            notification.mark_expired(datetime(2026, 3, 2, 12, 20))

    def test_a_failed_notification_can_still_expire(self):
        """Failure is not terminal - ``record_failure`` leaves it pending.

        The twin of ``test_a_retry_after_a_failure_can_still_succeed``, and the
        mirror of the warning's own lifecycle: an expired message cannot then
        fail (see ``test_an_expired_message_cannot_then_fail`` in the warning's
        file), but a failed message is still owed and can be settled by a later
        pass either way. Nothing expires a notification yet, but the day a kind
        with a shelf life arrives, the message that failed its first attempts is
        exactly the one that will need this.
        """
        notification = build_notification()
        notification.record_failure("connection refused")

        notification.mark_expired(TEN_PAST_NOON)

        assert notification.status is DeliveryStatus.EXPIRED
        assert notification.settled_at == TEN_PAST_NOON
        assert notification.attempts == 1
        assert notification.last_error == "connection refused"

    def test_a_retry_after_a_failure_can_still_succeed(self):
        """The path the retry design exists for."""
        notification = build_notification()
        notification.record_failure("connection refused")

        notification.mark_sent(TEN_PAST_NOON)

        assert notification.status is DeliveryStatus.SENT
        assert notification.attempts == 1
        assert notification.last_error == "connection refused"


class TestWhenItStopsBeingWorthSending:
    def test_never_it_is_always_worth_sending(self):
        """Decision 30, in the one place it is now executed.

        A receipt reports something that already happened, and that never stops
        being true. There is no ``as_of`` that makes "your payout went out" a
        message better dropped than delivered, so the answer is a flat ``False``
        at every moment - here at the event, and ten minutes later.
        """
        notification = build_notification(created_at=NOON)

        assert notification.is_stale(NOON) is False
        assert notification.is_stale(TEN_PAST_NOON) is False

    def test_a_century_later_it_is_still_worth_sending(self):
        """No horizon, not merely a distant one.

        Worth pinning precisely because it looks absurd: there is no large
        ``as_of`` at which this flips. A rule that held "for long enough" would
        be a deadline wearing a disguise, and the receipt would be dropped by
        whichever drain ran after it.
        """
        notification = build_notification(created_at=NOON)

        assert notification.is_stale(datetime(2126, 3, 2, 12, 0)) is False

    def test_a_wedged_retry_never_becomes_stale(self):
        """The backlog consequence, pinned as behaviour rather than as prose.

        A receipt that has failed a hundred times is still owed, and still true.
        If accumulated failures could make it stale, the one receipt that
        matters would be the one dropped - which is exactly what this drain
        exists to prevent. The cost is real and accepted: the retry has no cap.
        """
        notification = build_notification()
        for _ in range(100):
            notification.record_failure("connection refused")

        assert notification.is_stale(TEN_PAST_NOON) is False
