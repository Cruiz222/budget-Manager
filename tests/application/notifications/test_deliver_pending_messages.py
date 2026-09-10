import sqlite3
from datetime import datetime
from uuid import uuid4

from app.application.notifications.deliver_pending_messages import (
    DeliverPendingMessages,
    DeliveryReport,
)
from app.domain.notifications.outboundMessage import OutboundMessage
from app.infrastructure.persistence.sqlite_unit_of_work import SqliteUnitOfWorkFactory

NOON = datetime(2026, 3, 2, 12, 0)
HALF_PAST_ELEVEN = datetime(2026, 3, 2, 11, 30)
QUARTER_TO_TWELVE = datetime(2026, 3, 2, 11, 45)


def build_deliverer(tmp_path, channel, name="outbox.db"):
    factory = SqliteUnitOfWorkFactory(str(tmp_path / name))
    return DeliverPendingMessages(factory, channel=channel), factory, str(
        tmp_path / name
    )


def seed(factory, wallet, plan, messages=()):
    """Store the wallet and plan, and queue any messages, in one unit."""
    uow = factory.start()
    try:
        uow.wallets.save(wallet)
        uow.plans.save(plan)
        for message in messages:
            uow.outbound_messages.enqueue(message)
        uow.commit()
    except BaseException:
        uow.rollback()
        raise


def build_message(plan_id, **overrides):
    kwargs = dict(
        plan_id=plan_id,
        due_at=NOON,
        recipient="chinedu@example.com",
        subject="Payout of 2000.00 NGN in 30 minutes",
        body="The plan 'salary' pays 2000.00 NGN at 2026-03-02T12:00.",
        created_at=HALF_PAST_ELEVEN,
    )
    kwargs.update(overrides)
    return OutboundMessage(**kwargs)


def read_rows(db_path):
    """Every outbox row, straight from the table.

    Read with a plain connection rather than through the repository, because the
    assertions below are about what is *stored* - including the columns
    ``pending()`` deliberately filters out. This is also the diagnostic query
    worth reaching for when a message does not arrive:

        SELECT plan_id, due_at, status, attempts, last_error FROM outbound_messages
    """
    connection = sqlite3.connect(db_path)
    try:
        connection.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in connection.execute(
                "SELECT plan_id, due_at, status, attempts, last_error, settled_at "
                "FROM outbound_messages ORDER BY due_at"
            )
        ]
    finally:
        connection.close()


def only_row(db_path):
    rows = read_rows(db_path)
    assert len(rows) == 1
    return rows[0]


class TestSendingWhatIsOwed:
    def test_a_queued_message_is_sent_and_settled(
        self, tmp_path, build_wallet, build_plan, build_channel
    ):
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        channel = build_channel()
        deliverer, factory, db = build_deliverer(tmp_path, channel)
        seed(factory, wallet, plan, [build_message(plan.plan_id)])

        report = deliverer.execute(HALF_PAST_ELEVEN)

        assert [message.plan_id for message in report.sent] == [plan.plan_id]
        assert [message.plan_id for message in channel.sent] == [plan.plan_id]
        assert only_row(db)["status"] == "SENT"

    def test_the_words_sent_are_the_words_that_were_queued(
        self, tmp_path, build_wallet, build_plan, build_channel
    ):
        """Composition happens at enqueue, so the channel is handed the stored text.

        Nothing is re-rendered on the way out: a wording change to the notifier
        must not rewrite a message that was queued under the old wording.
        """
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        channel = build_channel()
        deliverer, factory, _ = build_deliverer(tmp_path, channel)
        seed(
            factory,
            wallet,
            plan,
            [build_message(plan.plan_id, subject="frozen subject", body="frozen body")],
        )

        deliverer.execute(HALF_PAST_ELEVEN)

        assert channel.sent[0].subject == "frozen subject"
        assert channel.sent[0].body == "frozen body"
        assert channel.sent[0].recipient == "chinedu@example.com"

    def test_an_empty_queue_is_quiet(
        self, tmp_path, build_wallet, build_plan, build_channel
    ):
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        channel = build_channel()
        deliverer, factory, _ = build_deliverer(tmp_path, channel)
        seed(factory, wallet, plan)

        report = deliverer.execute(HALF_PAST_ELEVEN)

        assert report.is_quiet is True
        assert channel.attempts == []

    def test_a_settled_message_is_not_sent_again(
        self, tmp_path, build_wallet, build_plan, build_channel
    ):
        """The property that stops a warning being emailed on every tick forever."""
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        channel = build_channel()
        deliverer, factory, _ = build_deliverer(tmp_path, channel)
        seed(factory, wallet, plan, [build_message(plan.plan_id)])

        deliverer.execute(HALF_PAST_ELEVEN)
        second = deliverer.execute(QUARTER_TO_TWELVE)

        assert len(channel.sent) == 1
        assert second.sent == ()


