from datetime import date, datetime
from uuid import UUID, uuid4

import pytest

from app.domain.notifications.exception import (
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
from app.domain.notifications.outboundMessage import OutboundMessage
from app.domain.notifications.deliveryStatus import DeliveryStatus

NOON = datetime(2026, 3, 2, 12, 0)
HALF_PAST_ELEVEN = datetime(2026, 3, 2, 11, 30)


def build_message(**overrides):
    kwargs = dict(
        plan_id=uuid4(),
        due_at=NOON,
        recipient="chinedu@example.com",
        subject="Payout of 2000.00 NGN in 30 minutes",
        body="The plan 'salary' pays 2000.00 NGN at 2026-03-02T12:00.",
        created_at=HALF_PAST_ELEVEN,
    )
    kwargs.update(overrides)
    return OutboundMessage(**kwargs)


class TestWhatAMessageHolds:
    def test_a_new_message_is_pending_and_untried(self):
        message = build_message()

        assert message.status is DeliveryStatus.PENDING
        assert message.attempts == 0
        assert message.last_error is None
        assert message.settled_at is None
        assert message.is_settled is False

    def test_it_holds_the_words_it_will_send(self):
        message = build_message(recipient="a@b.c", subject="s", body="b")

        assert message.recipient == "a@b.c"
        assert message.subject == "s"
        assert message.body == "b"

    def test_the_natural_key_is_plan_and_occurrence(self):
        """Not the subject, and not the moment it was composed.

        A message is identified by what it is *about* - this plan, this
        occurrence - which is what makes a second enqueue of the same warning a
        no-op rather than a second email.
        """
        plan_id = uuid4()
        message = build_message(plan_id=plan_id, due_at=NOON)

        assert message.key == (plan_id, NOON)

    def test_str_reads_as_the_occurrence_being_announced(self):
        message = build_message(plan_id=UUID(int=0), due_at=NOON)

        assert str(message) == (
            f"message to chinedu@example.com about {UUID(int=0)} "
            f"due 2026-03-02T12:00: pending"
        )


class TestWhatCannotBeBuilt:
    def test_an_invalid_plan_id_is_rejected(self):
        with pytest.raises(InvalidOutboundMessagePlanIDError):
            build_message(plan_id=str(uuid4()))

    def test_a_bare_date_due_at_is_rejected(self):
        """The subclass trap, in the fourth class to have to name it.

        ``datetime`` passes ``isinstance(x, date)``, so a check written against
        the wider type would accept either. The narrower type is required by
        name here, and a plain date fails for that reason rather than by a
        special case.
        """
        assert isinstance(datetime(2026, 3, 2, 12, 0), date)  # the trap, still true

        with pytest.raises(InvalidOutboundMessageDueAtError):
            build_message(due_at=date(2026, 3, 2))

    def test_a_non_datetime_created_at_is_rejected(self):
        with pytest.raises(InvalidOutboundMessageCreatedAtError):
            build_message(created_at="2026-03-02T11:30:00")

    @pytest.mark.parametrize("field", ["recipient", "subject", "body"])
    def test_an_empty_text_field_is_rejected(self, field):
        """Empty is rejected, and so is whitespace-only.

        A message with no recipient fails at *send* time, which is the worst
        possible moment: by then the notice is claimed, so the warning cannot be
        raised again, and there is nothing left to deliver. Refusing it at
        construction moves the failure to the moment the mistake was made.
        """
        with pytest.raises(
            {
                "recipient": InvalidOutboundMessageRecipientError,
                "subject": InvalidOutboundMessageSubjectError,
                "body": InvalidOutboundMessageBodyError,
            }[field]
        ):
            build_message(**{field: "   "})

    def test_a_non_string_recipient_is_rejected(self):
        with pytest.raises(InvalidOutboundMessageRecipientError):
            build_message(recipient=None)

    def test_an_unknown_status_is_rejected(self):
        with pytest.raises(InvalidOutboundMessageStatusError):
            build_message(status="sent")

    def test_a_boolean_attempt_count_is_rejected(self):
        """``bool`` subclasses ``int``, so this has to be checked by name.

        Without the explicit clause, ``attempts=True`` would be a perfectly
        legitimate one-attempt count and nothing would ever complain.
        """
        assert isinstance(True, int)  # the trap, still true

        with pytest.raises(InvalidOutboundMessageAttemptsError):
            build_message(attempts=True)

    def test_a_negative_attempt_count_is_rejected(self):
        with pytest.raises(InvalidOutboundMessageAttemptsError):
            build_message(attempts=-1)

    def test_a_non_string_last_error_is_rejected(self):
        with pytest.raises(InvalidOutboundMessageLastErrorError):
            build_message(last_error=OSError("refused"))

    def test_a_non_datetime_settled_at_is_rejected(self):
        with pytest.raises(InvalidOutboundMessageSettledAtError):
            build_message(settled_at="2026-03-02T12:00:00")


class TestSettledAndUnsettledMustAgree:
    """The same shape of rule as a blocked run needing a reason.

    A status that claims the message is finished, and a moment that says when -
    or their absence - have to tell the same story. A half-written pair is a row
    that cannot answer the question it exists to answer.
    """

    def test_a_sent_message_with_no_moment_is_rejected(self):
        with pytest.raises(InvalidOutboundMessageSettledAtError):
            build_message(
                status=DeliveryStatus.SENT,
                settled_at=None,
            )

    def test_an_expired_message_with_no_moment_is_rejected(self):
        with pytest.raises(InvalidOutboundMessageSettledAtError):
            build_message(
                status=DeliveryStatus.EXPIRED,
                settled_at=None,
            )

    def test_a_pending_message_carrying_a_settled_moment_is_rejected(self):
        with pytest.raises(InvalidOutboundMessageSettledAtError):
            build_message(status=DeliveryStatus.PENDING, settled_at=NOON)

    def test_a_sent_message_with_its_moment_is_accepted(self):
        message = build_message(status=DeliveryStatus.SENT, settled_at=NOON)

        assert message.is_settled is True


class TestItsLifecycle:
    def test_marking_it_sent_records_the_moment(self):
        message = build_message()

        message.mark_sent(NOON)

        assert message.status is DeliveryStatus.SENT
        assert message.settled_at == NOON
        assert message.is_settled is True

    def test_marking_it_expired_records_the_moment_and_no_error(self):
        """Expiry is a decision, not a failure, so ``last_error`` stays empty."""
        message = build_message()

        message.mark_expired(NOON)

        assert message.status is DeliveryStatus.EXPIRED
        assert message.settled_at == NOON
        assert message.last_error is None

    def test_a_failure_leaves_it_pending_and_counted(self):
        """The whole reason there is no FAILED status.

        A delivery attempt that dies does not settle the message - it stays
        owed, and the next tick tries again. Making failure terminal would mean
        a mail server that was down for five minutes loses the warning forever.
        """
        message = build_message()

        message.record_failure("OSError: connection refused")

        assert message.status is DeliveryStatus.PENDING
        assert message.attempts == 1
        assert message.last_error == "OSError: connection refused"
        assert message.settled_at is None

    def test_failing_twice_counts_twice_and_keeps_the_last_error(self):
        message = build_message()

        message.record_failure("first")
        message.record_failure("second")

        assert message.attempts == 2
        assert message.last_error == "second"

    def test_a_settled_message_cannot_be_settled_again(self):
        """History does not get edited.

        A second ``mark_sent`` means the caller has lost track of which messages
        it is holding, and letting it through would overwrite the moment the
        message really went.
        """
        message = build_message()
        message.mark_sent(NOON)

        with pytest.raises(MessageAlreadySettledError):
            message.mark_sent(datetime(2026, 3, 2, 12, 5))

    def test_an_expired_message_cannot_then_fail(self):
        message = build_message()
        message.mark_expired(NOON)

        with pytest.raises(MessageAlreadySettledError):
            message.record_failure("too late")

    def test_a_retry_after_a_failure_can_still_succeed(self):
        """The path the retry design exists for."""
        message = build_message()
        message.record_failure("connection refused")

        message.mark_sent(NOON)

        assert message.status is DeliveryStatus.SENT
        assert message.attempts == 1
        assert message.last_error == "connection refused"


class TestWhenItStopsBeingWorthSending:
    def test_before_its_occurrence_it_is_still_worth_sending(self):
        message = build_message(due_at=NOON)

        assert message.is_stale(HALF_PAST_ELEVEN) is False

    def test_at_its_occurrence_it_is_already_too_late(self):
        """Strict at the lower bound, exactly as the notice window is.

        Decision 20's comparison, in the place where it is finally acted on: at
        ``due_at`` the payout is happening, and a warning about a payout that is
        happening is no longer news. This is the assertion the drain's expiry
        branch rests on - if the bound went slack here, a warning would be sent
        for a payout already in flight.
        """
        message = build_message(due_at=NOON)

        assert message.is_stale(NOON) is True

    def test_after_its_occurrence_it_is_stale(self):
        message = build_message(due_at=HALF_PAST_ELEVEN)

        assert message.is_stale(NOON) is True

    def test_it_is_about_the_moment_asked_and_not_the_moment_composed(self):
        """``created_at`` plays no part in the answer.

        A message composed long before its occurrence is not stale merely for
        being old, and one composed moments before is not spared for being
        fresh. The question is entirely about ``due_at`` against ``as_of`` -
        which is what lets the drain ask it of a message it has just read,
        without knowing when that message was written.
        """
        composed_early = build_message(due_at=NOON, created_at=HALF_PAST_ELEVEN)
        composed_late = build_message(due_at=HALF_PAST_ELEVEN, created_at=NOON)

        assert composed_early.is_stale(HALF_PAST_ELEVEN) is False
        assert composed_late.is_stale(NOON) is True

    def test_a_settled_message_answers_the_same_question_the_same_way(self):
        """Staleness is a fact about the clock, not about delivery state.

        The answer must not depend on status. If it did, "already sent" would be
        indistinguishable from "too late", and a report would describe a message
        that went out as one that expired.
        """
        message = build_message(due_at=NOON)
        message.mark_sent(HALF_PAST_ELEVEN)

        assert message.is_stale(HALF_PAST_ELEVEN) is False
        assert message.is_stale(NOON) is True


class TestTheStatusEnum:
    def test_only_pending_is_unsettled(self):
        assert DeliveryStatus.PENDING.is_settled is False
        assert DeliveryStatus.SENT.is_settled is True
        assert DeliveryStatus.EXPIRED.is_settled is True

    def test_there_is_no_failed_member(self):
        """Named as a test because its absence is a decision, not an oversight.

        A failure is recorded on a message that is still PENDING; making it a
        status would make it terminal, and a terminal failure is a lost warning.
        """
        assert [member.name for member in DeliveryStatus] == [
            "PENDING",
            "SENT",
            "EXPIRED",
        ]