class TestAStaleWarningIsNeverSent:
    """The rule that makes retrying safe, and the reason no retry cap is needed.

    A message whose occurrence has already passed is expired rather than
    delivered. Telling someone a payout is thirty minutes away after it already
    happened is worse than silence - it is actively wrong.
    """

    def test_a_message_whose_occurrence_has_passed_is_expired(
        self, tmp_path, build_wallet, build_plan, build_channel
    ):
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        channel = build_channel()
        deliverer, factory, db = build_deliverer(tmp_path, channel)
        seed(factory, wallet, plan, [build_message(plan.plan_id)])

        report = deliverer.execute(datetime(2026, 3, 2, 12, 5))

        assert [message.plan_id for message in report.expired] == [plan.plan_id]
        assert channel.attempts == []  # never even tried
        row = only_row(db)
        assert row["status"] == "EXPIRED"
        assert row["settled_at"] == datetime(2026, 3, 2, 12, 5).isoformat()

    def test_at_the_occurrence_itself_the_warning_is_too_late(
        self, tmp_path, build_wallet, build_plan, build_channel
    ):
        """The lower bound is strict, exactly as it is in the notifier.

        At ``as_of == due_at`` the payout is happening. A warning about it is no
        longer news, and the comparison must not be "not yet past" - which would
        email the user about a payment that is being made as they read it.
        """
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        channel = build_channel()
        deliverer, factory, _ = build_deliverer(tmp_path, channel)
        seed(factory, wallet, plan, [build_message(plan.plan_id)])

        report = deliverer.execute(NOON)

        assert len(report.expired) == 1
        assert channel.attempts == []

    def test_a_stale_message_is_expired_even_with_no_channel_configured(
        self, tmp_path, build_wallet, build_plan
    ):
        """Staleness is a fact about the clock, not about delivery.

        If expiry only ran when a mail server was reachable, an install with no
        email would hold stale warnings forever - and they would all be sent at
        once, hours late, on the day someone finally configured SMTP.
        """
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        deliverer, factory, db = build_deliverer(tmp_path, channel=None)
        seed(factory, wallet, plan, [build_message(plan.plan_id)])

        report = deliverer.execute(datetime(2026, 3, 2, 12, 5))

        assert len(report.expired) == 1
        assert only_row(db)["status"] == "EXPIRED"


class TestFailureIsRecordedNotRaised:
    """A mail server being down is a message that goes out next tick, not an incident.

    The property this buys is the one the whole feature rests on: **a broken
    SMTP server can never make ``plan tick`` fail.** The money still moved, and
    the warning is still owed.
    """

    def test_a_channel_that_raises_does_not_raise_from_execute(
        self, tmp_path, build_wallet, build_plan, build_channel
    ):
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        channel = build_channel(failures=[OSError("connection refused")])
        deliverer, factory, db = build_deliverer(tmp_path, channel)
        seed(factory, wallet, plan, [build_message(plan.plan_id)])

        report = deliverer.execute(HALF_PAST_ELEVEN)  # must not raise

        assert [message.plan_id for message in report.failed] == [plan.plan_id]
        row = only_row(db)
        assert row["status"] == "PENDING"
        assert row["attempts"] == 1
        assert "connection refused" in row["last_error"]
        assert row["settled_at"] is None

    def test_the_error_names_the_exception_type(self, tmp_path, build_wallet, build_plan, build_channel):
        """So the log line says what actually went wrong, not just that something did."""
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        channel = build_channel(failures=[OSError("connection refused")])
        deliverer, factory, db = build_deliverer(tmp_path, channel)
        seed(factory, wallet, plan, [build_message(plan.plan_id)])

        deliverer.execute(HALF_PAST_ELEVEN)

        assert only_row(db)["last_error"] == "OSError: connection refused"

    def test_a_later_tick_retries_and_succeeds(
        self, tmp_path, build_wallet, build_plan, build_channel
    ):
        """The retry the design exists for, and the reason there is no retry cap."""
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        channel = build_channel(failures=[OSError("connection refused")])
        deliverer, factory, db = build_deliverer(tmp_path, channel)
        seed(factory, wallet, plan, [build_message(plan.plan_id)])

        first = deliverer.execute(HALF_PAST_ELEVEN)
        second = deliverer.execute(QUARTER_TO_TWELVE)

        assert len(first.failed) == 1
        assert len(second.sent) == 1
        row = only_row(db)
        assert row["status"] == "SENT"
        assert row["attempts"] == 1  # the count survives the success

    def test_the_stored_error_survives_a_later_success(
        self, tmp_path, build_wallet, build_plan, build_channel
    ):
        """A record of the trouble, kept after it stopped.

        The row is not just a queue position; it is the history of what this
        warning went through, and clearing the error on success would erase the
        only trace that the mail server was briefly down.
        """
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        channel = build_channel(failures=[OSError("connection refused")])
        deliverer, factory, db = build_deliverer(tmp_path, channel)
        seed(factory, wallet, plan, [build_message(plan.plan_id)])

        deliverer.execute(HALF_PAST_ELEVEN)
        deliverer.execute(QUARTER_TO_TWELVE)

        assert only_row(db)["last_error"] == "OSError: connection refused"

    def test_one_bad_message_does_not_stop_the_next(
        self, tmp_path, build_wallet, build_plan, build_channel
    ):
        """The transaction boundary follows the aggregate, not the batch.

        A tick that hits an error on the first message must not abandon the
        second - the two are separate facts, and the second one is deliverable.
        """
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        channel = build_channel(failures=[OSError("first one refused")])
        deliverer, factory, db = build_deliverer(tmp_path, channel)
        seed(
            factory,
            wallet,
            plan,
            [
                build_message(plan.plan_id, due_at=datetime(2026, 3, 2, 12, 0)),
                build_message(plan.plan_id, due_at=datetime(2026, 3, 2, 13, 0)),
            ],
        )

        report = deliverer.execute(HALF_PAST_ELEVEN)

        assert len(report.failed) == 1
        assert len(report.sent) == 1
        statuses = {row["due_at"]: row["status"] for row in read_rows(db)}
        assert statuses[datetime(2026, 3, 2, 12, 0).isoformat()] == "PENDING"
        assert statuses[datetime(2026, 3, 2, 13, 0).isoformat()] == "SENT"


class TestWithNoChannelConfigured:
    """An install with no mail account is normal, not broken.

    ``None`` is not a channel that fails - it is the *absence* of one, and the
    use case can see the difference, which is why it is modelled this way rather
    than as an SMTP adapter pointing at nothing.
    """

    def test_nothing_is_sent_and_nothing_is_lost(
        self, tmp_path, build_wallet, build_plan
    ):
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        deliverer, factory, db = build_deliverer(tmp_path, channel=None)
        seed(factory, wallet, plan, [build_message(plan.plan_id)])

        report = deliverer.execute(HALF_PAST_ELEVEN)

        assert report.sent == ()
        assert report.failed == ()  # not a failure: nothing was attempted
        assert len(report.deferred) == 1
        assert only_row(db)["status"] == "PENDING"
        assert only_row(db)["attempts"] == 0

    def test_it_is_not_reported_as_an_error(
        self, tmp_path, build_wallet, build_plan
    ):
        """Deferred is not failed, and the distinction is what keeps the log honest."""
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        deliverer, factory, _ = build_deliverer(tmp_path, channel=None)
        seed(factory, wallet, plan, [build_message(plan.plan_id)])

        report = deliverer.execute(HALF_PAST_ELEVEN)

        assert report.failed == ()
        assert report.is_quiet is False  # there *is* something to say

    def test_a_message_queued_before_the_channel_vanished_is_still_owed(
        self, tmp_path, build_wallet, build_plan, build_channel
    ):
        """Configured once, then unconfigured: the message waits, it does not vanish."""
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        channel = build_channel(failures=[OSError("mail server down")])
        deliverer, factory, db = build_deliverer(tmp_path, channel)
        seed(factory, wallet, plan, [build_message(plan.plan_id)])
        deliverer.execute(HALF_PAST_ELEVEN)

        # The same database, now with no channel at all.
        unconfigured = DeliverPendingMessages(factory, channel=None)
        report = unconfigured.execute(QUARTER_TO_TWELVE)

        assert len(report.deferred) == 1
        assert only_row(db)["status"] == "PENDING"


class TestTheReport:
    def test_a_quiet_report_is_the_empty_one(self):
        assert DeliveryReport().is_quiet is True

    def test_a_report_with_an_outcome_is_not_quiet(self):
        sent = build_message(uuid4())

        assert DeliveryReport(sent=(sent,)).is_quiet is False
        assert DeliveryReport(expired=(sent,)).is_quiet is False
        assert DeliveryReport(failed=(sent,)).is_quiet is False
        assert DeliveryReport(deferred=(sent,)).is_quiet is False


def test_the_use_case_never_loads_a_wallet(tmp_path, build_wallet, build_plan, build_channel):
    """The structural guarantee decision 19 rests on, checked where it could creep in.

    The notifier's version of this is that it holds no balance to be conditional
    on. Delivery's is the same and one step further out: nothing about a message
    may depend on a wallet, or a courtesy could start deciding whether a payout
    is worth announcing.
    """
    wallet = build_wallet(available="0", locked="0")
    plan = build_plan(wallet_id=wallet.wallet_id)
    channel = build_channel()
    deliverer, factory, db = build_deliverer(tmp_path, channel)
    seed(factory, wallet, plan, [build_message(plan.plan_id)])

    # An empty wallet, and the message goes out anyway.
    report = deliverer.execute(HALF_PAST_ELEVEN)

    assert len(report.sent) == 1
    assert only_row(db)["status"] == "SENT"
